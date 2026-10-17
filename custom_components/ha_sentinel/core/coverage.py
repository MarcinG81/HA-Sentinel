"""Coverage tracking (spec 5.4). Missing/partial/complete describe data, not home health."""

from __future__ import annotations

from .models import CoverageSegment


class CoverageTracker:
    """Knows since when a source is observed and which time ranges are gaps."""

    def __init__(self, monitoring_since_us: int) -> None:
        self.monitoring_since_us = monitoring_since_us
        self._gaps: list[CoverageSegment] = []
        self._open_gap_from_us: int | None = None

    def add_gap(self, segment: CoverageSegment) -> None:
        self._gaps.append(segment)

    def open_gap(self, from_us: int) -> None:
        if self._open_gap_from_us is None:
            self._open_gap_from_us = from_us

    def close_gap(self) -> None:
        self._open_gap_from_us = None

    @property
    def gaps(self) -> list[CoverageSegment]:
        return list(self._gaps)

    def prune(self, before_us: int) -> None:
        self._gaps = [g for g in self._gaps if g.to_us is None or g.to_us >= before_us]

    def is_complete(self, from_us: int, to_us: int) -> bool:
        if from_us < self.monitoring_since_us:
            return False
        if self._open_gap_from_us is not None and self._open_gap_from_us <= to_us:
            return False
        for g in self._gaps:
            g_end = g.to_us if g.to_us is not None else to_us
            if g.from_us <= to_us and g_end >= from_us:
                return False
        return True

    def complete_at_us(self, window_us: int, now_us: int) -> int | None:
        """When [t - window, t] will first contain no gap; None if already complete or
        a gap is still open (the end is unknown)."""
        if self._open_gap_from_us is not None:
            return None
        start = self.monitoring_since_us
        for g in self._gaps:
            if g.to_us is None:
                return None
            start = max(start, g.to_us)
        ready = start + window_us
        return ready if ready > now_us else None
