"""Probe scheduling, global limits and status state machine (spec 13.3). Pure logic.

One limiter is shared by periodic and manual scans (AC NET 05): at most 30 new
connections per minute, 4 in parallel and one attempt per target per minute.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

MAX_PER_MINUTE = 30
MAX_PARALLEL = 4
PER_TARGET_INTERVAL_NS = 60 * 1_000_000_000
WINDOW_NS = 60 * 1_000_000_000
FAILURES_TO_UNREACHABLE = 3
SUCCESSES_TO_RECOVER = 2


class Outcome(StrEnum):
    OPEN = "open"  # TCP accepted / HTTP answered
    REFUSED = "host_reachable_service_closed"
    TIMEOUT = "unknown_or_filtered"
    DNS_ERROR = "dns_error"
    BLOCKED = "blocked_by_policy"
    ERROR = "error"


class Status(StrEnum):
    UNKNOWN = "unknown"
    REACHABLE = "reachable"
    SERVICE_CLOSED = "service_closed"
    UNREACHABLE = "unreachable"


@dataclass(slots=True)
class TargetState:
    status: Status = Status.UNKNOWN
    failures: int = 0
    successes: int = 0
    last_attempt_ns: int | None = None
    last_outcome: Outcome | None = None
    unreachable_since_us: int | None = None


class ProbeLimiter:
    def __init__(self) -> None:
        self._starts: deque[int] = deque()
        self.in_flight = 0

    def try_acquire(self, now_ns: int) -> bool:
        while self._starts and now_ns - self._starts[0] >= WINDOW_NS:
            self._starts.popleft()
        if self.in_flight >= MAX_PARALLEL or len(self._starts) >= MAX_PER_MINUTE:
            return False
        self._starts.append(now_ns)
        self.in_flight += 1
        return True

    def release(self) -> None:
        self.in_flight = max(0, self.in_flight - 1)


@dataclass(slots=True)
class Transition:
    became_unreachable: bool = False
    recovered: bool = False


@dataclass(slots=True)
class ProbeBook:
    targets: dict[str, TargetState] = field(default_factory=dict)

    def due(self, target_id: str, now_ns: int) -> bool:
        st = self.targets.setdefault(target_id, TargetState())
        return st.last_attempt_ns is None or now_ns - st.last_attempt_ns >= PER_TARGET_INTERVAL_NS

    def record(self, target_id: str, outcome: Outcome, now_ns: int, now_us: int) -> Transition:
        st = self.targets.setdefault(target_id, TargetState())
        st.last_attempt_ns = now_ns
        st.last_outcome = outcome
        tr = Transition()
        if outcome in (Outcome.OPEN, Outcome.REFUSED):
            # A refused port proves the host answers; it is not a network failure.
            st.failures = 0
            st.successes += 1
            if st.status is Status.UNREACHABLE and st.successes >= SUCCESSES_TO_RECOVER:
                tr.recovered = True
                st.unreachable_since_us = None
            if st.status is not Status.UNREACHABLE or tr.recovered:
                st.status = Status.REACHABLE if outcome is Outcome.OPEN else Status.SERVICE_CLOSED
        elif outcome in (Outcome.TIMEOUT, Outcome.DNS_ERROR, Outcome.ERROR):
            st.successes = 0
            st.failures += 1
            if st.status is not Status.UNREACHABLE and st.failures >= FAILURES_TO_UNREACHABLE:
                st.status = Status.UNREACHABLE
                st.unreachable_since_us = now_us
                tr.became_unreachable = True
        return tr
