"""Per-object rule overrides (spec 27.1, 27.2). Pure logic, no HA imports.

An override always names one subject: an empty scope is never "everything" (27.2). The
global defaults come from the options; an override can disable a rule for one subject or
change its parameters within the documented ranges. DOC005 has no global form: an
enabled DOC005 override *is* the explicit heartbeat contract (8.1, AC DOC 03).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.errors import ErrorCode, SentinelError

# rule -> param -> (min, max); every param is an integer number of seconds or percent.
PARAM_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "DOC001": {"unavailable_s": (30, 3600)},
    "DOC002": {"unknown_s": (60, 86_400)},
    "DOC003": {"flap_threshold": (2, 100)},
    "DOC004": {"low_pct": (1, 99), "clear_pct": (2, 100)},
    "DOC005": {"period_s": (10, 86_400), "threshold_s": (0, 7 * 86_400)},
    # DOC007: weekday bitmask (Mon = bit 0), minute of the local day, tolerance.
    "DOC007": {"days_mask": (1, 127), "minute": (0, 1439), "tolerance_min": (1, 240)},
    "DOC008": {},
    "DOC009": {},
}
REQUIRED_WHEN_ENABLED = {"DOC005": ("period_s",),
                         "DOC007": ("days_mask", "minute", "tolerance_min")}
OVERRIDABLE_RULES = tuple(PARAM_RANGES)


@dataclass(frozen=True, slots=True)
class RuleOverride:
    id: str
    rule_id: str
    subject_uid: str
    enabled: bool
    params: dict[str, int] = field(default_factory=dict)
    policy_revision: int = 1

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule_id, self.subject_uid)


def validate(rule_id: str, enabled: bool, params: dict[str, Any]) -> dict[str, int]:
    """Return normalised params or raise validation_failed with safe details."""
    ranges = PARAM_RANGES.get(rule_id)
    if ranges is None:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "rule cannot be overridden",
                            rule_id=rule_id)
    out: dict[str, int] = {}
    for key, value in params.items():
        if key not in ranges:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "unknown parameter",
                                rule_id=rule_id, param=key)
        lo, hi = ranges[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "parameter out of range",
                                param=key, min=lo, max=hi)
        out[key] = value
    if enabled:
        missing = [k for k in REQUIRED_WHEN_ENABLED.get(rule_id, ()) if k not in out]
        if missing:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "missing parameter",
                                param=missing[0])
    if "low_pct" in out and "clear_pct" in out and out["clear_pct"] <= out["low_pct"]:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "clear threshold must exceed low",
                            param="clear_pct")
    return out


def expectations(overrides: dict[tuple[str, str], RuleOverride]) -> dict[str, dict[str, int]]:
    """subject_uid -> DOC007 params for every enabled expectation."""
    return {o.subject_uid: o.params for o in overrides.values()
            if o.rule_id == "DOC007" and o.enabled}


def heartbeat_contracts(overrides: dict[tuple[str, str], RuleOverride]
                        ) -> dict[str, tuple[int, int]]:
    """subject_uid -> (period_s, manual_threshold_s) for every enabled DOC005 override."""
    return {o.subject_uid: (o.params["period_s"], o.params.get("threshold_s", 0))
            for o in overrides.values() if o.rule_id == "DOC005" and o.enabled}
