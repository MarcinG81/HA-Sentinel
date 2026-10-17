"""Restart policy for failing modules (spec 4.3). Pure logic.

Retries after 5, 15 and 60 s with ±20 % jitter; five failures within ten minutes pause
the module until an admin decides.
"""

from __future__ import annotations

import random
from collections import deque

BACKOFF_S = (5, 15, 60)
WINDOW_NS = 10 * 60 * 1_000_000_000
MAX_FAILURES = 5


class Supervisor:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._failures: deque[int] = deque()
        self._rng = rng or random.Random()

    def failed(self, now_ns: int) -> float | None:
        """Register a failure; returns the delay before retrying, or None to pause."""
        self._failures.append(now_ns)
        while self._failures and now_ns - self._failures[0] > WINDOW_NS:
            self._failures.popleft()
        if len(self._failures) >= MAX_FAILURES:
            return None
        base = BACKOFF_S[min(len(self._failures), len(BACKOFF_S)) - 1]
        return base * self._rng.uniform(0.8, 1.2)

    def reset(self) -> None:
        self._failures.clear()
