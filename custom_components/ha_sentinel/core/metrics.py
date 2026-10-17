"""Sentinel metrics (spec 20.1). In-memory, bounded, low-cardinality labels only.

Never label metrics with entity_id, IP addresses or incident ids. Histograms keep a
fixed-size reservoir of the most recent samples, so memory is constant.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

RESERVOIR = 2048


@dataclass(slots=True)
class Histogram:
    unit: str
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=RESERVOIR))
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0

    def observe(self, value: float) -> None:
        self.samples.append(value)
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)

    def percentile(self, q: float) -> float | None:
        if not self.samples:
            return None
        ordered = sorted(self.samples)
        idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[idx]

    def summary(self) -> dict[str, float | int | str | None]:
        return {
            "unit": self.unit,
            "count": self.count,
            "p50": _round(self.percentile(0.50)),
            "p95": _round(self.percentile(0.95)),
            "p99": _round(self.percentile(0.99)),
            "max": _round(self.maximum) if self.count else None,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.histograms: dict[str, Histogram] = {
            "ingestion_callback_us": Histogram("us"),
            "ingestion_lag_ms": Histogram("ms"),
            "writer_commit_ms": Histogram("ms"),
            "rules_duration_ms": Histogram("ms"),
            "correlation_ms": Histogram("ms"),
            "api_latency_ms": Histogram("ms"),
        }
        self.queue_depth_max = 0

    def inc(self, name: str, value: int = 1, label: str | None = None) -> None:
        key = f"{name}{{{label}}}" if label else name
        self.counters[key] = self.counters.get(key, 0) + value

    def observe(self, name: str, value: float) -> None:
        self.histograms[name].observe(value)

    def snapshot(self) -> dict[str, object]:
        return {
            "counters": dict(sorted(self.counters.items())),
            "histograms": {k: h.summary() for k, h in self.histograms.items()},
            "queue_depth_max": self.queue_depth_max,
        }
