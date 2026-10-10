"""Question difficulty estimation for Vietnamese high school / THPT exams.

Classifies questions into 4 standard Ministry of Education (BGD) cognitive levels:
  1: Nhận biết (Recognition / Easy) - direct formula, definitions, graph readings
  2: Thông hiểu (Comprehension / Medium) - 1-2 standard calculation steps
  3: Vận dụng (Application / Hard) - multi-step synthesis, parameter analysis (m, k)
  4: Vận dụng cao (High Application / Very Hard) - optimization, real-world models, subtle cases
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import urllib.request
from urllib.parse import urlsplit

logger = logging.getLogger("DifficultyAI")

DIFFICULTY_LABELS = {
    1: 'Nhận biết',
    2: 'Thông hiểu',
    3: 'Vận dụng',
    4: 'Vận dụng cao'
}

AI_CLASSIFICATION_PROMPT = """Bạn là chuyên gia thẩm định và khảo thí đề thi tốt nghiệp THPT môn Toán theo chuẩn Bộ GD&ĐT Việt Nam.
Hãy phân loại độ khó cho từng câu hỏi vào đúng 1 trong 4 mức độ tư duy chuẩn:
1: Nhận biết (Hỏi trực tiếp định nghĩa, công thức cơ bản, nhìn đồ thị/bảng biến thiên thấy ngay)
2: Thông hiểu (Cần 1-2 bước tính toán tiêu chuẩn, áp dụng trực tiếp định lý/tính chất cơ bản)
3: Vận dụng (Tổng hợp nhiều bước tính, bài toán chứa tham số m, kết hợp 2-3 mảng kiến thức)
4: Vận dụng cao (Bài toán thực tế tối ưu hóa, cực trị hình học/giải tích nhiều tầng, phân loại trường hợp khó)

Yêu cầu trả về DUY NHẤT một JSON theo định dạng:
{
  "classifications": [
    {"number": 1, "difficulty": 1|2|3|4, "reason": "giải thích ngắn gọn 1 câu"}
  ]
}"""


def parse_difficulty_value(val: Any) -> Optional[int]:
    """Parse integer or string difficulty representation into int 1..4."""
    if isinstance(val, int) and 1 <= val <= 4:
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if 'nhận biết' in v or 'easy' in v or v == '1':
            return 1
        if 'thông hiểu' in v or 'medium' in v or v == '2':
            return 2
        if 'vận dụng cao' in v or 'very_hard' in v or v == '4':
            return 4
        if 'vận dụng' in v or 'hard' in v or v == '3':
            return 3
        try:
            num = int(v)
            if 1 <= num <= 4:
                return num
        except ValueError:
            pass
    return None


def estimate_difficulty(question: Dict[str, Any]) -> int:
    """Determine difficulty level (1-4) for a question.

    If AI solver or classifier already assessed the difficulty (1-4), use it.
    Otherwise, applies structural heuristics based on the BGD THPT exam pattern.
    """
    ai_diff = parse_difficulty_value(question.get('difficulty'))
    if ai_diff is not None:
        return ai_diff
    return heuristic_difficulty(question)


def heuristic_difficulty(question: Dict[str, Any]) -> int:
    """Heuristic difficulty estimation based on question structure."""
    num = question.get('number', 1)
    kind = question.get('kind', 'single')
    text = ' '.join(question.get('body', []) if isinstance(question.get('body'), list) else [str(question.get('text', ''))])
    has_image = bool(question.get('image') or question.get('images') or question.get('image_hashes'))

    score = 0

    # 1. Base score by question type and index in standard 22-question THPT Math paper
    if kind == 'single':
        if num <= 6:
            score += 1
        elif num <= 10:
            score += 2
        else:
            score += 3
    elif kind == 'truefalse':
        if num == 1:
            score += 2
        elif num == 2:
            score += 2
        elif num == 3:
            score += 3
        else:
            score += 4
    elif kind == 'numeric':
        if num in (17, 18) or num <= 2:
            score += 2
        elif num in (19, 20) or num <= 4:
            score += 3
        else:
            score += 4
    else:
        score += 2

    # 2. Mathematical indicators
    if re.search(r'\b(tham số|giá trị thực của m|tìm m|với mọi m|nguyên dương|cực trị|lớn nhất|nhỏ nhất|tối ưu)\b', text, re.I):
        score += 1

    if re.search(r'\b(bài toán thực tế|chi phí|lợi nhuận|mảnh vườn|công ty|không vượt quá|thể tích lớn nhất)\b', text, re.I):
        score += 1

    if has_image and ('bảng biến thiên' in text.lower() or 'đồ thị' in text.lower()):
        score = max(score, 2)

    return max(1, min(4, score))


def _question_summary_for_ai(q: Dict[str, Any]) -> Dict[str, Any]:
    """Build a concise text representation of question for AI difficulty evaluation."""
    text = ' '.join(q.get('body', []) if isinstance(q.get('body'), list) else [str(q.get('text', ''))])
    text = re.sub(r'\s+', ' ', text).strip()
    summary = {
        'number': q.get('number', 1),
        'kind': q.get('kind', 'single'),
        'question': text[:500]
    }
    if q.get('options'):
        summary['options'] = q.get('options')
    if q.get('items'):
        summary['items_count'] = q.get('items')
    return summary


def classify_batch_ai(
    questions: List[Dict[str, Any]],
    base_url: str,
    model: str,
    key: Optional[str] = None,
    timeout: int = 45
) -> List[Dict[str, Any]]:
    """Classify difficulty of all questions using AI in one batch request with heuristic fallback."""
    if not questions:
        return questions

    summaries = [_question_summary_for_ai(q) for q in questions]
    endpoint = base_url.rstrip('/') + '/chat/completions'

    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': AI_CLASSIFICATION_PROMPT},
            {'role': 'user', 'content': json.dumps(summaries, ensure_ascii=False)}
        ],
        'response_format': {'type': 'json_object'}
    }

    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = f'Bearer {key}'

    data_bytes = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(endpoint, data=data_bytes, headers=headers)

    classifications_by_num = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode('utf-8')
            resp_json = json.loads(content)
            msg_content = resp_json['choices'][0]['message']['content']
            parsed = json.loads(msg_content)
            items = parsed.get('classifications', [])
            for item in items:
                n = item.get('number')
                diff = parse_difficulty_value(item.get('difficulty'))
                if n is not None and diff is not None:
                    classifications_by_num[n] = {
                        'difficulty': diff,
                        'reason': str(item.get('reason', '')).strip(),
                    }
    except Exception as exc:
        logger.warning(f"AI batch classification failed ({exc}); falling back to heuristic")

    # Apply classifications to questions
    for q in questions:
        n = q.get('number')
        if n in classifications_by_num:
            q['difficulty'] = classifications_by_num[n]['difficulty']
            q['difficulty_reason'] = classifications_by_num[n]['reason']
            q['difficulty_source'] = 'ai'
        elif q.get('difficulty') is None or q.get('difficulty_source') not in ('ai', 'manual'):
            q['difficulty'] = heuristic_difficulty(q)
            q['difficulty_reason'] = 'Phân loại theo cấu trúc đề thi'
            q['difficulty_source'] = 'heuristic'

    return questions


def classify_question_ai(
    question: Dict[str, Any],
    base_url: str,
    model: str,
    key: Optional[str] = None,
    timeout: int = 30
) -> int:
    """Classify difficulty of a single question using AI with heuristic fallback."""
    res = classify_batch_ai([question], base_url, model, key=key, timeout=timeout)
    return res[0].get('difficulty', heuristic_difficulty(question))
