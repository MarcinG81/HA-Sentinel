"""Branch coverage for the parser family (spec 23.3: >= 90 % for parser code)."""

from __future__ import annotations

import pytest

from custom_components.ha_sentinel.core.yaml_ast import YamlRejected, parse
from custom_components.ha_sentinel.modules.autopilot.model import extract
from custom_components.ha_sentinel.modules.autopilot.replay import StateEvent, replay


def model(src: str, kind: str = "automation"):
    return extract(parse(src).tree, kind)


# --- yaml_ast -------------------------------------------------------------------------------


@pytest.mark.parametrize(("src", "reason"), [
    (b"\xff\xfe not utf8", "not_utf8"),
    ("? [a, b]\n: 1", "complex_key"),
    ("a: !secret [x]", "bad_tag_value"),
    ("a: !!binary aGVsbG8=", "unknown_tag"),
    ("a: !!set {x}", "unknown_tag"),
    ("a: !!omap [b: 1]", "unknown_tag"),
])
def test_yaml_more_rejections(src, reason):
    with pytest.raises(YamlRejected) as err:
        parse(src)
    assert err.value.reason == reason


def test_yaml_empty_scalars_and_timestamps():
    assert parse("").tree is None
    assert parse("# only a comment\n").tree is None
    tree = parse("when: 2026-10-17\nn: ~\nb: yes\nf: 1.5\ni: 0x10\n").tree
    assert tree == {"when": "2026-10-17", "n": None, "b": True, "f": 1.5, "i": 16}
    parsed = parse("a:\n  b: 1\n")
    assert parsed.span("/a/b/missing").line == 2  # nearest ancestor span
    assert parsed.span("/zzz") is not None  # falls back to the document root


# --- automation / script / blueprint model ----------------------------------------------


def test_legacy_forms_and_nested_actions():
    m = model("""
automation:
  - alias: Legacy
    trigger:
      platform: state
      entity_id: binary_sensor.door, binary_sensor.window
    condition:
      - condition: or
        conditions:
          - condition: state
            entity_id: sun.sun
            state: below_horizon
          - "{{ is_state('x', 'on') }}"
      - condition: device
        device_id: dev123
        domain: zha
        type: is_on
      - condition: template
        value_template: "{{ true }}"
    action:
      - service: light.turn_on
        entity_id: light.hall
      - scene: scene.evening
      - parallel:
          - sequence:
              - service: script.goodnight
          - service: notify.mobile
      - repeat:
          count: 2
          sequence:
            - service: switch.toggle
              data: {entity_id: switch.fan}
      - sequence:
          - event: custom_event
          - variables: {x: 1}
          - stop: done
      - device_id: dev123
        domain: zha
        type: turn_on
      - wait_for_trigger: [{trigger: state, entity_id: x.y}]
      - if: [{condition: state, entity_id: light.hall, state: "on"}]
        then: [{service: light.turn_off, target: {area_id: kitchen}}]
        else: [{service: light.turn_on, target: {device_id: dev123}}]
""")
    r = m.runnables[0]
    assert r.trigger_entities == {"binary_sensor.door", "binary_sensor.window"}
    services = [c.service for c in r.calls]
    assert services[:2] == ["light.turn_on", "scene.turn_on"]
    assert {"script.goodnight", "notify.mobile", "switch.toggle", "light.turn_off"} <= set(services)
    kinds = {ref.kind for ref in r.refs}
    assert {"entity", "device", "area", "scene", "script", "service"} <= kinds
    assert r.has_delay_or_wait and r.has_conditions
    assert r.templates  # template condition and shorthand template
    assert not r.unsupported


def test_unsupported_and_malformed_fragments():
    m = model("""
- alias: Odd
  triggers:
    - just a string
    - trigger: state
      entity_id: [sensor.a, "{{ dynamic }}"]
      value_template: "{{ x }}"
  conditions:
    - condition: quantum
    - 42
    - not a template
  actions:
    - 7
    - action: not_a_service_name
    - action: "{{ 'light.' ~ x }}"
    - action: light.turn_on
      target: "{{ targets }}"
    - teleport: now
- 5
""")
    reasons = {u.reason for r in m.runnables for u in r.unsupported} | \
        {u.reason for u in m.unsupported}
    assert {"trigger_not_mapping", "unknown_condition", "condition_not_mapping",
            "action_not_mapping", "dynamic_service", "unknown_action", "not_a_mapping"} <= reasons
    assert m.completeness < 0.5


def test_scripts_and_blueprints_variants():
    single = model("alias: one\nsequence:\n  - action: light.turn_on\n", kind="script")
    assert single.runnables[0].key == "script"
    bad = model("not_a_mapping: 1\nother: [1]\n", kind="script")
    assert {u.reason for u in bad.unsupported} == {"not_a_mapping"}
    assert model("- a\n", kind="script").unsupported[0].reason == "not_a_script_mapping"
    assert model("blueprint: {domain: script}\nsequence: []\n",
                 kind="blueprint").runnables[0].kind == "script"
    assert model("blueprint: {domain: template}\n",
                 kind="blueprint").unsupported[0].reason == "unknown_blueprint_domain"
    assert model("a: 1\n", kind="blueprint").unsupported[0].reason == "not_a_blueprint"
    assert model("42\n").unsupported[0].reason == "not_an_automation_list"
    assert model("a: 1\n", kind="other").unsupported[0].reason == "unknown_resource_kind"
    single_auto = model("alias: x\ntriggers: []\nactions: []\n")
    assert len(single_auto.runnables) == 1


# --- replay -------------------------------------------------------------------------------


def test_replay_logic_operators_and_branches():
    tree = parse("""
- alias: Logic
  triggers:
    - trigger: state
      entity_id: input_button.go
  conditions:
    - condition: or
      conditions:
        - condition: state
          entity_id: person.a
          state: home
        - condition: not
          conditions:
            - condition: numeric_state
              entity_id: sensor.t
              above: 30
  actions:
    - if:
        - condition: state
          entity_id: light.x
          state: "on"
      then:
        - action: light.turn_off
      else:
        - action: light.turn_on
    - condition: state
      entity_id: switch.gate
      state: "on"
    - action: notify.after_gate
""").tree
    states = {"input_button.go": "a", "person.a": "away", "sensor.t": "20", "light.x": "off",
              "switch.gate": "off"}
    occ = replay(tree, states, [StateEvent(1, "input_button.go", "b")]).occurrences[0]
    assert occ["conditions"] == "true"  # not(20 > 30) is true
    assert [a.get("service") for a in occ["planned_actions"]] == ["light.turn_on"]
    assert occ["plan_complete"] is True  # gate condition false: the run stops like in HA

    states["switch.gate"] = "on"
    states["light.x"] = "on"
    occ = replay(tree, states, [StateEvent(1, "input_button.go", "c")]).occurrences[0]
    assert [a.get("service") for a in occ["planned_actions"]] == ["light.turn_off",
                                                                  "notify.after_gate"]
    del states["light.x"]
    occ = replay(tree, states, [StateEvent(1, "input_button.go", "d")]).occurrences[0]
    assert occ["planned_actions"][-1].get("unknown_branch") is True


def test_replay_unsupported_conditions_and_actions():
    tree = parse("""
- alias: Odd
  triggers:
    - trigger: numeric_state
      entity_id: sensor.t
      above: input_number.threshold
    - trigger: numeric_state
      entity_id: sensor.t
      below: 10
    - trigger: state
      entity_id: "{{ x }}"
    - not_a_mapping
  conditions:
    - "{{ template }}"
    - condition: time
      after: "07:00"
  actions:
    - wait_template: "{{ x }}"
- alias: Odd action
  triggers: [{trigger: state, entity_id: sensor.t}]
  actions: [5]
- alias: Unknown action
  triggers: [{trigger: state, entity_id: sensor.t}]
  actions: [{teleport: true}]
- use_blueprint: {path: x.yaml}
""").tree
    rep = replay(tree, {"sensor.t": "20"}, [StateEvent(1, "sensor.t", "5")])
    reasons = {u["reason"] for u in rep.unsupported}
    assert {"non_numeric_threshold", "template", "trigger_not_mapping", "template_condition",
            "condition_time", "action_not_mapping", "unknown_action"} <= reasons
    odd = next(o for o in rep.occurrences if o["automation"] == "Odd")
    assert odd["conditions"] == "unknown"
    assert rep.as_dict()["interpreter_version"] == 1
    assert replay({"alias": "single", "triggers": []}, {}, []).occurrences == []
    assert replay("not a tree", {}, []).occurrences == []


def test_replay_truncates_occurrences():
    tree = parse("- alias: Many\n  triggers: [{trigger: state, entity_id: sensor.t}]\n"
                 "  actions: []\n").tree
    events = [StateEvent(i, "sensor.t", str(i)) for i in range(1100)]
    rep = replay(tree, {"sensor.t": "x"}, events)
    assert rep.truncated and len(rep.occurrences) == 1000
