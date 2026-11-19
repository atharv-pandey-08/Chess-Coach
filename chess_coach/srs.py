"""Spaced repetition for puzzles: a simplified SM-2 (the algorithm behind Anki).

A puzzle you solve comes back after 1 day, then 3, then the previous gap times an
"ease" factor that grows when you keep getting it right. A miss brings it back in
ten minutes and lowers the ease, so the positions you struggle with come round
more often than the ones you've learned.
"""

from __future__ import annotations

from dataclasses import dataclass

DAY = 86_400
RETRY_SECONDS = 600
MIN_EASE, MAX_EASE = 1.3, 3.0


@dataclass
class Card:
    interval_d: float = 0.0
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0


def review(card: Card, solved: bool, now: int) -> tuple[Card, int]:
    """The card's new state and the unix time it's due next."""
    if solved:
        reps = card.reps + 1
        interval = 1.0 if reps == 1 else 3.0 if reps == 2 else round(card.interval_d * card.ease, 1)
        new = Card(interval, min(MAX_EASE, card.ease + 0.05), reps, card.lapses)
        return new, now + int(interval * DAY)
    new = Card(0.0, max(MIN_EASE, card.ease - 0.2), 0, card.lapses + 1)
    return new, now + RETRY_SECONDS
