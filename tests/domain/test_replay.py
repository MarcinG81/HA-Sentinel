"""Replay interpreter (spec 11.4): three-valued logic, crossings, unsupported steps."""

from __future__ import annotations

from custom_components.ha_sentinel.core.yaml_ast import parse
from custom_components.ha_sentinel.modules.autopilot.replay import StateEvent, replay

HEAT = parse("""
- id: heat
  alias: Heat
  triggers:
    - trigger: numeric_state
      entity_id: sensor.temp
      below: 20
  conditions:
    - condition: state
      entity_id: input_boolean.home
      state: "on"
  actions:
    - action: switch.turn_on
      target: {entity_id: switch.heater}
- id: door
  alias: Door
  triggers:
    - trigger: state
      entity_id: binary_sensor.door
      to: "on"
  actions:
    - choose:
        - conditions:
            - condition: numeric_state
              entity_id: sensor.temp
              below: 10
          sequence:
            - action: notify.cold
      default:
        - action: light.turn_on
          target: {entity_id: light.hall}
    - delay: 30
    - action: light.turn_off
""").tree


def ev(t, e, s):
    return StateEvent(t, e, s)


def test_numeric_state_fires_on_crossing_only():
    rep = replay(HEAT, {"sensor.temp": "21", "input_boolean.home": "on"},
                 [ev(1, "sensor.temp", "19.5"), ev(2, "sensor.temp", "18"),
                  ev(3, "sensor.temp", "22"), ev(4, "sensor.temp", "19")])
    heat = [o for o in rep.occurrences if o["automation"] == "Heat"]
    assert [o["time_us"] for o in heat] == [1, 4]  # 19.5 and 19 cross; 18 stays below
    assert heat[0]["would_run"] == "true"
    assert heat[0]["planned_actions"][0]["service"] == "switch.turn_on"


def test_missing_condition_state_is_unknown_not_false():
    rep = replay(HEAT, {"sensor.temp": "21"}, [ev(1, "sensor.temp", "15")])
    occ = rep.occurrences[0]
    assert occ["conditions"] == "unknown"
    assert rep.missing_initial == ["binary_sensor.door"]  # only the other automation lacks it


def test_condition_false_means_no_actions():
    rep = replay(HEAT, {"sensor.temp": "21", "input_boolean.home": "off"},
                 [ev(1, "sensor.temp", "15")])
    assert rep.occurrences[0]["would_run"] == "false"
    assert rep.occurrences[0]["planned_actions"] == []


def test_choose_default_branch_then_delay_is_unsupported():
    rep = replay(HEAT, {"sensor.temp": "21", "binary_sensor.door": "off"},
                 [ev(1, "binary_sensor.door", "on")])
    occ = next(o for o in rep.occurrences if o["automation"] == "Door")
    services = [a.get("service") for a in occ["planned_actions"] if "service" in a]
    assert services == ["light.turn_on"]  # 21 is not below 10 -> default
    assert occ["plan_complete"] is False
    assert any(u["reason"] == "needs_time_model" for u in rep.unsupported)


def test_unknown_choose_condition_stops_plan():
    rep = replay(HEAT, {"binary_sensor.door": "off"}, [ev(1, "binary_sensor.door", "on")])
    occ = rep.occurrences[0]
    assert occ["planned_actions"] == [{"pointer": "/1/actions/0/choose/0", "unknown_branch": True}]


def test_state_trigger_from_to_and_missing_initial():
    tree = parse("""
- alias: Off to on
  triggers: [{trigger: state, entity_id: light.x, from: "off", to: "on"}]
  actions: [{action: script.go}]
""").tree
    rep = replay(tree, {}, [ev(1, "light.x", "on"), ev(2, "light.x", "off"),
                            ev(3, "light.x", "on")])
    assert [o["time_us"] for o in rep.occurrences] == [3]  # first "on" had unknown origin
    assert rep.missing_initial == ["light.x"]


def test_unsupported_triggers_are_listed_with_pointers():
    tree = parse("""
- alias: Mixed
  triggers:
    - trigger: time
      at: "07:00:00"
    - trigger: state
      entity_id: light.x
      to: "on"
      for: "00:05:00"
    - trigger: state
      entity_id: light.y
  actions: []
""").tree
    rep = replay(tree, {"light.y": "off"}, [ev(1, "light.y", "on")])
    reasons = {u["pointer"]: u["reason"] for u in rep.unsupported}
    assert reasons["/0/triggers/0"].startswith("trigger_time")
    assert reasons["/0/triggers/1"] == "for_needs_time_model"
    assert rep.supported_nodes == 1 and rep.total_nodes == 3
    assert len(rep.occurrences) == 1
