"""Per-user API rate limits (spec 15.3): 60 queries/min and 10 mutations/min."""

from __future__ import annotations

from collections import deque
from enum import StrEnum

from .clock import Clock

WINDOW_NS = 60 * 1_000_000_000


class RequestClass(StrEnum):
    QUERY = "query"
    MUTATION = "mutation"


LIMITS = {RequestClass.QUERY: 60, RequestClass.MUTATION: 10}


class RateLimiter:
    """Sliding one-minute window on the monotonic clock."""

    def __init__(self, clock: Clock, limits: dict[RequestClass, int] | None = None) -> None:
        self._clock = clock
        self._limits = limits or LIMITS
        self._hits: dict[tuple[str, RequestClass], deque[int]] = {}

    def allow(self, user_id: str, cls: RequestClass) -> bool:
        now = self._clock.monotonic_ns()
        hits = self._hits.setdefault((user_id, cls), deque())
        while hits and now - hits[0] >= WINDOW_NS:
            hits.popleft()
        if len(hits) >= self._limits[cls]:
            return False
        hits.append(now)
        return True
