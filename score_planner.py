"""Score Planner following Ministry of Education (BGD) 2025+ THPT Exam format.

BGD 2025 THPT Exam Point Allocation:
  - Part I (Multiple Choice 4 options): 12 questions x 0.25 = 3.0 pts max.
  - Part II (True/False 4 sub-items): 4 questions x 1.0 = 4.0 pts max.
      * 1 item correct: 0.10 pt
      * 2 items correct: 0.25 pt
      * 3 items correct: 0.50 pt
      * 4 items correct: 1.00 pt
      * 0 items correct: 0.00 pt
  - Part III (Short answer / Numeric): 6 questions x 0.5 = 3.0 pts max.
  Total Maximum: 10.00 points.

Given a target score (e.g. 7.5, 8.0, 8.25, 9.0), this planner:
  1. Preserves 100% accuracy on basic (Level 1 / Nhận biết) questions.
  2. Intentionally concedes points on the most difficult questions (Level 4 / Vận dụng cao first, then Level 3).
  3. Generates realistic, natural student errors (e.g., flipping the hardest sub-item in True/False,
     selecting a plausible distractor in multiple choice, or slight calculation offset in numeric).
"""
import copy
import random
from typing import Any, Dict, List, Tuple

import difficulty


def question_max_score(question: Dict[str, Any]) -> float:
    """Return maximum possible points for this question under BGD 2025."""
    kind = question.get('kind', 'single')
    if kind == 'single':
        return 0.25
    if kind == 'truefalse':
        return 1.0
    if kind == 'numeric':
        return 0.5
    return 0.25


def calculate_score(questions: List[Dict[str, Any]]) -> float:
    """Calculate total score from a list of questions with their applied answers."""
    total = 0.0
    for q in questions:
        kind = q.get('kind', 'single')
        correct = q.get('correct_answer', q.get('answer'))
        applied = q.get('applied_answer', q.get('answer'))
        if applied is None or correct is None:
            continue
        if kind == 'single':
            if str(applied).strip().upper() == str(correct).strip().upper():
                total += 0.25
        elif kind == 'truefalse':
            if isinstance(applied, list) and isinstance(correct, list):
                matches = sum(1 for a, c in zip(applied, correct) if bool(a) == bool(c))
                score_map = {0: 0.0, 1: 0.1, 2: 0.25, 3: 0.5, 4: 1.0}
                total += score_map.get(matches, 0.0)
        elif kind == 'numeric':
            if str(applied).strip() == str(correct).strip():
                total += 0.5
    return round(total, 2)


def make_wrong_answer(question: Dict[str, Any], correct_answer: Any, target_sub_points: float = 0.0) -> Any:
    """Generate a realistic, student-like wrong answer for an intentionally missed question."""
    kind = question.get('kind', 'single')

    if kind == 'single':
        # Pick a plausible distractor among available options
        options = question.get('options', [])
        # If options are full strings, labels are usually 'A', 'B', 'C', 'D'
        labels = ['A', 'B', 'C', 'D']
        correct_str = str(correct_answer).strip().upper()
        candidates = [l for l in labels if l != correct_str]
        # Prefer neighbor letters (e.g. if correct is B, A or C is a common calculation mistake)
        return random.choice(candidates) if candidates else ('A' if correct_str != 'A' else 'B')

    if kind == 'truefalse':
        # correct_answer is a list of 4 booleans: [b1, b2, b3, b4]
        if not isinstance(correct_answer, list) or len(correct_answer) != 4:
            return correct_answer

        # Target score dictates how many items to get right:
        # 0.5 pt -> 3 items right (flip 1 item)
        # 0.25 pt -> 2 items right (flip 2 items)
        # 0.1 pt -> 1 item right (flip 3 items)
        # 0.0 pt -> 0 items right (flip all 4)
        wrong = list(correct_answer)
        if target_sub_points >= 0.5:
            # Flip the last sub-item (d), which is almost always the hardest in BGD tests
            wrong[3] = not wrong[3]
        elif target_sub_points >= 0.25:
            # Flip sub-items c and d
            wrong[2] = not wrong[2]
            wrong[3] = not wrong[3]
        elif target_sub_points >= 0.1:
            # Flip sub-items b, c, d (only item a correct)
            wrong[1] = not wrong[1]
            wrong[2] = not wrong[2]
            wrong[3] = not wrong[3]
        else:
            # Flip all
            wrong = [not b for b in wrong]
        return wrong

    if kind == 'numeric':
        # For numeric, a common student mistake: arithmetic sign error, or offset by 1
        correct_str = str(correct_answer).strip()
        try:
            val = float(correct_str.replace(',', '.'))
            if val.is_integer():
                offset = random.choice([-1, 1, 2])
                return str(int(val + offset))
            return str(round(val + 0.5, 2)).replace('.', ',')
        except Exception:
            return ""


def plan_score(questions: List[Dict[str, Any]], target_score: float) -> Tuple[List[Dict[str, Any]], float]:
    """Adjust answers in question list to achieve target_score under BGD 2025 rules.

    Returns:
      (adjusted_questions, projected_score)
    """
    if target_score is None or target_score >= 10.0:
        for q in questions:
            q['correct_answer'] = q.get('correct_answer', q.get('answer'))
            q['applied_answer'] = q['correct_answer']
        return questions, 10.0

    target = max(0.0, min(10.0, round(float(target_score), 2)))

    for q in questions:
        q['correct_answer'] = q.get('correct_answer', q.get('answer'))
        if not q.get('verified_saved'):
            q['applied_answer'] = q['correct_answer']
        if 'difficulty' not in q:
            q['difficulty'] = difficulty.estimate_difficulty(q)

    current_score = calculate_score(questions)
    diff_to_drop = round(current_score - target, 2)
    if diff_to_drop <= 0:
        return questions, current_score

    # Question sacrifice priority (hardest first, never sacrifice already verified questions)
    def sacrifice_priority(q):
        diff = q.get('difficulty', 2)
        kind = q.get('kind', 'single')
        kind_rank = 3 if kind == 'numeric' else (2 if kind == 'truefalse' else 1)
        return (diff, kind_rank, q.get('number', 0))

    sorted_candidates = [q for q in sorted(questions, key=sacrifice_priority, reverse=True) if not q.get('verified_saved')]

    # First pass: try to match quarter points (0.25, 0.75) if diff_to_drop has 0.25 or 0.75
    remainder_quarter = round(diff_to_drop % 0.5, 2)
    if remainder_quarter in (0.25, 0.75):
        # Prefer sacrificing one Level 3/4 single choice (0.25) to align to half points
        single_candidates = [q for q in sorted_candidates if q.get('kind') == 'single' and q.get('difficulty', 1) >= 2]
        if single_candidates:
            pick = single_candidates[0]
            pick['applied_answer'] = make_wrong_answer(pick, pick['correct_answer'], 0.0)
            diff_to_drop = round(diff_to_drop - 0.25, 2)

    # Second pass: drop 0.5 points using numeric or TF (1.0 -> 0.5)
    for q in sorted_candidates:
        if diff_to_drop <= 0.02:
            break
        if q['applied_answer'] != q['correct_answer']:
            continue

        kind = q.get('kind', 'single')
        correct = q['correct_answer']

        if kind == 'numeric' and diff_to_drop >= 0.48:
            q['applied_answer'] = make_wrong_answer(q, correct, 0.0)
            diff_to_drop = round(diff_to_drop - 0.5, 2)
        elif kind == 'truefalse' and diff_to_drop >= 0.48:
            q['applied_answer'] = make_wrong_answer(q, correct, target_sub_points=0.5)
            diff_to_drop = round(diff_to_drop - 0.5, 2)

    # Third pass: drop 0.25 points using remaining single choice or TF
    for q in sorted_candidates:
        if diff_to_drop <= 0.02:
            break
        if q['applied_answer'] != q['correct_answer']:
            continue
        kind = q.get('kind', 'single')
        correct = q['correct_answer']

        if kind == 'single' and diff_to_drop >= 0.23:
            q['applied_answer'] = make_wrong_answer(q, correct, 0.0)
            diff_to_drop = round(diff_to_drop - 0.25, 2)
        elif kind == 'truefalse' and diff_to_drop >= 0.23:
            q['applied_answer'] = make_wrong_answer(q, correct, target_sub_points=0.25)
            diff_to_drop = round(diff_to_drop - 0.25, 2)

    projected = calculate_score(questions)
    return questions, projected
