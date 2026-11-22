"""Daily caps on AI coach calls, so a public demo can't run through the Gemini quota.

Two budgets reset every UTC day: one per visitor (by IP address) and one for the whole
server. Only real model calls count; explanations served from the cache are free.
A limit of 0 means "no cap" (the default when running locally).
"""

from __future__ import annotations

import threading
import time


class DailyLimiter:
    def __init__(self, per_client: int, total: int, clock=time.time) -> None:
        self.per_client = per_client
        self.total = total
        self._clock = clock
        self._lock = threading.Lock()
        self._day = -1
        self._used: dict[str, int] = {}
        self._used_total = 0

    def _roll(self) -> None:
        day = int(self._clock() // 86_400)
        if day != self._day:
            self._day, self._used, self._used_total = day, {}, 0

    def take(self, client: str) -> str | None:
        """Use one call for `client`. Returns None if allowed, else the reason it isn't."""
        with self._lock:
            self._roll()
            if self.total and self._used_total >= self.total:
                return "server"
            if self.per_client and self._used.get(client, 0) >= self.per_client:
                return "client"
            self._used[client] = self._used.get(client, 0) + 1
            self._used_total += 1
            return None

    def remaining(self, client: str) -> int | None:
        """Calls left today for `client`, or None when there's no cap."""
        with self._lock:
            self._roll()
            left = []
            if self.per_client:
                left.append(self.per_client - self._used.get(client, 0))
            if self.total:
                left.append(self.total - self._used_total)
            return max(0, min(left)) if left else None
