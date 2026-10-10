"""Assigned homework: scan first, solve into a private draft, then apply explicitly."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
import onluyen


NAV = 'app-sidebar-school-test .answer-sheet .option'
ROOT = 'app-test-school-step-data-regular'


def check_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'app.onluyen.vn' or not re.fullmatch(r'/school/test/step/[A-Za-z0-9]+', parts.path) or parts.query or parts.fragment:
        raise ValueError('Require an existing homework /school/test/step/ID URL')


def write_draft(path, data):
    path = Path(path)
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


def numbers(page):
    labels = [x.strip() for x in page.locator(NAV).filter(visible=True).all_text_contents()]
    if not labels or labels != [str(i) for i in range(1, len(labels) + 1)] or len(labels) > 200:
        raise ValueError('Ambiguous homework navigation')
    return list(range(1, len(labels) + 1))


def remaining(page):
    loc = page.locator('app-test-school-step-header .pause-timer, .pause-timer, .timer-value').filter(visible=True)
    try:
        loc.first.wait_for(state='visible', timeout=3000)
    except Exception:
        pass
    count = loc.count()
    if count == 0:
        return 86400
    text = loc.first.inner_text().strip()
    if re.fullmatch(r'\d{1,2}:\d{2}:\d{2}', text):
        h, m, s = map(int, text.split(':'))
        if m < 60 and s < 60:
            return h * 3600 + m * 60 + s
    if re.fullmatch(r'\d{1,2}:\d{2}', text):
        m, s = map(int, text.split(':'))
        if s < 60:
            return m * 60 + s
    return 86400


def visit(page, number):
    onluyen.require_unblocked(page)
    item = page.locator(NAV).filter(visible=True).filter(has_text=re.compile(r'^\s*' + str(number) + r'\s*$'))
    if item.count() != 1:
        raise ValueError('Ambiguous homework question')
    item.click()
    page.wait_for_function(
        r'''n => {
            const text = (document.querySelector('app-test-school-step-data-regular')?.innerText) || '';
            const m = text.match(/Câu:\s*(\d+)/);
            return m && parseInt(m[1], 10) === n;
        }''',
        arg=number,
        timeout=15000
    )
    try:
        page.wait_for_function('''() => [...document.querySelectorAll('app-test-school-step-data-regular img')].every(i => i.complete && i.naturalWidth > 0)''', timeout=5000)
    except Exception:
        pass
    q, root = onluyen.extract(page, ROOT + ' .question-container')
    if q['number'] != number:
        raise ValueError('Homework question changed')
    q['body'] = root.locator('.question-name').all_text_contents()
    q['choice_text'] = root.locator('.question-option-content').all_text_contents()
    sources = root.locator('img').evaluate_all('(els)=>els.map(e=>e.currentSrc || e.src)')
    q['image_hashes'] = [hashlib.sha256(src.encode()).hexdigest() for src in sources]
    return q, root


def fingerprint(question):
    # Exclude mutable answer widgets/markers; compare the stable question body and choices.
    fields = ('id', 'number', 'kind', 'mathml', 'options', 'items', 'body', 'choice_text', 'image_hashes')
    return hashlib.sha256(json.dumps({k: question[k] for k in fields if k in question}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scan(page, path, url):
    ns = numbers(page)
    if Path(path).exists():
        draft = json.loads(Path(path).read_text())
        if draft.get('url') != url or draft.get('phase') != 'scanning':
            raise ValueError('Draft belongs to another attempt or completed scan')
        if [q['number'] for q in draft['questions']] != ns[:len(draft['questions'])]:
            raise ValueError('Invalid partial scan')
    else:
        draft = {'version': 1, 'url': url, 'phase': 'scanning', 'questions': []}
    write_draft(path, draft)
    seen = {q['id'] for q in draft['questions']}
    for n in ns[len(draft['questions']):]:
        if remaining(page) < 120:
            raise ValueError('Insufficient time to scan homework')
        q, root = visit(page, n)
        if q['id'] in seen:
            raise ValueError('Duplicate homework question ID')
        seen.add(q['id'])
        # The cropped screenshot contains every visible image, including base64 images.
        originals = root.locator('img').evaluate_all('(els)=>els.map(e=>e.currentSrc || e.src).filter(s=>s.startsWith("data:image/"))')
        if len(originals) > 16 or sum(len(x) for x in originals) > 16_000_000:
            raise ValueError('Oversized homework images')
        for image in originals:
            if not re.match(r'^data:image/(png|jpeg|gif|webp);base64,', image):
                raise ValueError('Unsupported homework image')
            base64.b64decode(image.split(',', 1)[1], validate=True)
        q['images'] = originals
        q['fingerprint'] = fingerprint(q)
        q['answer'] = None
        q['verified_saved'] = False
        draft['questions'].append(q)
        write_draft(path, draft)
        print(json.dumps({'scanned': n, 'total': len(ns), 'remaining_seconds': remaining(page)}), flush=True)
    draft['phase'] = 'scanned'
    write_draft(path, draft)
    return draft


def solve_all(path, base, model, timeout):
    draft = json.loads(Path(path).read_text())
    check_url(draft['url'])
    if draft['phase'] in ('applying', 'verified', 'submission_pending', 'submitted'):
        return draft
    if draft['phase'] not in ('scanned', 'solving', 'solved'):
        raise ValueError('Complete the scan before solving')
    client = onluyen.Client(base, model, os.environ.get('ONLUYEN_API_KEY', ''), timeout)
    draft['phase'] = 'solving'
    draft['model'] = model
    write_draft(path, draft)
    for q in draft['questions']:
        if q['answer'] is not None:
            continue
        question = {k: v for k, v in q.items() if k not in ('answer', 'verified_saved', 'fingerprint', 'difficulty')}
        answer = onluyen.solve(question, client, 2)
        q['answer'] = answer
        write_draft(path, draft)
        print(json.dumps({'number': q['number'], 'answer': answer}, ensure_ascii=False), flush=True)
        if answer is None:
            raise ValueError('No certain agreed homework answer; draft preserved')
    draft['phase'] = 'solved'
    write_draft(path, draft)
    return draft


def choose(page, root, question, answer):
    onluyen.require_unblocked(page)
    if onluyen.validate({'kind': question['kind'], 'answer': answer, 'certain': True}, question) is None:
        raise ValueError('Invalid homework answer')
    if onluyen.selected(root, question, answer):
        return
    if question['kind'] == 'single':
        option = root.locator('.question-option').filter(has=page.locator('.question-option-label', has_text=re.compile(r'^\s*' + re.escape(answer) + r'\s*$')))
        if option.count() != 1:
            raise ValueError('Ambiguous homework option')
        option.click()
    elif question['kind'] == 'numeric':
        entry = onluyen.one(root, 'input.can-resize-second, input.input-size-1, input.can-resize, input[id^="mathplay-answer"]')
        if not entry.is_editable():
            raise ValueError('Homework numeric answer is read-only')
        entry.fill('')
        entry.press_sequentially(str(answer), delay=80)
        entry.press('Tab')
    else:
        radios = root.locator('input[type=radio]')
        for i, value in enumerate(answer):
            radio = radios.nth(i * 2 + (0 if value else 1))
            ident = radio.get_attribute('id')
            matches = [x for x in root.locator('label').all() if ident and x.get_attribute('for') == ident]
            if len(matches) != 1:
                raise ValueError('Ambiguous homework radio label')
            matches[0].click()
    if not onluyen.selected(root, question, answer):
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


def apply_all(page, path):
    draft = json.loads(Path(path).read_text())
    check_url(draft['url'])
    if page.url != draft['url'] or draft['phase'] not in ('solved', 'applying', 'verified'):
        raise ValueError('Homework draft is not ready to apply')
    questions = draft['questions']
    if len(questions) < 2:
        raise ValueError('Cannot verify single-question homework')
    if [q['number'] for q in questions] != numbers(page):
        raise ValueError('Homework question set changed')
    for q in questions:
        if onluyen.validate({'kind': q['kind'], 'answer': q['answer'], 'certain': True}, q) is None:
            raise ValueError('Unresolved homework question')
    draft['phase'] = 'applying'
    write_draft(path, draft)
    for saved in questions:
        if saved.get('verified_saved'):
            continue
        if remaining(page) < 60:
            raise ValueError('Insufficient time for verified saving')
        q, root = visit(page, saved['number'])
        check_question(q, saved)
        choose(page, root, q, saved['answer'])
        button = None
        text = ''
        for _ in range(25):
            button = onluyen.one(page, ROOT + ' .submit-bar button')
            text = button.inner_text().strip()
            if re.fullmatch(r'TRẢ LỜI', text, re.IGNORECASE):
                break
            page.wait_for_timeout(100)
        if re.fullmatch(r'TRẢ LỜI', text, re.IGNORECASE):
            if not button.is_enabled():
                raise ValueError('Homework save disabled')
            onluyen.require_unblocked(page)
            button.click()
            page.wait_for_timeout(400)
        elif not saved['verified_saved']:
            raise ValueError('Unknown homework save control')
        away = saved['number'] + 1 if saved['number'] < len(questions) else 1
        if away == saved['number']:
            raise ValueError('Cannot verify single-question homework')
        visit(page, away)
        q, root = visit(page, saved['number'])
        check_question(q, saved)
        if not onluyen.selected(root, q, saved['answer']):
            raise ValueError('Homework saved answer did not persist')
        saved['verified_saved'] = True
        write_draft(path, draft)
        print(json.dumps({'verified_saved': saved['number'], 'remaining_seconds': remaining(page)}), flush=True)
    draft['phase'] = 'verified'
    write_draft(path, draft)


def submit_verified(page, path):
    draft = json.loads(Path(path).read_text())
    if draft.get('phase') != 'verified' or page.url != draft['url']:
        raise ValueError('Submission requires a verified homework draft')
    if [q['number'] for q in draft['questions']] != numbers(page):
        raise ValueError('Homework question set changed')
    for saved in draft['questions']:
        if not saved.get('verified_saved'):
            raise ValueError('Homework contains unverified answers')
        q, root = visit(page, saved['number'])
        check_question(q, saved)
        if not onluyen.selected(root, q, saved['answer']):
            raise ValueError('Final homework readback failed')
    onluyen.require_unblocked(page)
    button = onluyen.one(page, 'app-test-school-step-header .submit-button button')
    if button.inner_text().strip().casefold() != 'nộp bài':
        raise ValueError('Unexpected homework submit button')
    button.click()
    confirm = page.locator('button.btn-submit').filter(visible=True)
    confirm.wait_for()
    from playwright.sync_api import expect
    expect(confirm).to_be_enabled(timeout=15000)
    if confirm.count() != 1 or confirm.inner_text().strip().casefold() != 'nộp bài':
        raise ValueError('Unexpected homework confirmation')
    onluyen.require_unblocked(page)
    draft['phase'] = 'submission_pending'
    write_draft(path, draft)
    confirm.click()  # Never retry an uncertain submission.
    page.wait_for_url(re.compile(r'https://app\.onluyen\.vn/school/test/history/[A-Za-z0-9]+$'))
    page.get_by_text('Nộp bài thành công', exact=True).wait_for()
    draft['phase'] = 'submitted'
    draft['result_url'] = page.url
    draft['result_status'] = 'grading'
    write_draft(path, draft)
    print(json.dumps({'submitted': True, 'verified_answers': len(draft['questions']), 'status': 'grading'}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('scan', 'solve', 'apply', 'submit'))
    p.add_argument('--authorize-save', action='store_true', help='Explicitly allow saving all solved homework answers')
    p.add_argument('--authorize-submit', action='store_true', help='Explicitly allow submitting the verified homework')
    p.add_argument('--url-file', default='homework-active-url.txt')
    p.add_argument('--draft', default='homework-draft.json')
    p.add_argument('--profile', default=str(Path(__file__).with_name('.browser-profile')))
    p.add_argument('--base-url', default='http://localhost:4981/openai/v1')
    p.add_argument('--model', default='gemini-3.8-flash')
    p.add_argument('--timeout', type=float, default=45)
    args = p.parse_args()
    if not 1 <= args.timeout <= 120:
        p.error('timeout must be 1..120 seconds')
    if args.action == 'apply' and not args.authorize_save:
        p.error('apply requires --authorize-save')
    if args.action == 'submit' and not args.authorize_submit:
        p.error('submit requires --authorize-submit')
    if args.action == 'solve':
        solve_all(args.draft, args.base_url, args.model, args.timeout)
        return
    url = Path(args.url_file).read_text().strip()
    check_url(url)
    profile = Path(args.profile).expanduser()
    if not profile.is_dir():
        raise onluyen.LoginRequired()
    with onluyen.profile_lock(profile), sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(profile.resolve()), headless=True)
        try:
            page = context.new_page()
            def watch_response(response):
                if response.status == 403 and urlsplit(response.url).hostname == 'app.onluyen.vn':
                    page._onluyen_forbidden = True
            page.on('response', watch_response)
            page.set_default_timeout(20000)
            page.goto(url, wait_until='domcontentloaded')
            page.locator(NAV).first.wait_for()
            if page.url != url:
                raise ValueError('Homework route changed')
            if args.action == 'apply':
                apply_all(page, args.draft)
            elif args.action == 'submit':
                submit_verified(page, args.draft)
            else:
                scan(page, args.draft, url)
        finally:
            context.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Homework stopped:', onluyen.safe_error(exc), flush=True)
        raise SystemExit(1) from None
