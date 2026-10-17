"""Explicit numeric sources (spec 5.1, 8.1 DOC004/DOC008, 27.1). Pure logic, no HA imports.

Sentinel never guesses what a number means from an entity name. A numeric source is an
admin's statement "this entity is a battery level on scale [min, max]" or "this entity is
a radio signal on scale [min, max], weak below threshold". Signal values of different
technologies (RSSI in dBm, LQI 0-255) are never compared with each other: every source
carries its own scale and threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ...core.errors import ErrorCode, SentinelError

PURPOSES = ("battery", "signal")
SCALE_LIMIT = 1_000_000.0
# Signal recovery needs the value back above threshold + 5 % of the scale (hysteresis).
SIGNAL_HYSTERESIS = 0.05


@dataclass(frozen=True, slots=True)
class NumericSource:
    id: str
    subject_uid: str
    purpose: str
    scale_min: float
    scale_max: float
    threshold: float | None = None

    def payload(self, raw: str) -> dict[str, Any] | None:
        """Observation payload for a raw HA state, or None if it is not a number."""
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        if self.purpose == "battery":
            pct = (value - self.scale_min) / (self.scale_max - self.scale_min) * 100
            return {"value": round(min(100.0, max(0.0, pct)), 1), "unit": "%",
                    "device_class": "battery", "mapping": "manual"}
        return {"value": value, "purpose": "signal", "threshold": self.threshold,
                "scale": [self.scale_min, self.scale_max]}


def validate(purpose: str, scale_min: float, scale_max: float,
             threshold: float | None) -> None:
    if purpose not in PURPOSES:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "unknown purpose", param="purpose")
    for name, value in (("scale_min", scale_min), ("scale_max", scale_max),
                        ("threshold", threshold)):
        if value is not None and (not math.isfinite(value) or abs(value) > SCALE_LIMIT):
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "value out of range", param=name)
    if scale_max <= scale_min:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "scale_max must exceed scale_min",
                            param="scale_max")
    if purpose == "signal":
        if threshold is None:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "missing parameter",
                                param="threshold")
        if not scale_min < threshold < scale_max:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "threshold outside the scale",
                                param="threshold")
    elif threshold is not None:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "battery uses the DOC004 thresholds",
                            param="threshold")
