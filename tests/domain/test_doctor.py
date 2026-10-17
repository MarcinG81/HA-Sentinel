"""Doctor rules: T04, T05, T06, AC DOC 01-04."""

from __future__ import annotations

from custom_components.ha_sentinel.core.coverage import CoverageTracker
from custom_components.ha_sentinel.core.models import (
    CoverageSegment,
    CoverageStatus,
    Finding,
    FindingStatus,
    Severity,
)
from custom_components.ha_sentinel.modules.doctor.engine import DoctorEngine, DoctorParams

from .helpers import S, avail, battery, heartbeat, snapshot

T0 = 1_792_230_000_000_000
UID = "6a1b3a2e-1f8e-4c69-9d55-0d1f5b8e2a01"


def engine(**kw) -> DoctorEngine:
    return DoctorEngine(boot_started_us=T0, **kw)


def run(e: DoctorEngine, until_us: int):
    return e.tick(until_us)


def test_t04_unavailable_119s_no_finding_120s_one_finding():
    e = engine()
    t = T0 + 600 * S
    assert e.process(snapshot(UID, "available", T0)) == []
    assert e.process(avail(UID, "unavailable", t, "available")) == []
    assert run(e, t + 119 * S) == []
    out = run(e, t + 120 * S)
    assert [(f.rule_id, f.status) for f in out] == [("DOC001", FindingStatus.ACTIVE)]
    assert out[0].first_seen_us == t  # outage counted from the first observation
    assert run(e, t + 1000 * S) == []  # exactly one finding


def test_return_within_119s_cancels_pending():
    e = engine()
    t = T0 + 600 * S
    e.process(snapshot(UID, "available", T0))
    e.process(avail(UID, "unavailable", t))
    e.process(avail(UID, "available", t + 119 * S))
    assert run(e, t + 3600 * S) == []


def test_recovery_needs_30s_stability_and_short_return_keeps_finding():
    e = engine()
    t = T0 + 600 * S
    e.process(snapshot(UID, "available", T0))
    e.process(avail(UID, "unavailable", t))
    assert len(run(e, t + 120 * S)) == 1
    e.process(avail(UID, "available", t + 200 * S))
    e.process(avail(UID, "unavailable", t + 220 * S))  # back before 30 s
    assert run(e, t + 400 * S) == []
    assert len(e.active_findings()) == 1
    e.process(avail(UID, "available", t + 500 * S))
    assert run(e, t + 529 * S) == []
    out = run(e, t + 530 * S)
    assert [(f.rule_id, f.status) for f in out] == [("DOC001", FindingStatus.RESOLVED)]


def test_ac_doc_04_no_alarm_wave_during_startup_grace():
    e = engine()
    e.process(snapshot(UID, "unavailable", T0 + 1 * S))
    assert run(e, T0 + 120 * S) == []
    out = run(e, T0 + 121 * S)
    assert len(out) == 1
    assert out[0].coverage is CoverageStatus.PARTIAL  # start known only from snapshot


def test_unknown_is_a_separate_category():
    e = engine()
    t = T0 + 600 * S
    e.process(snapshot(UID, "available", T0))
    e.process(avail(UID, "unknown", t))
    assert run(e, t + 599 * S) == []
    assert [f.rule_id for f in run(e, t + 600 * S)] == ["DOC002"]


def _flap(e: DoctorEngine, start_us: int, n: int, step_s: int = 300):
    out = []
    for i in range(n):
        at = start_us + i * step_s * S
        out += e.process(avail(UID, "unavailable", at))
        out += e.process(avail(UID, "available", at + 20 * S))
    return out


def test_ac_doc_02_six_entries_in_60_min_with_full_coverage():
    cov = CoverageTracker(monitoring_since_us=T0)
    e = engine(coverage_complete=cov.is_complete)
    start = T0 + 3700 * S
    e.process(snapshot(UID, "available", T0))
    out = _flap(e, start, 5)
    assert [f for f in out if f.rule_id == "DOC003"] == []
    out = _flap(e, start + 5 * 300 * S, 1)
    assert [f.rule_id for f in out if f.rule_id == "DOC003"] == ["DOC003"]


def test_ac_doc_02_transport_duplicates_do_not_count():
    cov = CoverageTracker(monitoring_since_us=T0)
    e = engine(coverage_complete=cov.is_complete)
    start = T0 + 3700 * S
    e.process(snapshot(UID, "available", T0))
    for i in range(5):
        at = start + i * 300 * S
        dup = avail(UID, "unavailable", at)
        e.process(dup)
        e.process(dup)  # same observation delivered twice
        e.process(avail(UID, "available", at + 20 * S))
    assert [f for f in e.active_findings() if f.rule_id == "DOC003"] == []


def test_t05_flapping_with_gap_is_partial_not_a_finding():
    cov = CoverageTracker(monitoring_since_us=T0)
    start = T0 + 3700 * S
    cov.add_gap(CoverageSegment("ha.states", "control", start + 100 * S, start + 160 * S,
                                CoverageStatus.MISSING, "queue_overflow"))
    e = engine(coverage_complete=cov.is_complete)
    e.process(snapshot(UID, "available", T0))
    out = _flap(e, start, 6)
    assert [f for f in out if f.rule_id == "DOC003"] == []
    assert UID in e.flap_partial


def test_flapping_resolves_when_window_slides():
    cov = CoverageTracker(monitoring_since_us=T0)
    e = engine(coverage_complete=cov.is_complete)
    start = T0 + 3700 * S
    e.process(snapshot(UID, "available", T0))
    _flap(e, start, 6)
    assert any(f.rule_id == "DOC003" for f in e.active_findings())
    out = run(e, start + 3600 * S + 1)
    assert [(f.rule_id, f.status) for f in out] == [("DOC003", FindingStatus.RESOLVED)]


def test_doc004_battery_two_samples_30_min_apart_and_hysteresis():
    e = engine()
    t = T0 + 600 * S
    assert e.process(battery(UID, 15, t)) == []
    assert e.process(battery(UID, 14, t + 29 * 60 * S)) == []
    out = e.process(battery(UID, 13, t + 30 * 60 * S))
    assert [f.rule_id for f in out] == ["DOC004"]
    assert len(out[0].evidence_ids) == 2
    assert e.process(battery(UID, 24, t + 40 * 60 * S)) == []  # 20..25 does not clear
    out = e.process(battery(UID, 26, t + 50 * 60 * S))
    assert [(f.rule_id, f.status) for f in out] == [("DOC004", FindingStatus.RESOLVED)]


def test_doc004_zero_percent_is_valid_and_non_percent_ignored():
    e = engine()
    t = T0 + 600 * S
    e.process(battery(UID, 0, t))
    assert len(e.process(battery(UID, 0, t + 1800 * S))) == 1
    other = "6a1b3a2e-1f8e-4c69-9d55-0d1f5b8e2a02"
    e.process(battery(other, 3.1, t, unit="V"))
    assert e.process(battery(other, 3.0, t + 1800 * S, unit="V")) == []


def test_t06_no_stale_without_heartbeat_contract():
    e = engine()
    e.process(snapshot(UID, "available", T0))
    assert run(e, T0 + 86400 * S) == []


def test_doc005_stale_with_explicit_heartbeat():
    e = engine(params=DoctorParams(heartbeats={UID: (60, 0)}))
    e.process(snapshot(UID, "available", T0))
    assert run(e, T0 + 179 * S) == []
    assert [f.rule_id for f in run(e, T0 + 180 * S)] == ["DOC005"]
    out = e.process(avail(UID, "unavailable", T0 + 200 * S))
    assert "DOC005" not in [f.rule_id for f in out]  # unavailable is not a report
    out = e.process(heartbeat(UID, T0 + 210 * S))
    assert ("DOC005", FindingStatus.RESOLVED) in [(f.rule_id, f.status) for f in out]


def test_coverage_warmup_completion_time():
    from custom_components.ha_sentinel.core.coverage import CoverageTracker

    window = 900 * S
    cov = CoverageTracker(monitoring_since_us=T0)
    assert cov.complete_at_us(window, T0 + 60 * S) == T0 + window
    assert cov.complete_at_us(window, T0 + window) is None  # already complete
    cov.add_gap(CoverageSegment("ha.states", "control", T0 + 1000 * S, T0 + 1100 * S,
                                CoverageStatus.MISSING, "restart"))
    assert cov.complete_at_us(window, T0 + 1200 * S) == T0 + 1100 * S + window
    cov.open_gap(T0 + 2000 * S)
    assert cov.complete_at_us(window, T0 + 2100 * S) is None


def test_doc002_needs_a_transition_from_a_known_state():
    e = engine()
    e.process(snapshot(UID, "unknown", T0))  # unknown since start: not evidence
    assert run(e, T0 + 3600 * S) == []
    e.process(avail(UID, "available", T0 + 3700 * S))
    e.process(avail(UID, "unknown", T0 + 3800 * S))
    assert [f.rule_id for f in run(e, T0 + 4400 * S)] == ["DOC002"]


def test_doc002_ignores_stateless_domains():
    e = DoctorEngine(boot_started_us=T0, domain_of=lambda uid: "conversation")
    e.process(snapshot(UID, "available", T0))
    e.process(avail(UID, "unknown", T0 + 600 * S))
    assert run(e, T0 + 3600 * S) == []


def test_restored_findings_of_vanished_subjects_are_retired_explicitly():
    old = Finding(id="f-old", rule_id="DOC002", rule_version=1, subject_uid="gone-uid",
                  status=FindingStatus.ACTIVE, severity=Severity.WARNING,
                  first_seen_us=T0 - 10, last_seen_us=T0 - 10)
    kept = Finding(id="f-kept", rule_id="DOC001", rule_version=1, subject_uid=UID,
                   status=FindingStatus.ACTIVE, severity=Severity.HIGH,
                   first_seen_us=T0 - 10, last_seen_us=T0 - 10)
    e = engine()
    e.restore([old, kept])
    e.process(snapshot(UID, "unavailable", T0))
    retired = e.retire_stale(T0 + 1)
    assert [f.id for f in retired] == ["f-old"]
    assert retired[0].details["resolution"] == "subject_identity_ended"
    assert [f.id for f in e.active_findings()] == ["f-kept"]


def test_restored_findings_of_revised_rules_are_retired():
    def doc002(fid, uid, version):
        return Finding(id=fid, rule_id="DOC002", rule_version=version, subject_uid=uid,
                       status=FindingStatus.ACTIVE, severity=Severity.WARNING,
                       first_seen_us=T0 - 10, last_seen_us=T0 - 10)

    e = DoctorEngine(boot_started_us=T0,
                     domain_of=lambda uid: "conversation" if uid == "conv" else "sensor")
    e.restore([doc002("legacy", UID, 1), doc002("stateless", "conv", 2),
               doc002("current", "other", 2)])
    for uid in (UID, "conv", "other"):
        e.process(snapshot(uid, "unknown", T0))
    retired = {f.id: f.details["resolution"] for f in e.retire_stale(T0 + 1)}
    assert retired == {"legacy": "rule_revised", "stateless": "rule_revised"}
    assert [f.id for f in e.active_findings()] == ["current"]


def _override(rule, uid, enabled=True, **params):
    from custom_components.ha_sentinel.modules.doctor.overrides import RuleOverride
    return RuleOverride(id=f"o-{rule}", rule_id=rule, subject_uid=uid, enabled=enabled,
                        params=params)


def test_heartbeat_contract_from_override_and_its_removal():
    e = engine()
    e.process(snapshot(UID, "available", T0))
    assert e.heartbeat_subjects == frozenset()
    e.set_overrides([_override("DOC005", UID, period_s=60, threshold_s=300)], T0)
    assert run(e, T0 + 299 * S) == []  # max(3 x 60, 300)
    e.process(heartbeat(UID, T0 + 200 * S))
    assert run(e, T0 + 499 * S) == []
    assert [f.rule_id for f in run(e, T0 + 500 * S)] == ["DOC005"]
    closed = e.set_overrides([], T0 + 600 * S)
    assert [(f.rule_id, f.details["resolution"]) for f in closed] == [
        ("DOC005", "contract_removed")]
    assert run(e, T0 + 10_000 * S) == []
    assert e.process(heartbeat(UID, T0 + 10_001 * S)) == []  # no contract, ignored


def test_heartbeat_ignored_without_contract_ac_doc_03():
    e = engine()
    e.process(snapshot(UID, "available", T0))
    for i in range(1, 25):
        e.process(heartbeat(UID, T0 + i * 3600 * S))
    assert run(e, T0 + 86_400 * S) == []


def test_override_changes_threshold_and_disables_rule():
    e = engine()
    other = "7d0e4b8c-9f1a-4e37-8f5b-2a3c4d5e6f70"
    e.set_overrides([_override("DOC001", UID, unavailable_s=600)], T0)
    for uid in (UID, other):
        e.process(snapshot(uid, "available", T0))
        e.process(avail(uid, "unavailable", T0 + 200 * S))
    assert [f.subject_uid for f in run(e, T0 + 799 * S)] == [other]
    assert [f.subject_uid for f in run(e, T0 + 800 * S)] == [UID]
    closed = e.set_overrides([_override("DOC001", other, enabled=False)], T0 + 900 * S)
    assert [(f.subject_uid, f.details["resolution"]) for f in closed] == [
        (other, "rule_disabled")]
    e.process(avail(other, "available", T0 + 950 * S))
    e.process(avail(other, "unavailable", T0 + 1000 * S))
    assert [f for f in run(e, T0 + 5000 * S) if f.subject_uid == other] == []


def test_override_validation():
    import pytest

    from custom_components.ha_sentinel.core.errors import SentinelError
    from custom_components.ha_sentinel.modules.doctor.overrides import validate

    assert validate("DOC005", True, {"period_s": 60}) == {"period_s": 60}
    assert validate("DOC001", False, {}) == {}
    for rule, enabled, params, field in (
        ("DOC010", True, {}, "rule_id"),
        ("DOC001", True, {"bogus": 1}, "param"),
        ("DOC001", True, {"unavailable_s": 5}, "param"),
        ("DOC001", True, {"unavailable_s": True}, "param"),
        ("DOC005", True, {}, "param"),
        ("DOC004", True, {"low_pct": 30, "clear_pct": 25}, "param"),
    ):
        with pytest.raises(SentinelError) as err:
            validate(rule, enabled, params)
        assert err.value.code == "validation_failed" and field in err.value.details
