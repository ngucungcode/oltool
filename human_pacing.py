"""Realistic human pacing and deliberation simulation for Onluyen homework.

Simulates authentic high school student behavior:
  - Reading the question and mathematical expressions.
  - Scratchpad thinking / calculation pauses (can take up to 5 minutes for difficult Level 4 questions).
  - Natural mouse wander and occasional scrolling.
  - Granular pauses between evaluating sub-items (a, b, c, d) in True/False.
  - Review pauses before committing the answer.
  - Safety countdown watchdog: never allows time to expire or get dangerously close to test deadline.
"""
import asyncio
import math
import random
from typing import Any, Dict, Optional

# Timing profiles in seconds per question based on difficulty (1-4)
PACING_PROFILES = {
    # Full realistic student pace (up to 5+ minutes for Level 4)
    'stealth': {
        1: (45, 90),     # Nhận biết: 0.75 - 1.5 mins
        2: (90, 180),    # Thông hiểu: 1.5 - 3.0 mins
        3: (180, 270),   # Vận dụng: 3.0 - 4.5 mins
        4: (240, 320),   # Vận dụng cao: 4.0 - 5.3 mins
    },
    # Balanced realistic pace (total ~25 - 40 mins)
    'balanced': {
        1: (20, 45),     # 0.3 - 0.75 mins
        2: (45, 90),     # 0.75 - 1.5 mins
        3: (90, 150),    # 1.5 - 2.5 mins
        4: (120, 200),   # 2.0 - 3.3 mins
    },
    # Fast pace for automated tests and validation
    'fast': {
        1: (1, 3),
        2: (2, 4),
        3: (2, 5),
        4: (3, 6),
    }
}


def calculate_deliberation_seconds(difficulty_level: int, mode: str = 'stealth', remaining_test_seconds: Optional[int] = None) -> float:
    """Calculate target thinking time in seconds for a question."""
    profile = PACING_PROFILES.get(mode, PACING_PROFILES['stealth'])
    diff = max(1, min(4, int(difficulty_level)))
    min_s, max_s = profile.get(diff, (45, 120))
    target = random.uniform(min_s, max_s)

    # Watchdog: if test is running out of time, ensure we finish with at least 180s buffer
    if remaining_test_seconds is not None and remaining_test_seconds > 0:
        safe_budget = max(5.0, (remaining_test_seconds - 180) / 25.0)
        target = min(target, safe_budget)

    return round(target, 1)


async def simulate_human_deliberation(page, duration_seconds: float):
    """Simulate authentic human interaction during question thinking time."""
    if duration_seconds <= 2.0:
        await asyncio.sleep(duration_seconds)
        return

    end_time = asyncio.get_running_loop().time() + duration_seconds

    # Phase 1: Initial reading (first 20% of duration)
    reading_time = duration_seconds * random.uniform(0.15, 0.25)
    await asyncio.sleep(reading_time)

    # Phase 2: Thinking & occasional page interactions
    while asyncio.get_running_loop().time() < end_time - 3.0:
        remaining = end_time - asyncio.get_running_loop().time()
        chunk = min(remaining - 2.0, random.uniform(6.0, 18.0))
        if chunk <= 0.5:
            break

        action = random.random()
        if action < 0.35:
            # Subtle mouse movement simulating following text
            try:
                viewport = page.viewport_size or {'width': 1280, 'height': 800}
                x = random.randint(150, viewport['width'] - 150)
                y = random.randint(150, viewport['height'] - 150)
                await page.mouse.move(x, y, steps=random.randint(5, 15))
            except Exception:
                pass
        elif action < 0.60:
            # Subtle scroll down then back up to read long formulas / options
            try:
                scroll_delta = random.choice([80, 150, -80, -120])
                await page.mouse.wheel(0, scroll_delta)
            except Exception:
                pass

        await asyncio.sleep(chunk)

    # Remainder sleep before answer commitment
    rem = end_time - asyncio.get_running_loop().time()
    if rem > 0:
        await asyncio.sleep(rem)
