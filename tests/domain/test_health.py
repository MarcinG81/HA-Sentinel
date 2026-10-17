"""Health score: AC DOC 05 and coverage threshold."""

from decimal import Decimal

from custom_components.ha_sentinel.core.models import Finding, FindingStatus, Severity
from custom_components.ha_sentinel.modules.doctor.health import (
    HealthUnit,
    compute_health,
    round_half_up,
)


def _finding(subject: str, severity: Severity, status=FindingStatus.ACTIVE) -> Finding:
    return Finding(id=subject + severity, rule_id="DOC001", rule_version=1, subject_uid=subject,
                   status=status, severity=severity, first_seen_us=0, last_seen_us=0)


def test_ac_doc_05_weights_and_half_up():
    units = [
        HealthUnit("u1", frozenset({"e1"}), 1),
        HealthUnit("u2", frozenset({"e2"}), 1),
        HealthUnit("u3", frozenset({"e3"}), 2),
    ]
    findings = [_finding("e1", Severity.CRITICAL), _finding("e3", Severity.WARNING)]
    r = compute_health(units, findings)
    assert r.score_exact == Decimal("62.5")
    assert r.score == 63


def test_round_half_up_not_bankers():
    assert round_half_up(Decimal("62.5")) == 63
    assert round(62.5) == 62  # documents why Python round() is not used


def test_unit_takes_max_penalty_not_sum():
    units = [HealthUnit("dev", frozenset({"a", "b"}))]
    findings = [_finding("a", Severity.WARNING), _finding("b", Severity.HIGH),
                _finding("b", Severity.WARNING)]
    assert compute_health(units, findings).score == 40


def test_low_coverage_publishes_no_complete_score():
    units = [HealthUnit(f"u{i}", frozenset({f"e{i}"}), covered=i < 7) for i in range(10)]
    r = compute_health(units, [])
    assert r.coverage_pct == Decimal(70)
    assert r.score is None and r.score_exact == Decimal(100)


def test_maintenance_excluded_but_resolved_findings_ignored():
    units = [HealthUnit("u1", frozenset({"e1"})), HealthUnit("u2", frozenset({"e2"}))]
    findings = [_finding("e1", Severity.CRITICAL),
                _finding("e2", Severity.CRITICAL, FindingStatus.RESOLVED)]
    r = compute_health(units, findings, excluded_subjects={"u1": "maintenance"})
    assert r.score == 100 and r.units_total == 1


def test_empty_denominator_is_unknown():
    assert compute_health([], []).score is None
