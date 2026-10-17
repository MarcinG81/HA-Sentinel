"""Ingestion: bounded queues, overload policy and coverage gaps (spec 5.2-5.4).

`offer()` is called from the HA callback and costs O(1): it never waits for COMMIT
and never blocks the loop. The writer drains the queues via `drain()`.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field

from ..const import (
    CONTROL_QUEUE_SIZE,
    MAX_PAYLOAD_BUDGET_BYTES,
    TELEMETRY_LAST_SAMPLE_MAP,
    TELEMETRY_QUEUE_SIZE,
    WRITER_BATCH_SIZE,
)
from .clock import Clock
from .models import CoverageSegment, CoverageStatus, Observation


@dataclass(slots=True)
class IngestionStats:
    received_total: int = 0
    dropped_total: int = 0
    rejected_oversize_total: int = 0
    telemetry_coalesced_total: int = 0


@dataclass(slots=True)
class _OpenGap:
    from_us: int
    dropped: int = 0


@dataclass(slots=True)
class DrainResult:
    observations: list[Observation] = field(default_factory=list)
    gaps: list[CoverageSegment] = field(default_factory=list)
    resync_required: bool = False


class IngestionQueues:
    def __init__(
        self,
        clock: Clock,
        *,
        control_size: int = CONTROL_QUEUE_SIZE,
        telemetry_size: int = TELEMETRY_QUEUE_SIZE,
        payload_budget: int = MAX_PAYLOAD_BUDGET_BYTES,
        source_id: str = "ha.states",
    ) -> None:
        self._clock = clock
        self._control: deque[tuple[Observation, int]] = deque()
        # Baseline snapshot of current states (startup and resync). It is not live traffic,
        # so it never counts against the bounded live queues: a large installation would
        # otherwise overflow them on every start and trigger an endless resync loop.
        self._baseline: deque[Observation] = deque()
        self._telemetry: deque[tuple[Observation, int]] = deque()
        self._control_size = control_size
        self._telemetry_size = telemetry_size
        self._payload_budget = payload_budget
        self._payload_bytes = 0
        self._source_id = source_id
        # On telemetry overflow keep only the last sample per subject (bounded map).
        self._last_sample: OrderedDict[str, Observation] = OrderedDict()
        self._control_gap: _OpenGap | None = None
        self.telemetry_degraded = False
        self.stats = IngestionStats()

    @property
    def depth(self) -> int:
        """Live queue depth (what the overload policy and degradation look at)."""
        return len(self._control) + len(self._telemetry)

    @property
    def baseline_pending(self) -> int:
        return len(self._baseline)

    def replace_baseline(self, observations: list[Observation]) -> None:
        """A new snapshot supersedes any part of the previous one not yet processed."""
        self._baseline = deque(observations)
        self.stats.received_total += len(observations)

    @property
    def capacity(self) -> int:
        return self._control_size + self._telemetry_size

    @property
    def degraded(self) -> bool:
        return self._control_gap is not None or self.telemetry_degraded

    def offer(self, obs: Observation) -> bool:
        """Accept an observation; False means it was dropped or coalesced."""
        self.stats.received_total += 1
        try:
            size = len(obs.payload_json().encode())
        except ValueError:  # over 8 KiB or non-finite numbers
            self.stats.rejected_oversize_total += 1
            return False

        if obs.kind.is_control:
            over_budget = self._payload_bytes + size > self._payload_budget
            if len(self._control) >= self._control_size or over_budget:
                self.stats.dropped_total += 1
                if self._control_gap is None:
                    self._control_gap = _OpenGap(from_us=self._clock.now_us())
                self._control_gap.dropped += 1
                return False
            self._control.append((obs, size))
            self._payload_bytes += size
            return True

        over_budget = self._payload_bytes + size > self._payload_budget
        if len(self._telemetry) >= self._telemetry_size or over_budget:
            self.telemetry_degraded = True
            self.stats.dropped_total += 1
            self.stats.telemetry_coalesced_total += 1
            key = obs.subject_uid or ""
            if key in self._last_sample:
                self._last_sample.move_to_end(key)
            elif len(self._last_sample) >= TELEMETRY_LAST_SAMPLE_MAP:
                self._last_sample.popitem(last=False)
            self._last_sample[key] = obs
            return False
        self._telemetry.append((obs, size))
        self._payload_bytes += size
        return True

    def drain(self, limit: int = WRITER_BATCH_SIZE) -> DrainResult:
        """Take a batch; control observations take priority over telemetry."""
        result = DrainResult()
        while self._baseline and len(result.observations) < limit:
            result.observations.append(self._baseline.popleft())
        while self._control and len(result.observations) < limit:
            obs, size = self._control.popleft()
            self._payload_bytes -= size
            result.observations.append(obs)
        while self._telemetry and len(result.observations) < limit:
            obs, size = self._telemetry.popleft()
            self._payload_bytes -= size
            result.observations.append(obs)

        if self._control_gap is not None and len(self._control) < self._control_size // 2:
            # Recovered: write a single aggregate gap record and resync current states.
            gap = self._control_gap
            self._control_gap = None
            result.gaps.append(
                CoverageSegment(
                    source_id=self._source_id,
                    scope="control",
                    from_us=gap.from_us,
                    to_us=self._clock.now_us(),
                    status=CoverageStatus.MISSING,
                    reason=f"queue_overflow dropped={gap.dropped}",
                )
            )
            result.resync_required = True

        if self.telemetry_degraded and not self._telemetry and len(result.observations) < limit:
            while self._last_sample and len(result.observations) < limit:
                _, obs = self._last_sample.popitem(last=False)
                result.observations.append(obs)
            if not self._last_sample:
                self.telemetry_degraded = False
        return result
