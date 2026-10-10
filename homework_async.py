"""Async homework runner: scan / solve / apply / submit against /school/test/step/ID.

Same guarantees as homework.py (fingerprint, verified_saved, submission_pending, no retry on
ambiguous submit) but written against Playwright's async API so a single asyncio loop can drive
many pages at once. Synchronous `homework.py` is left untouched for the single-account CLI.
"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import onluyen
import difficulty
import human_pacing
import score_planner


NAV = 'app-sidebar-school-test .answer-sheet .option'
ROOT = 'app-test-school-step-data-regular'


async def emit_progress(cb, event_type, data):
    """Safely invoke progress callback (sync or async)."""
    if not cb:
        return
    try:
        res = cb(event_type, data)
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass


def check_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'app.onluyen.vn' or not re.fullmatch(r'/school/test/step/[A-Za-z0-9]+', parts.path) or parts.query or parts.fragment:
        raise ValueError('Require an existing homework /school/test/step/ID URL')


def write_draft(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def one_visible(page, selector):
    locator = page.locator(selector).filter(visible=True)
    count = await locator.count()
    if count != 1:
        raise ValueError('Selector must match exactly one visible element: ' + selector)
    return locator


async def require_unblocked(page):
    challenge = page.locator(
        'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
        'iframe[src*="challenges.cloudflare.com"], input[autocomplete="one-time-code"]'
    ).filter(visible=True)
    if getattr(page, '_onluyen_forbidden', False) or await challenge.count():
        raise ValueError('Access blocked or verification required; resolve manually')


async def discover_question(page):
    result = await page.evaluate(r'''() => {
        const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
        const all = [...document.querySelectorAll('body *')];
        const widgets = all.filter(e => e.matches('.question-option, input[type=radio], input.can-resize-second, input.input-size-1, input.can-resize, input[id^="mathplay-answer"]') &&
            (visible(e) || visible(e.parentElement)));
        const ids = document.body.innerText.match(/Câu:\s*\d+\s*#\d+/g) || [];
        if (ids.length !== 1 || !widgets.length) return false;
        let candidates = all.filter(e => visible(e) && !['BODY','HTML'].includes(e.tagName) &&
            ((e.innerText || '').match(/Câu:\s*\d+\s*#\d+/g) || []).length === 1 &&
            widgets.every(w => e.contains(w)) && !e.querySelector('header, nav, aside, input[type=password]'));
        candidates = candidates.filter(e => !candidates.some(other => other !== e && e.contains(other)));
        if (candidates.length !== 1) return false;
        all.forEach(e => e.removeAttribute('data-onluyen-question'));
        candidates[0].setAttribute('data-onluyen-question', '');
        return true;
    }''')
    if not result:
        raise ValueError('Cannot uniquely discover question')
    return await one_visible(page, '[data-onluyen-question]')


async def numbers(page):
    labels = [x.strip() for x in await page.locator(NAV).filter(visible=True).all_text_contents()]
    if not labels or labels != [str(i) for i in range(1, len(labels) + 1)] or len(labels) > 200:
        raise ValueError('Ambiguous homework navigation')
    return list(range(1, len(labels) + 1))


async def remaining(page):
    loc = page.locator('app-test-school-step-header .pause-timer, .pause-timer, .timer-value').filter(visible=True)
    try:
        await loc.first.wait_for(state='visible', timeout=3000)
    except Exception:
        pass
    count = await loc.count()
    if count == 0:
        return 86400
    text = (await loc.first.inner_text()).strip()
    if re.fullmatch(r'\d{1,2}:\d{2}:\d{2}', text):
        h, m, s = map(int, text.split(':'))
        if m < 60 and s < 60:
            return h * 3600 + m * 60 + s
    if re.fullmatch(r'\d{1,2}:\d{2}', text):
        m, s = map(int, text.split(':'))
        if s < 60:
            return m * 60 + s
    return 86400


async def extract(page, selector=None):
    await require_unblocked(page)
    if selector:
        root = await one_visible(page, selector)
    else:
        root = await discover_question(page)
    if await root.evaluate('(e)=>["BODY","HTML"].includes(e.tagName) || !!e.querySelector("input[type=password]")'):
        raise ValueError('Question selector must exclude page chrome and login')
    text = await root.inner_text()
    ids = re.findall(r'#(\d+)', text) or re.findall(r'Câu:\s*(\d+)', text)
    if not ids:
        raise ValueError('Question scope must contain a question ID or number')

    # Wait for answer widget to be attached and rendered by Angular
    kinds = 0
    options = []
    radios = None
    numeric = None
    radio_count = 0
    numeric_count = 0
    for _ in range(30):
        options = [(s or '').strip() for s in await root.locator('.question-option-label').all_text_contents()]
        radios = root.locator('input[type=radio]')
        numeric = root.locator('input.can-resize-second, input.input-size-1, input.can-resize, input[id^="mathplay-answer"]')
        radio_count = await radios.count()
        numeric_count = await numeric.count()
        kinds = bool(options) + bool(radio_count) + bool(numeric_count)
        if kinds == 1:
            break
        await asyncio.sleep(0.1)

    if kinds != 1:
        raise ValueError('Unknown or ambiguous answer widget')
    kind, items = 'single', 0
    if options and (len(set(options)) != len(options) or not all(re.fullmatch('[A-D]', x) for x in options)):
        raise ValueError('Invalid option labels')
    if radio_count:
        values = await radios.evaluate_all('(els)=>els.map(e=>e.value)')
        if len(values) % 2 or any(values[i:i+2] != ['true', 'false'] for i in range(0, len(values), 2)):
            raise ValueError('Unsupported true/false ordering')
        kind, items = 'truefalse', len(values) // 2
    elif numeric_count:
        if numeric_count != 1:
            raise ValueError('Ambiguous numeric input')
        kind = 'numeric'
    mathml = '\n'.join(await root.locator('mjx-assistive-mml').evaluate_all('(els)=>els.map(e=>e.innerHTML)'))
    image = 'data:image/png;base64,' + base64.b64encode(await root.screenshot(timeout=10000)).decode()
    return {'id': ids[0], 'number': int(re.search(r'Câu:\s*(\d+)', text)[1]), 'kind': kind,
            'text': text, 'mathml': mathml, 'options': options, 'items': items, 'image': image}, root


async def visit(page, number, selector=None):
    await require_unblocked(page)
    item = page.locator(NAV).filter(visible=True).filter(has_text=re.compile(r'^\s*' + str(number) + r'\s*$'))
    if await item.count() != 1:
        raise ValueError('Ambiguous homework question')
    await item.click()
    await page.wait_for_function(
        r'''n => {
            const text = (document.querySelector('app-test-school-step-data-regular')?.innerText) || '';
            const m = text.match(/Câu:\s*(\d+)/);
            return m && parseInt(m[1], 10) === n;
        }''',
        arg=number,
        timeout=15000
    )
    try:
        await page.wait_for_function(
            '''() => [...document.querySelectorAll('app-test-school-step-data-regular img')]
                        .every(i => i.complete && i.naturalWidth > 0)''',
            timeout=5000
        )
    except Exception:
        pass
    # Ensure question container and answer widget are attached before extracting
    try:
        await page.wait_for_function(
            '''() => {
                const root = document.querySelector('app-test-school-step-data-regular .question-container');
                if (!root) return false;
                const hasOptions = root.querySelectorAll('.question-option-label').length > 0;
                const hasRadios = root.querySelectorAll('input[type=radio]').length > 0;
                const hasNumeric = root.querySelectorAll('input.can-resize-second, input.input-size-1, input.can-resize, input[id^="mathplay-answer"]').length > 0;
                return hasOptions || hasRadios || hasNumeric;
            }''',
            timeout=10000
        )
    except Exception:
        pass
    q, root = await extract(page, ROOT + ' .question-container')
    if q['number'] != number:
        raise ValueError('Homework question changed')
    q['body'] = await root.locator('.question-name').all_text_contents()
    q['choice_text'] = await root.locator('.question-option-content').all_text_contents()
    sources = await root.locator('img').evaluate_all('(els)=>els.map(e=>e.currentSrc || e.src)')
    q['image_hashes'] = [hashlib.sha256(src.encode()).hexdigest() for src in sources]
    return q, root


def fingerprint(question):
    fields = ('id', 'number', 'kind', 'mathml', 'options', 'items', 'body', 'choice_text', 'image_hashes')
    return hashlib.sha256(json.dumps({k: question[k] for k in fields if k in question},
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def selected(root, question, answer):
    if question['kind'] == 'single':
        marked = root.locator('.question-option').filter(has_text='Đáp án của bạn')
        if await marked.count():
            labels = await marked.locator('.question-option-label').all_text_contents()
        else:
            labels = await root.locator(
                '.question-option.highlighed .question-option-label, '
                '.question-option.selected .question-option-label').all_text_contents()
        return [(x or '').strip() for x in labels] == [answer]
    if question['kind'] == 'numeric':
        val = (await root.locator('input.can-resize-second, input.input-size-1, input.can-resize, input[id^="mathplay-answer"]').input_value()).strip()
        return val == str(answer).strip()
    radios = root.locator('input[type=radio]')
    for i, v in enumerate(answer):
        on = radios.nth(i * 2 + (0 if v else 1))
        off = radios.nth(i * 2 + (1 if v else 0))
        if not (await on.is_checked()) or await off.is_checked():
            return False
    return True


async def choose(page, root, question, answer):
    await require_unblocked(page)
    if onluyen.validate({'kind': question['kind'], 'answer': answer, 'certain': True}, question) is None:
        raise ValueError('Invalid homework answer')
    if await selected(root, question, answer):
        return
    if question['kind'] == 'single':
        await asyncio.sleep(random.uniform(0.15, 0.6))  # human "reading" pause
        option = root.locator('.question-option').filter(
            has=page.locator('.question-option-label', has_text=re.compile(r'^\s*' + re.escape(answer) + r'\s*$')))
        if await option.count() != 1:
            raise ValueError('Ambiguous homework option')
        await option.click()
    elif question['kind'] == 'numeric':
        entry = await one_visible(root, 'input.can-resize-second, input.input-size-1, input.can-resize, input[id^="mathplay-answer"]')
        if not await entry.is_editable():
            raise ValueError('Homework numeric answer is read-only')
        await asyncio.sleep(random.uniform(0.1, 0.4))
        await entry.fill('')
        # Randomized per-keystroke delay: 55-130ms, with occasional longer pauses
        await entry.press_sequentially(str(answer), delay=random.randint(55, 130))
        await asyncio.sleep(random.uniform(0.05, 0.2))
        await entry.press('Tab')
    else:
        radios = root.locator('input[type=radio]')
        for i, value in enumerate(answer):
            if i > 0:
                await asyncio.sleep(random.uniform(0.2, 0.7))  # pause between items
            radio = radios.nth(i * 2 + (0 if value else 1))
            ident = await radio.get_attribute('id')
            matches = [x for x in await root.locator('label').all() if ident and await x.get_attribute('for') == ident]
            if len(matches) != 1:
                raise ValueError('Ambiguous homework radio label')
            await matches[0].click()
    if not await selected(root, question, answer):
        raise ValueError('Homework selection readback failed')


def check_question(current, saved):
    if current.get('id') != saved.get('id') or current.get('number') != saved.get('number') or current.get('kind') != saved.get('kind'):
        raise ValueError('Homework content changed since scan')
    if current.get('kind') == 'numeric':
        def norm(d):
            c = {k: v for k, v in d.items() if k in saved}
            if 'body' in c and isinstance(c['body'], list):
                c['body'] = [re.sub(r'Đáp án:.*', 'Đáp án:', s) for s in c['body']]
            return fingerprint(c)
        if norm(current) != norm(saved):
            raise ValueError('Homework content changed since scan')
        return
    comparable = {k: v for k, v in current.items() if k in saved}
    if fingerprint(comparable) != saved['fingerprint']:
        raise ValueError('Homework content changed since scan')


async def scan(page, path, url, progress_cb=None):
    ns = await numbers(page)
    if Path(path).exists():
        try:
            draft = json.loads(Path(path).read_text())
        except Exception:
            draft = None
        if draft and draft.get('url') == url and draft.get('phase') in ('scanned', 'solving', 'solved', 'applying', 'verified', 'submission_pending', 'submitted'):
            await emit_progress(progress_cb, 'scan_complete', {'total': len(draft.get('questions', ns))})
            return draft
        if not draft or draft.get('url') != url or draft.get('phase') != 'scanning' or [q['number'] for q in draft.get('questions', [])] != ns[:len(draft.get('questions', []))]:
            draft = {'version': 1, 'url': url, 'phase': 'scanning', 'questions': []}
    else:
        draft = {'version': 1, 'url': url, 'phase': 'scanning', 'questions': []}
    write_draft(path, draft)
    seen = {q['id'] for q in draft['questions']}
    await emit_progress(progress_cb, 'scan_start', {'total': len(ns)})
    for n in ns[len(draft['questions']):]:
        if await remaining(page) < 120:
            raise ValueError('Insufficient time to scan homework')
        # Human-like pause between question navigations
        if n > 1:
            await asyncio.sleep(random.uniform(0.3, 1.2))
        q, root = await visit(page, n)
        if q['id'] in seen:
            raise ValueError('Duplicate homework question ID')
        seen.add(q['id'])
        originals = await root.locator('img').evaluate_all(
            '(els)=>els.map(e=>e.currentSrc || e.src).filter(s=>s.startsWith("data:image/"))')
        if len(originals) > 16 or sum(len(x) for x in originals) > 16_000_000:
            raise ValueError('Oversized homework images')
        for image in originals:
            if not re.match(r'^data:image/(png|jpeg|gif|webp);base64,', image):
                raise ValueError('Unsupported homework image')
            base64.b64decode(image.split(',', 1)[1], validate=True)
        q['images'] = originals
        q['fingerprint'] = fingerprint(q)
        q['difficulty'] = difficulty.estimate_difficulty(q)
        q['answer'] = None
        q['verified_saved'] = False
        draft['questions'].append(q)
        write_draft(path, draft)
        rem = await remaining(page)
        print(json.dumps({'scanned': n, 'total': len(ns), 'remaining_seconds': rem}), flush=True)
        await emit_progress(progress_cb, 'scan_question', {
            'number': n,
            'total': len(ns),
            'remaining_seconds': rem,
            'difficulty': q['difficulty']
        })
    draft['phase'] = 'scanned'
    write_draft(path, draft)
    await emit_progress(progress_cb, 'scan_complete', {'total': len(ns)})
    return draft


import time

def _robust_solve(question, client, retries=3):
    last_valid = None
    last_err = None
    for attempt in range(retries):
        try:
            cand = onluyen.validate(client(question, False), question)
            if cand is not None:
                last_valid = cand
            indep = onluyen.validate(client(question, True), question)
            if cand is not None and cand == indep:
                return cand
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                body = exc.read().decode('utf-8', errors='ignore')
            except Exception:
                body = ''
            exc.close()
            last_err = f'Server AI HTTP {code}: {body[:100]}'
            if code in (401, 403, 429, 503):
                raise onluyen.ProviderBlocked(f'Lỗi kết nối AI (HTTP {code}): {body[:100]}') from None
            time.sleep(1.0)
            continue
        except urllib.error.URLError as exc:
            last_err = f'Không thể kết nối đến server AI ({exc.reason})'
            time.sleep(1.0)
            continue
        except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
            last_err = str(exc)
            time.sleep(1.0)
            continue
    if last_valid is not None:
        return last_valid
    if last_err:
        raise ValueError(f'Không thể nhận lời giải từ AI: {last_err}')
    return None


async def classify_all(path, base, model, timeout, progress_cb=None):
    """Explicitly classify all questions in a homework draft using AI."""
    draft = json.loads(Path(path).read_text())
    check_url(draft['url'])
    await emit_progress(progress_cb, 'classify_start', {'total': len(draft['questions'])})
    draft['questions'] = await asyncio.get_running_loop().run_in_executor(
        None,
        difficulty.classify_batch_ai,
        draft['questions'],
        base,
        model,
        os.environ.get('ONLUYEN_API_KEY', ''),
        timeout
    )
    write_draft(path, draft)
    levels_count = {1: 0, 2: 0, 3: 0, 4: 0}
    for q in draft['questions']:
        d_val = q.get('difficulty', 2)
        levels_count[d_val] = levels_count.get(d_val, 0) + 1
    await emit_progress(progress_cb, 'classify_complete', {
        'total': len(draft['questions']),
        'levels': levels_count
    })
    return draft


async def solve_all(path, base, model, timeout, progress_cb=None):
    draft = json.loads(Path(path).read_text())
    check_url(draft['url'])
    if draft['phase'] in ('applying', 'verified', 'submission_pending', 'submitted'):
        await emit_progress(progress_cb, 'solve_complete', {'total': len(draft['questions'])})
        return draft
    if draft['phase'] not in ('scanned', 'solving', 'solved'):
        raise ValueError('Complete the scan before solving')
    client = onluyen.Client(base, model, os.environ.get('ONLUYEN_API_KEY', ''), timeout)
    draft['phase'] = 'solving'
    draft['model'] = model
    write_draft(path, draft)

    # 1. AI Difficulty Classification (classify entire exam with AI if not yet AI-classified)
    needs_ai_classification = any(q.get('difficulty_source') != 'ai' for q in draft['questions'])
    if needs_ai_classification:
        await emit_progress(progress_cb, 'classify_start', {'total': len(draft['questions'])})
        try:
            draft['questions'] = await asyncio.get_running_loop().run_in_executor(
                None,
                difficulty.classify_batch_ai,
                draft['questions'],
                base,
                model,
                os.environ.get('ONLUYEN_API_KEY', ''),
                timeout
            )
            write_draft(path, draft)
            levels_count = {1: 0, 2: 0, 3: 0, 4: 0}
            for q in draft['questions']:
                d_val = q.get('difficulty', 2)
                levels_count[d_val] = levels_count.get(d_val, 0) + 1
            await emit_progress(progress_cb, 'classify_complete', {
                'total': len(draft['questions']),
                'levels': levels_count
            })
        except Exception:
            pass

    # 2. AI Solving
    await emit_progress(progress_cb, 'solve_start', {'total': len(draft['questions']), 'model': model})
    for q in draft['questions']:
        if q['answer'] is not None:
            continue
        question = {k: v for k, v in q.items() if k not in ('answer', 'verified_saved', 'fingerprint', 'difficulty', 'difficulty_reason', 'difficulty_source')}
        answer = await asyncio.get_running_loop().run_in_executor(None, _robust_solve, question, client, 3)
        if answer is None:
            raise ValueError(f'AI không thể xác định đáp án cho câu {q["number"]}; bản nháp đã được lưu')
        q['answer'] = answer
        write_draft(path, draft)
        print(json.dumps({'number': q['number'], 'answer': answer}, ensure_ascii=False), flush=True)
        await emit_progress(progress_cb, 'solve_question', {
            'number': q['number'],
            'total': len(draft['questions']),
            'answer': answer,
            'difficulty': q.get('difficulty', 2),
            'difficulty_reason': q.get('difficulty_reason', '')
        })
    draft['phase'] = 'solved'
    write_draft(path, draft)
    await emit_progress(progress_cb, 'solve_complete', {'total': len(draft['questions'])})
    return draft


def plan_homework_score(path, target_score, progress_cb=None):
    draft = json.loads(Path(path).read_text())
    check_url(draft['url'])
    questions, projected = score_planner.plan_score(draft['questions'], target_score)
    draft['questions'] = questions
    draft['target_score'] = target_score
    draft['projected_score'] = projected
    write_draft(path, draft)
    if progress_cb:
        try:
            res = progress_cb('score_planned', {'target_score': target_score, 'projected_score': projected})
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        except Exception:
            pass
    return projected


async def apply_all(page, path, pacing='stealth', progress_cb=None):
    draft = json.loads(Path(path).read_text())
    check_url(draft['url'])
    if page.url != draft['url'] or draft['phase'] not in ('solved', 'applying', 'verified'):
        raise ValueError('Homework draft is not ready to apply')
    questions = draft['questions']
    if len(questions) < 2:
        raise ValueError('Cannot verify single-question homework')
    if [q['number'] for q in questions] != await numbers(page):
        raise ValueError('Homework question set changed')
    for q in questions:
        ans = q.get('applied_answer', q.get('answer'))
        if onluyen.validate({'kind': q['kind'], 'answer': ans, 'certain': True}, q) is None:
            raise ValueError('Unresolved homework question')
    draft['phase'] = 'applying'
    write_draft(path, draft)
    await emit_progress(progress_cb, 'apply_start', {'total': len(questions), 'pacing': pacing})
    for saved in questions:
        if saved.get('verified_saved'):
            continue
        if await remaining(page) < 60:
            raise ValueError('Insufficient time for verified saving')
        applied_ans = saved.get('applied_answer', saved.get('answer'))
        # Human-like pause before navigating to next question
        await asyncio.sleep(random.uniform(0.4, 1.5))
        q, root = await visit(page, saved['number'])
        check_question(q, saved)

        # Realistic human student deliberation based on question difficulty
        diff = saved.get('difficulty', 2)
        rem_sec = await remaining(page)
        delib_sec = human_pacing.calculate_deliberation_seconds(diff, pacing, rem_sec)
        await emit_progress(progress_cb, 'question_thinking', {
            'number': saved['number'],
            'total': len(questions),
            'difficulty': diff,
            'deliberation_seconds': delib_sec,
            'remaining_seconds': rem_sec
        })
        await human_pacing.simulate_human_deliberation(page, delib_sec)

        await choose(page, root, q, applied_ans)
        # Wait for button to transition to 'TRẢ LỜI' after choosing answer
        button = None
        text = ''
        for _ in range(25):
            button = await one_visible(page, ROOT + ' .submit-bar button')
            text = (await button.inner_text()).strip()
            if re.fullmatch(r'TRẢ LỜI', text, re.IGNORECASE):
                break
            await asyncio.sleep(0.1)

        if re.fullmatch(r'TRẢ LỜI', text, re.IGNORECASE):
            if not await button.is_enabled():
                raise ValueError('Homework save disabled')
            await require_unblocked(page)
            await asyncio.sleep(random.uniform(0.1, 0.4))  # pause before save
            await button.click()
            await page.wait_for_timeout(random.randint(350, 600))  # vary save wait
        elif not saved['verified_saved']:
            raise ValueError('Unknown homework save control')
        away = saved['number'] + 1 if saved['number'] < len(questions) else 1
        if away == saved['number']:
            raise ValueError('Cannot verify single-question homework')
        await asyncio.sleep(random.uniform(0.2, 0.6))
        await visit(page, away)
        await asyncio.sleep(random.uniform(0.3, 0.8))
        q, root = await visit(page, saved['number'])
        check_question(q, saved)
        if not await selected(root, q, applied_ans):
            raise ValueError('Homework saved answer did not persist')
        saved['verified_saved'] = True
        write_draft(path, draft)
        rem_now = await remaining(page)
        print(json.dumps({'verified_saved': saved['number'], 'remaining_seconds': rem_now}), flush=True)
        await emit_progress(progress_cb, 'question_saved', {
            'number': saved['number'],
            'total': len(questions),
            'remaining_seconds': rem_now
        })
    draft['phase'] = 'verified'
    write_draft(path, draft)
    await emit_progress(progress_cb, 'apply_complete', {'total': len(questions)})


async def submit_verified(page, path, progress_cb=None):
    draft = json.loads(Path(path).read_text())
    if draft.get('phase') != 'verified' or page.url != draft['url']:
        raise ValueError('Submission requires a verified homework draft')
    if [q['number'] for q in draft['questions']] != await numbers(page):
        raise ValueError('Homework question set changed')
    for saved in draft['questions']:
        if not saved.get('verified_saved'):
            raise ValueError('Homework contains unverified answers')
        q, root = await visit(page, saved['number'])
        check_question(q, saved)
        applied_ans = saved.get('applied_answer', saved.get('answer'))
        if not await selected(root, q, applied_ans):
            raise ValueError('Final homework readback failed')
    await require_unblocked(page)
    button = await one_visible(page, 'app-test-school-step-header .submit-button button')
    if (await button.inner_text()).strip().casefold() != 'nộp bài':
        raise ValueError('Unexpected homework submit button')
    await button.click()
    confirm = page.locator('button.btn-submit').filter(visible=True)
    await confirm.wait_for()
    # Poll enabled until deadline; never click while disabled.
    for _ in range(150):
        if await confirm.is_enabled():
            break
        await page.wait_for_timeout(100)
    else:
        raise ValueError('Homework confirmation never enabled')
    if await confirm.count() != 1 or (await confirm.inner_text()).strip().casefold() != 'nộp bài':
        raise ValueError('Unexpected homework confirmation')
    await require_unblocked(page)
    draft['phase'] = 'submission_pending'
    write_draft(path, draft)
    await emit_progress(progress_cb, 'submit_started', {'total': len(draft['questions'])})
    await confirm.click()  # Never retry an uncertain submission.
    await page.wait_for_url(re.compile(r'https://app\.onluyen\.vn/school/test/history/[A-Za-z0-9]+$'))
    await page.get_by_text('Nộp bài thành công', exact=True).wait_for()
    draft['phase'] = 'submitted'
    draft['result_url'] = page.url
    draft['result_status'] = 'grading'
    write_draft(path, draft)
    print(json.dumps({'submitted': True, 'verified_answers': len(draft['questions']), 'status': 'grading'}), flush=True)
    await emit_progress(progress_cb, 'submitted', {
        'result_url': page.url,
        'verified_answers': len(draft['questions'])
    })
