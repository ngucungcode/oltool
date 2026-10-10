"""Educational Tutor Module: Generates detailed step-by-step mathematical explanations,
key formulas, and hints for high school students.
"""
import json
import logging
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger("OnluyenTutor")


def generate_explanation(
    question: Dict[str, Any],
    base_url: str = "http://localhost:4981/openai/v1",
    model: str = "gemini-3.8-flash",
    api_key: str = "",
    timeout: int = 60
) -> Dict[str, Any]:
    """Ask AI tutor to explain the question step-by-step with formulas and tips."""
    content = {
        "number": question.get("number"),
        "kind": question.get("kind"),
        "text": question.get("text", ""),
        "options": question.get("options", []),
        "items": question.get("items", []),
        "body": question.get("body", []),
        "difficulty": question.get("difficulty", 2)
    }

    instruction = (
        "Bạn là gia sư Toán học xuất sắc chương trình GDPT 2018 Việt Nam. "
        "Hãy phân tích và giải thích cặn kẽ câu hỏi được cung cấp. "
        "Bắt buộc trả về duy nhất một đối tượng JSON hợp lệ gồm đúng các trường sau:\n"
        "{\n"
        '  "key_concept": "Tên dạng toán, định lý hoặc công thức cốt lõi cần áp dụng",\n'
        '  "hint": "Gợi ý tư duy ngắn gọn (1-2 câu) giúp học sinh tự tìm ra cách làm",\n'
        '  "step_by_step": "Lời giải chi tiết từng bước, rõ ràng, tính toán chuẩn xác",\n'
        '  "correct_answer": "Đáp án đúng (ví dụ: A hoặc B hoặc giá trị số cụ thể hoặc Đúng/Sai)",\n'
        '  "caution": "Bẫy thường gặp hoặc điểm cần lưu ý để tránh mất điểm"\n'
        "}"
    )

    images = list(dict.fromkeys([img for img in [question.get('image'), *question.get('images', [])] if img]))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(content, ensure_ascii=False)},
                    *[{"type": "image_url", "image_url": {"url": img}} for img in images]
                ]
            }
        ],
        "max_tokens": 2048,
        "response_format": {"type": "json_object"}
    }

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, json.dumps(payload).encode("utf-8"), headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_content = data["choices"][0]["message"]["content"]
            return json.loads(raw_content)
    except Exception as exc:
        logger.warning(f"Failed to generate AI tutor explanation: {exc}")
        return {
            "key_concept": "Ôn tập kiến thức cơ bản",
            "hint": "Đọc kỹ đề bài và áp dụng định nghĩa cơ bản trong SGK.",
            "step_by_step": "Xem lại phương pháp giải trong bài giảng trên lớp.",
            "correct_answer": str(question.get("answer", "Chưa xác định")),
            "caution": "Kiểm tra kỹ điều kiện xác định trước khi kết luận."
        }


def format_telegram_tutor_message(
    q_num: int,
    total: int,
    exp: Dict[str, Any],
    question: Dict[str, Any]
) -> str:
    """Format explanation nicely into an HTML message for Telegram."""
    diff_labels = {
        1: "🟢 Nhận biết",
        2: "🟡 Thông hiểu",
        3: "🟠 Vận dụng",
        4: "🔴 Vận dụng cao"
    }
    diff_badge = diff_labels.get(question.get("difficulty", 2), "🟡 Thông hiểu")

    # Clean question text (remove technical headers like 'Câu: 1 #12345')
    text = question.get("text", "")
    text = re.sub(r"Câu:\s*\d+\s*#\d+", "", text)
    text = re.sub(r"Báo lỗi\s*Lưu ý.*", "", text, flags=re.DOTALL).strip()
    if len(text) > 300:
        text = text[:300] + "..."

    msg = (
        f"📖 <b>GIA SƯ TOÁN - CÂU {q_num}/{total}</b> [{diff_badge}]\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Đề bài:</b> <i>{text}</i>\n\n"
        f"💡 <b>Gợi ý tư duy:</b>\n{exp.get('hint', '')}\n\n"
        f"📐 <b>Công thức cốt lõi:</b> <code>{exp.get('key_concept', '')}</code>\n\n"
        f"✍️ <b>Lời giải chi tiết:</b>\n{exp.get('step_by_step', '')}\n\n"
        f"✅ <b>Đáp án:</b> <b>{exp.get('correct_answer', '')}</b>\n"
    )
    if exp.get("caution"):
        msg += f"\n⚠️ <b>Lưu ý tránh bẫy:</b> <i>{exp.get('caution')}</i>"

    return msg
