"""Sentinel clock: UTC time in microseconds and monotonic time (spec 5.3, 6.1)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now_us(self) -> int:
        """Microseconds since the UTC epoch."""

    def monotonic_ns(self) -> int:
        """Monotonic time for timeouts within a single boot."""


class SystemClock:
    def now_us(self) -> int:
        return time.time_ns() // 1000

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class FakeClock:
    """Controllable clock for domain tests."""

    def __init__(self, start_us: int = 1_792_230_000_000_000) -> None:
        self._now_us = start_us
        self._mono_ns = 0

    def now_us(self) -> int:
        return self._now_us

    def monotonic_ns(self) -> int:
        return self._mono_ns

    def advance(self, seconds: float) -> None:
        delta_us = int(seconds * 1_000_000)
        self._now_us += delta_us
        self._mono_ns += delta_us * 1000


def us_to_rfc3339(value_us: int) -> str:
    """RFC3339 with a Z suffix, as used by the API (spec 6.1)."""
    dt = datetime.fromtimestamp(value_us / 1_000_000, tz=UTC)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def datetime_to_us(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return (value - _EPOCH) // timedelta(microseconds=1)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
