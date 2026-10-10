"""OLTool - Standalone Onluyen Homework Automation & AI Tutor.

Single-machine standalone CLI runner with human pacing, score planner, and anti-detection.
"""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from playwright.async_api import async_playwright

import account_flow
import fingerprint
import homework
import homework_async
import onluyen
import stealth
import tutor

CONFIG_FILE = Path("config.json")
EXAMPLE_CONFIG = Path("config.example.json")
DISCLAIMER = "⚠️ LƯU Ý: Công cụ phục vụ mục đích nghiên cứu & học tập. Người dùng tự chịu trách nhiệm về kết quả bài tập của mình."

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("OLTool")


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json or fallback to defaults."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(f"Could not parse config.json: {exc}")
    return {}


def prompt_config_interactive() -> Dict[str, Any]:
    """Guide user to configure credentials interactively on first run."""
    print("=" * 65)
    print("🎓 CHÀO MỪNG BẠN ĐẾN VỚI OLTOOL (PHIÊN BẢN CHẠY TRỰC TIẾP)")
    print(DISCLAIMER)
    print("=" * 65)
    print("\nChưa tìm thấy file config.json. Hãy nhập thông tin cơ bản:\n")

    username = input("1. Tên đăng nhập Onluyen (Email/SĐT): ").strip()
    password = input("2. Mật khẩu Onluyen: ").strip()
    target_score_str = input("3. Điểm số mong muốn (mặc định 8.5): ").strip() or "8.5"
    pacing = input("4. Tốc độ làm bài (stealth/balanced/fast, mặc định stealth): ").strip() or "stealth"
    api_key = input("5. OpenAI-compatible API Key (Gemini/OpenAI/DeepSeek): ").strip()

    cfg = {
        "username": username,
        "password": password,
        "test_url": None,
        "target_score": float(target_score_str),
        "pacing": pacing,
        "mode": "auto",
        "ai": {
            "base_url": "https://api.openai.com/v1",
            "api_key": api_key,
            "model": "gpt-4o-mini",
            "timeout": 60
        }
    }
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ Đã lưu cấu hình vào {CONFIG_FILE.resolve()}\n")
    return cfg


async def progress_reporter(event_type: str, data: dict):
    """Print clean terminal progress indicators."""
    if event_type == "login_start":
        print(f"🔑 Đang đăng nhập tài khoản: {data.get('username')}...")
    elif event_type == "login_success":
        print("✅ Đăng nhập Onluyen thành công!")
    elif event_type == "discover_start":
        print("🔍 Đang tìm kiếm bài tập được giao trên Dashboard...")
    elif event_type == "discover_success":
        print(f"📋 Đã mở bài tập: {data.get('url')}")
    elif event_type == "scan_start":
        print(f"📥 Bắt đầu quét đề thi ({data.get('total')} câu hỏi)...")
    elif event_type == "scan_question":
        num = data.get("number")
        tot = data.get("total")
        diff = data.get("difficulty", 2)
        print(f"   • Đã quét câu {num}/{tot} (Độ khó: Mức {diff})")
    elif event_type == "scan_complete":
        print(f"✅ Quét xong toàn bộ {data.get('total')} câu hỏi.")
    elif event_type == "score_planned":
        print(f"🎯 Điểm mục tiêu: {data.get('target_score')}đ | Dự kiến đạt: {data.get('projected_score')}đ")
    elif event_type == "question_thinking":
        num = data.get("number")
        tot = data.get("total")
        sec = data.get("deliberation_seconds", 0)
        print(f"🤔 Câu {num}/{tot} - Đang cân nhắc suy nghĩ (~{int(sec)}s)...")
    elif event_type == "question_saved":
        print(f"💾 Đã áp dụng câu {data.get('number')}/{data.get('total')}")
    elif event_type == "submit_started":
        print("🚀 Đang nộp bài lên hệ thống Onluyen...")
    elif event_type == "submitted":
        res_url = data.get("result_url", "")
        print(f"\n🎉 NỘP BÀI THÀNH CÔNG! Đã hoàn thành {data.get('verified_answers')} câu.")
        if res_url:
            print(f"🔗 Xem kết quả bài chấm: {res_url}")


async def run(args: argparse.Namespace):
    cfg = load_config()
    if not cfg and not args.username:
        cfg = prompt_config_interactive()

    username = args.username or cfg.get("username", "")
    password = args.password or cfg.get("password", "")
    target_score = args.target_score if args.target_score is not None else float(cfg.get("target_score", 8.5))
    pacing = args.pacing or cfg.get("pacing", "stealth")
    mode = args.mode or cfg.get("mode", "auto")
    url = args.url or cfg.get("test_url")
    headed = args.headed

    ai_cfg = cfg.get("ai", {})
    base_url = args.base_url or ai_cfg.get("base_url", "https://api.openai.com/v1")
    api_key = args.api_key or ai_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    model = args.model or ai_cfg.get("model", "gpt-4o-mini")
    timeout = args.timeout or ai_cfg.get("timeout", 60)

    if not username or not password:
        print("❌ Lỗi: Thiếu tên đăng nhập hoặc mật khẩu Onluyen.")
        sys.exit(1)

    print("=" * 65)
    print("🚀 OLTOOL - TỰ ĐỘNG HOÁ & GIA SƯ ONLUYEN")
    print(DISCLAIMER)
    print("=" * 65)
    print(f"• Tài khoản: {username}")
    print(f"• Chế độ: {mode.upper()}")
    print(f"• Mục tiêu điểm: {target_score}đ | Tốc độ: {pacing}")
    print(f"• AI Model: {model} ({base_url})\n")

    account = {
        "name": "local_user",
        "username": username,
        "password": password,
        "target_score": target_score,
        "pacing": pacing,
    }

    # Browser profile and fingerprint isolation
    profile_dir = Path(".profiles/local_user")
    profile_dir.mkdir(parents=True, exist_ok=True)
    fp = fingerprint.from_account(account)

    Path("drafts").mkdir(exist_ok=True)
    Path("urls").mkdir(exist_ok=True)
    draft_path = "drafts/local_draft.json"

    async with async_playwright() as pw:
        launch_args = list(stealth.STEALTH_ARGS)
        browser = await pw.chromium.launch(
            headless=not headed,
            args=launch_args
        )
        context = await browser.new_context(
            viewport=fp.get("screen", {"width": 1440, "height": 900}),
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh"
        )
        await fingerprint.apply(context, fp)

        page = await context.new_page()
        page.set_default_timeout(timeout * 1000)

        try:
            # 1. Login
            await progress_reporter("login_start", {"username": username})
            await account_flow.ensure_authenticated(page, account)
            await progress_reporter("login_success", {})

            # 2. Discover homework if no URL specified
            if not url:
                await progress_reporter("discover_start", {})
                url = await account_flow.discover_and_open_homework(page)
                await progress_reporter("discover_success", {"url": url})
            else:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(1000)

            # Check if test already completed
            current_url = page.url.lower()
            if "/school/test/result/" in current_url or "/home-student" in current_url:
                print(f"ℹ️ Bài tập tại {url} đã hoàn thành hoặc hết hạn.")
                return

            # Wait for answer sheet navigation
            try:
                await page.locator(homework.NAV).first.wait_for(timeout=15000)
            except Exception:
                if "/school/test/result/" in page.url or "kết thúc" in (await page.content()).lower():
                    print("ℹ️ Bài tập này đã hoàn thành trước đó.")
                    return
                raise

            # Execute according to Mode
            if mode == "tutor":
                print("\n📚 ĐANG Ở CHẾ ĐỘ GIA SƯ: Quét đề và tạo lời giải chi tiết...")
                await homework_async.scan(page, draft_path, page.url, progress_reporter)
                await homework_async.solve_all(draft_path, base_url, model, timeout, progress_reporter)
                
                # Stream explanation
                d_info = json.loads(Path(draft_path).read_text(encoding="utf-8"))
                questions = d_info.get("questions", [])
                out_md = ["# LỜI GIẢI CHI TIẾT BÀI TẬP ONLUYEN\n\n"]
                for idx, q in enumerate(questions, 1):
                    exp = tutor.generate_explanation(q, base_url=base_url, model=model, api_key=api_key, timeout=timeout)
                    print(f"\n--- [CÂU {idx}/{len(questions)}] ---")
                    print(exp.strip())
                    out_md.append(f"## Câu {idx}\n\n{exp}\n\n---\n\n")

                out_file = Path("loigiai.md")
                out_file.write_text("".join(out_md), encoding="utf-8")
                print(f"\n💡 Đã lưu toàn bộ lời giải chi tiết vào file: {out_file.resolve()}")

            elif mode == "scan":
                await homework_async.scan(page, draft_path, page.url, progress_reporter)
                print(f"✅ Đã quét xong câu hỏi vào {draft_path}")

            elif mode == "solve":
                await homework_async.solve_all(draft_path, base_url, model, timeout, progress_reporter)
                print(f"✅ Đã giải xong câu hỏi trong {draft_path}")

            elif mode == "apply":
                if target_score < 10.0:
                    homework_async.plan_homework_score(draft_path, target_score, progress_reporter)
                await homework_async.apply_all(page, draft_path, pacing=pacing, progress_cb=progress_reporter)
                print("✅ Đã điền xong đáp án vào trang thi.")

            elif mode == "submit":
                await homework_async.submit_verified(page, draft_path, progress_reporter)

            else:  # auto mode
                await homework_async.scan(page, draft_path, page.url, progress_reporter)
                await homework_async.solve_all(draft_path, base_url, model, timeout, progress_reporter)
                if target_score < 10.0:
                    homework_async.plan_homework_score(draft_path, target_score, progress_reporter)
                await homework_async.apply_all(page, draft_path, pacing=pacing, progress_cb=progress_reporter)
                await homework_async.submit_verified(page, draft_path, progress_reporter)

        finally:
            await context.close()
            await browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="OLTool - Standalone Onluyen Homework Automation & AI Tutor",
        epilog=DISCLAIMER
    )
    parser.add_argument("-u", "--username", help="Tên đăng nhập Onluyen")
    parser.add_argument("-p", "--password", help="Mật khẩu Onluyen")
    parser.add_argument("--url", help="Link bài tập (nếu không có sẽ tự tìm trên dashboard)")
    parser.add_argument("--target-score", type=float, help="Điểm số mục tiêu (0.0 - 10.0)")
    parser.add_argument("--pacing", choices=["stealth", "balanced", "fast"], help="Tốc độ làm bài")
    parser.add_argument("--mode", choices=["auto", "tutor", "scan", "solve", "apply", "submit"], help="Chế độ chạy")
    parser.add_argument("--model", help="AI Model (vd: gpt-4o-mini, gemini-2.5-flash)")
    parser.add_argument("--base-url", help="OpenAI-compatible Base URL")
    parser.add_argument("--api-key", help="API Key cho AI")
    parser.add_argument("--timeout", type=int, help="Timeout xử lý (giây)")
    parser.add_argument("--headed", action="store_true", help="Hiện cửa sổ trình duyệt (mặc định chạy ẩn)")

    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
