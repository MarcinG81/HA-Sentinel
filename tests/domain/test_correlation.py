"""Correlation and RCA ranking: reference case 9.3, AC COR 01-04."""

from __future__ import annotations

from custom_components.ha_sentinel.core.models import Severity
from custom_components.ha_sentinel.modules.correlation.correlator import (
    ActiveFinding,
    CandidateEvidence,
    ExistingIncident,
    correlate,
    fingerprint,
)
from custom_components.ha_sentinel.modules.dependency_mapper.graph import (
    DeviceInfo,
    EntityInfo,
    EntryInfo,
    build_graph,
)

S = 1_000_000
T0 = 1_792_230_000_000_000  # 09:42:00 in the scenario
INSTANCE = "instance-1"


def _graph():
    entities, devices = [], []
    for i in range(18):
        devices.append(DeviceInfo(f"m{i}", f"MQTT dev {i}", "salon", ("mqtt",)))
        entities.append(EntityInfo(f"e-m{i}", f"sensor.mqtt_{i}", f"MQTT {i}", f"m{i}", "salon",
                                   "mqtt"))
    for i in range(3):
        devices.append(DeviceInfo(f"z{i}", f"Zigbee {i}", "salon", ("zha",)))
        entities.append(EntityInfo(f"e-z{i}", f"sensor.zb_{i}", f"ZB {i}", f"z{i}", "salon", "zha"))
    entries = [EntryInfo("mqtt", "mqtt", "MQTT broker"), EntryInfo("zha", "zha", "Zigbee")]
    return build_graph(entities=entities, devices=devices, entries=entries,
                       areas={"salon": "Salon"}, imports=[], revision=4)


def _outage(n=18, spread_s=40):
    return [ActiveFinding(f"f{i:02d}", "DOC001", f"e-m{i}", Severity.HIGH,
                          T0 + int(i * spread_s / max(n - 1, 1)) * S) for i in range(n)]


def _available(uid):
    return not uid.startswith("e-m")  # MQTT entities down, Zigbee fine


def _run(findings, existing=(), evidence=None, graph=None):
    return correlate(
        instance_id=INSTANCE, findings=findings, existing=list(existing),
        graph=graph or _graph(), evidence=evidence or {}, entity_available=_available,
        coverage_complete=lambda a, b: True, now_us=T0 + 300 * S,
    )


BROKER = {"config_entry:mqtt": CandidateEvidence(1.0, T0, "config_entry_state")}


def test_reference_case_one_infrastructure_incident_with_broker_evidence():
    plans = _run(_outage(), evidence=BROKER)
    assert len(plans) == 1
    plan = plans[0]
    assert len(plan.finding_ids) == 18
    assert plan.title_key == "incident.infrastructure"
    assert plan.root_uid == "config_entry:mqtt"
    top = plan.hypotheses[0]
    assert top["candidate_uid"] == "config_entry:mqtt"
    assert top["label"] == "strong"
    assert top["features"]["D"] == 1.0 and top["features"]["E"] == 1.0
    assert len(plan.hypotheses) <= 3


def test_without_broker_telemetry_it_is_only_possible():
    top = _run(_outage())[0].hypotheses[0]
    assert top["candidate_uid"] == "config_entry:mqtt"
    assert top["label"] == "possible"  # "a common MQTT problem is possible", not certain
    assert {"kind": "no_direct_evidence"} in top["counter_evidence"]


def test_ac_cor_01_unrelated_battery_in_same_room_not_merged():
    findings = _outage() + [ActiveFinding("bat", "DOC004", "e-z0", Severity.WARNING, T0 + 10 * S)]
    plans = _run(findings, evidence=BROKER)
    assert len(plans) == 2
    battery = next(p for p in plans if p.finding_ids == ["bat"])
    assert battery.hypotheses == [] and battery.root_uid is None


def test_ac_cor_02_removing_evidence_lowers_ranking():
    with_ev = _run(_outage(), evidence=BROKER)[0].hypotheses[0]["score"]
    without = _run(_outage())[0].hypotheses[0]["score"]
    assert without < with_ev


def test_ac_cor_03_identical_inputs_identical_ranking():
    assert _run(_outage(), evidence=BROKER)[0].hypotheses == \
        _run(list(reversed(_outage())), evidence=BROKER)[0].hypotheses


def test_ac_cor_04_counter_evidence_present_for_partial_candidates():
    # Only 9 of 18 MQTT entities down: healthy dependents are counter-evidence (K).
    findings = _outage(n=9)

    def half(uid):
        return not (uid.startswith("e-m") and int(uid[3:]) < 9)

    plan = correlate(instance_id=INSTANCE, findings=findings, existing=[], graph=_graph(),
                     evidence={}, entity_available=half, coverage_complete=lambda a, b: True,
                     now_us=T0)[0]
    # Without direct evidence nothing explains half an integration failing: no main cause.
    assert all(h["label"] == "none" for h in plan.hypotheses)
    plan = correlate(instance_id=INSTANCE, findings=findings, existing=[], graph=_graph(),
                     evidence=BROKER, entity_available=half,
                     coverage_complete=lambda a, b: True, now_us=T0)[0]
    top = plan.hypotheses[0]
    assert top["candidate_uid"] == "config_entry:mqtt" and top["label"] == "possible"
    assert top["features"]["K"] == 0.5
    healthy = next(c for c in top["counter_evidence"] if c["kind"] == "healthy_dependents")
    assert healthy["count"] == 9 and len(healthy["sample"]) == 5


def test_late_effect_outside_window_is_separate():
    findings = _outage(n=2) + [ActiveFinding("late", "DOC001", "e-m5", Severity.HIGH,
                                             T0 + 600 * S)]
    plans = _run(findings)
    assert sorted(len(p.finding_ids) for p in plans) == [1, 2]


def test_candidate_starting_after_effects_gets_t_zero():
    ev = {"config_entry:mqtt": CandidateEvidence(1.0, T0 + 60 * S, "config_entry_state")}
    top = next(h for h in _run(_outage(), evidence=ev)[0].hypotheses
               if h["candidate_uid"] == "config_entry:mqtt")
    assert top["features"]["T"] == 0.0 and "candidate_started_after_effects" in top["reasons"]


def test_singleton_incident_is_merged_when_peers_mature():
    first = _outage()[:1]
    singleton_fp = fingerprint(INSTANCE, "DOC001", "e-m0")
    existing = [ExistingIncident("inc-1", singleton_fp, frozenset({"f00"}), T0, None)]
    assert _run(first, existing)[0].fingerprint == singleton_fp  # alone: unchanged
    plans = _run(_outage(), existing)
    assert len(plans) == 1 and plans[0].merge_incident_ids == ["inc-1"]


def test_group_incident_keeps_members_while_any_is_active():
    group_fp = fingerprint(INSTANCE, "availability", "config_entry:mqtt")
    existing = [ExistingIncident("g", group_fp, frozenset(f.id for f in _outage()), T0,
                                 "config_entry:mqtt")]
    remaining = _outage()[5:6]  # all but one recovered
    plans = _run(remaining, existing)
    assert [(p.fingerprint, p.finding_ids) for p in plans] == [(group_fp, ["f05"])]
