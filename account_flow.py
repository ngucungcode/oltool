"""Account flow automation: auto-login, homework discovery, and end-to-end pipeline.

Allows multisession to run 100% autonomously:
  1. Auto-login using username/password if session is missing or expired
  2. Auto-discovery of active or waiting homework assignments from dashboard
  3. Seamless full pipeline: login -> discover -> scan -> solve -> apply -> submit
"""
import asyncio
import json
import logging
import random
import re
from pathlib import Path
from urllib.parse import urlsplit

import homework
import homework_async
import onluyen

log = logging.getLogger('onluyen.flow')


class NoAssignmentAvailable(RuntimeError):
    """No active or waiting homework found on the student dashboard."""
    pass


async def ensure_authenticated(page, account):
    """Ensure the browser profile has an active Onluyen session for account['username'].

    If session is already valid for the target username, returns immediately.
    If session belongs to a different user, clears session and re-authenticates.
    If redirected to login or password input is present, performs automated login.
    """
    target_user = (account.get('username') or '').strip().lower()

    await page.goto('https://app.onluyen.vn/home-student', wait_until='domcontentloaded')
    await page.wait_for_timeout(1500)

    # Check if we landed on login page or home-student
    is_login = await page.locator('input[type=password], input[placeholder*="Tên đăng nhập"]').count()
    current_url = page.url.lower()

    if not is_login and '/home-student' in current_url:
        # Check token in localStorage to confirm active session
        token = await page.evaluate('localStorage.getItem("token") || localStorage.getItem("auth")')
        if token:
            current_user = await page.evaluate(r'''() => {
                try {
                    const raw = localStorage.getItem('user_traits');
                    if (raw) {
                        const decoded = decodeURIComponent(raw);
                        const match = decoded.match(/"userName"\s*:\s*"([^"]+)"/i) || decoded.match(/"email"\s*:\s*"([^"]+)"/i);
                        if (match) return decodeURIComponent(match[1]).toLowerCase();
                    }
                } catch {}
                const text = document.body ? document.body.innerText : '';
                const emailMatch = text.match(/[\w.-]+@[\w.-]+\.\w+/);
                return emailMatch ? emailMatch[0].toLowerCase() : '';
            }''')
            if target_user and current_user and current_user == target_user:
                return True
            if target_user and current_user and current_user != target_user:
                # Session belongs to another user: clear session to switch accounts
                await page.evaluate('try { localStorage.clear(); sessionStorage.clear(); } catch {}')
                await page.context.clear_cookies()
                is_login = 1
            elif not target_user:
                return True

    # Needs login
    username = account.get('username')
    password = account.get('password')
    if not username or not password:
        raise onluyen.LoginRequired()

    # Navigate to login page if not already there
    if '/login' not in current_url and not is_login:
        await page.goto('https://app.onluyen.vn/login', wait_until='domcontentloaded')
        await page.wait_for_timeout(1000)

    # Find username input (exclude readonly combobox)
    user_input = page.locator('input:not([readonly])[placeholder*="Tên đăng nhập"], input:not([readonly])[type=text]').first
    await user_input.wait_for(state='visible', timeout=8000)
    await user_input.fill('')
    await user_input.press_sequentially(username, delay=random.randint(45, 95))

    await asyncio.sleep(random.uniform(0.2, 0.5))

    # Find password input
    pass_input = page.locator('input[type=password]').first
    await pass_input.wait_for(state='visible', timeout=5000)
    await pass_input.fill('')
    await pass_input.press_sequentially(password, delay=random.randint(45, 95))

    # Remember me checkbox
    remember = page.locator('input[type=checkbox][name="remember"], input#remember')
    if await remember.count() and not await remember.first.is_checked():
        try:
            await remember.first.check()
        except Exception:
            pass

    await asyncio.sleep(random.uniform(0.3, 0.8))

    # Click login button
    login_btn = page.locator('button:has-text("Đăng nhập"), input[type=submit][value*="Đăng nhập"]').first
    await login_btn.click()

    # Wait for either navigation away from login OR error message
    res = await page.wait_for_function(
        '''() => {
            const url = window.location.href;
            if (!url.includes("/login") && (url.includes("/home") || !!localStorage.getItem("token"))) {
                return "success";
            }
            const danger = [...document.querySelectorAll('div, span, p, .text-danger, .error, .alert')].find(
                e => /(mật khẩu không chính xác|tài khoản không tồn tại|bị khóa|sai thông tin)/i.test(e.innerText || '')
            );
            if (danger) return "error:" + danger.innerText.trim();
            return false;
        }''',
        timeout=15000
    )
    result_val = await res.json_value()
    if isinstance(result_val, str) and result_val.startswith('error:'):
        err_msg = result_val.split('error:', 1)[1].strip()
        raise ValueError(f'Login rejected for {account["name"]}: {err_msg}')
    if result_val != 'success':
        raise ValueError(f'Login timed out for {account["name"]}')

    await asyncio.sleep(random.uniform(0.5, 1.2))
    return True


async def discover_and_open_homework(page, target_name=None):
    """Find and enter an active or waiting homework assignment from student dashboard.

    Returns the active step URL: https://app.onluyen.vn/school/test/step/ID
    """
    await page.goto('https://app.onluyen.vn/home-student', wait_until='networkidle')
    await page.wait_for_timeout(1500)

    # Look for assignment cards in home-student
    cards_locator = page.locator('.class-as, app-list-assignment .class-as')
    count = await cards_locator.count()

    candidate = None
    candidate_name = ''
    candidate_status = ''

    for i in range(count):
        card = cards_locator.nth(i)
        name_el = card.locator('.name-as, .assignment-name, h4, h5').first
        status_el = card.locator('.status, .status-as, .badge').first
        if not await name_el.count():
            continue
        c_name = (await name_el.inner_text()).strip()
        c_status = (await status_el.inner_text()).strip() if await status_el.count() else ''

        # If specific test requested, match name
        if target_name and target_name.lower() in c_name.lower():
            candidate = card
            candidate_name = c_name
            candidate_status = c_status
            break

        # Check if card is waiting or in-progress
        is_waiting = (
            c_status in ('Chờ làm bài', 'Đang làm', 'Chưa làm') or
            any(kw in c_status.lower() for kw in ('chờ làm', 'đang làm', 'chưa làm')) or
            await card.locator(':text-matches("^(Làm bài|Tiếp tục|Bắt đầu)$", "i")').count() > 0
        )
        if not target_name and is_waiting:
            candidate = card
            candidate_name = c_name
            candidate_status = c_status
            break

    if not candidate:
        raise NoAssignmentAvailable('Hiện tại không tìm thấy bài tập mới nào đang chờ làm hoặc đang làm trên trang chủ Onluyen.')

    # Click the assignment card to open its detail page
    await candidate.click()
    await page.wait_for_timeout(2000)

    # Now we might be at /school/test/ID or already at /school/test/step/ID
    current = page.url
    if '/school/test/step/' in current:
        return current

    # If at landing /school/test/ID, click start/continue button
    start_btn = page.locator(
        'button:has-text("Làm bài"), button:has-text("Bắt đầu"), button:has-text("Tiếp tục"), '
        'a:has-text("Làm bài"), a:has-text("Bắt đầu"), a:has-text("Tiếp tục"), '
        '.btn-test:has-text("Bắt đầu"), .btn-test:has-text("Làm bài"), .btn-test:has-text("Tiếp tục"), '
        ':text-matches("^\\s*(Bắt đầu|Làm bài|Tiếp tục)\\s*$", "i")'
    ).filter(visible=True)

    if await start_btn.count():
        await start_btn.first.click()
        await page.wait_for_url(re.compile(r'/school/test/step/[A-Za-z0-9]+'), timeout=15000)
        return page.url

    # Check if already step URL
    if '/school/test/step/' in page.url:
        return page.url

    raise ValueError(f'Could not transition from test overview to step URL: {page.url}')
