"""Health score H and coverage C (spec 8.3). Pure logic.

The scoring unit is a device; entities without a device are their own units. Each unit
takes the largest penalty among its active, non-excluded findings - findings are never
summed. H is a Sentinel indicator, not a measure of home safety.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ...const import COVERAGE_MIN_PCT
from ...core.models import Finding, FindingStatus, Severity

PENALTY: dict[Severity, Decimal] = {
    Severity.CRITICAL: Decimal(1),
    Severity.HIGH: Decimal("0.6"),
    Severity.WARNING: Decimal("0.25"),
    Severity.INFO: Decimal(0),
}


@dataclass(slots=True)
class HealthUnit:
    uid: str
    entity_uids: frozenset[str]
    weight: int = 1
    covered: bool = True  # valid baseline and continuous observation in the last 15 min


@dataclass(slots=True)
class HealthResult:
    score_exact: Decimal | None
    score: int | None  # None when coverage is insufficient: the sensor reports unknown
    coverage_pct: Decimal | None
    units_total: int
    components: list[dict] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.score is not None


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compute_health(
    units: Iterable[HealthUnit],
    findings: Iterable[Finding],
    excluded_subjects: Mapping[str, str] | None = None,
) -> HealthResult:
    """Compute H and C. `excluded_subjects` maps uid -> reason (e.g. planned maintenance).

    Muted notifications are NOT exclusions: a muted failure stays in the denominator.
    """
    excluded = excluded_subjects or {}
    by_subject: dict[str, Decimal] = {}
    for f in findings:
        if f.status is not FindingStatus.ACTIVE or f.subject_uid in excluded:
            continue
        p = PENALTY[f.severity]
        if p > by_subject.get(f.subject_uid, Decimal(-1)):
            by_subject[f.subject_uid] = p

    sum_w = Decimal(0)
    sum_wp = Decimal(0)
    covered_w = Decimal(0)
    components: list[dict] = []
    total = 0
    for unit in units:
        if unit.uid in excluded:
            components.append({"unit": unit.uid, "excluded": excluded[unit.uid]})
            continue
        if not 1 <= unit.weight <= 5:
            raise ValueError("unit weight must be within 1..5")
        total += 1
        w = Decimal(unit.weight)
        penalty = max((by_subject.get(e, Decimal(0)) for e in unit.entity_uids),
                      default=Decimal(0))
        sum_w += w
        sum_wp += w * penalty
        if unit.covered:
            covered_w += w
        components.append({"unit": unit.uid, "weight": unit.weight,
                           "penalty": str(penalty), "covered": unit.covered})

    if sum_w == 0:
        return HealthResult(None, None, None, 0, components)
    exact = Decimal(100) * (Decimal(1) - sum_wp / sum_w)
    coverage = Decimal(100) * covered_w / sum_w
    score = round_half_up(exact) if coverage >= COVERAGE_MIN_PCT else None
    return HealthResult(exact, score, coverage, total, components)
