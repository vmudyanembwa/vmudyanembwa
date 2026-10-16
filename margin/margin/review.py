"""Spaced-repetition scheduling for the daily review.

Readwise-style feedback: after seeing a highlight you say how soon you want to see it again.
Each answer grows the interval by a multiplier, with a floor so early answers still mean something.
Pure functions only, so the behaviour is easy to test and tweak.
"""

from datetime import date, timedelta

# action -> (multiplier on the current interval, minimum interval in days)
ACTIONS = {
    "soon": (1.5, 3),
    "later": (2.5, 14),
    "someday": (5.0, 60),
}
MAX_INTERVAL_DAYS = 365
FAVORITE_FACTOR = 0.5  # favourites come back twice as often


def next_interval(current_days: float, action: str, favorite: bool = False) -> float:
    if action not in ACTIONS:
        raise ValueError(f"unknown review action: {action!r}")
    multiplier, floor = ACTIONS[action]
    interval = max(floor, current_days * multiplier)
    if favorite:
        interval *= FAVORITE_FACTOR
    return round(min(MAX_INTERVAL_DAYS, max(1.0, interval)), 1)


def next_due(today: date, interval_days: float) -> date:
    return today + timedelta(days=round(interval_days))
