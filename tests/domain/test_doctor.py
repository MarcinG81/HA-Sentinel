"""Doctor rules: T04, T05, T06, AC DOC 01-04."""

from __future__ import annotations

from custom_components.ha_sentinel.core.coverage import CoverageTracker
from custom_components.ha_sentinel.core.models import (
    CoverageSegment,
    CoverageStatus,
    FindingStatus,
)
from custom_components.ha_sentinel.modules.doctor.engine import DoctorEngine, DoctorParams

from .helpers import S, avail, battery, snapshot

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
