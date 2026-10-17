"""Autopilot draft wizard (spec 11.1, AC AUT 04)."""

from __future__ import annotations

import pytest

from custom_components.ha_sentinel.core.errors import SentinelError
from custom_components.ha_sentinel.core.yaml_ast import parse
from custom_components.ha_sentinel.modules.autopilot.draft import (
    DraftSpec,
    build_tree,
    to_yaml,
    validate,
)
from custom_components.ha_sentinel.modules.autopilot.lint import lint
from custom_components.ha_sentinel.modules.autopilot.model import extract

KNOWN = {"binary_sensor.door", "sensor.lux", "light.hall", "light.porch", "sun.sun"}


def spec(**kw) -> DraftSpec:
    base = dict(alias="Hall light", trigger={"platform": "state", "entity_id": "binary_sensor.door",
                                             "to": "on"},
                conditions=[{"entity_id": "sun.sun", "state": "below_horizon"}],
                actions=[{"service": "light.turn_on", "entity_ids": ["light.hall"],
                          "brightness_pct": 60}])
    base.update(kw)
    return DraftSpec(**base)


def test_draft_roundtrips_through_parser_and_lint():
    s = spec(cooldown_s=120)
    validate(s, KNOWN.__contains__)
    text = to_yaml(build_tree(s))
    tree = parse(text).tree  # same safe parser as an import
    auto = tree[0]
    assert auto["triggers"] == [{"trigger": "state", "entity_id": "binary_sensor.door",
                                 "to": "on"}]
    assert auto["actions"][-1] == {"delay": {"seconds": 120}}
    assert auto["id"].startswith("sentinel_draft_")
    model = extract(tree, "automation")
    assert model.completeness == 1.0
    report = lint(model, entity_status=lambda e: ("active", None) if e in KNOWN
                  else ("missing", None), service_exists=lambda d, s: True,
                  inventory_complete=True)
    assert {f.rule_id for f in report.findings} <= {"AUT005"}  # delay + single is advisory
    assert to_yaml(build_tree(s)) == text  # deterministic id and output


def test_numeric_trigger_and_turn_off():
    s = spec(trigger={"platform": "numeric_state", "entity_id": "sensor.lux", "below": 20,
                      "for_s": 300},
             conditions=[], actions=[{"service": "light.turn_off",
                                      "entity_ids": ["light.porch"]}], mode="restart")
    validate(s, KNOWN.__contains__)
    auto = build_tree(s)[0]
    assert auto["triggers"][0] == {"trigger": "numeric_state", "entity_id": "sensor.lux",
                                   "below": 20, "for": {"seconds": 300}}
    assert auto["mode"] == "restart" and "data" not in auto["actions"][0]


@pytest.mark.parametrize(("kw", "param"), [
    ({"trigger": {"platform": "template", "entity_id": "sensor.lux"}}, "trigger.platform"),
    ({"trigger": {"platform": "numeric_state", "entity_id": "sensor.lux"}}, "trigger.above"),
    ({"trigger": {"platform": "numeric_state", "entity_id": "sensor.lux", "above": 5,
                  "below": 1}}, "trigger.below"),
    ({"trigger": {"platform": "state", "entity_id": "sensor.lux", "above": 5}},
     "trigger.above"),
    ({"mode": "queued", "cooldown_s": 60}, "cooldown_s"),
    ({"mode": "chaotic"}, "mode"),
    ({"actions": []}, "actions"),
    ({"conditions": [{"entity_id": "sun.sun", "state": "x"}] * 6}, "conditions"),
    ({"actions": [{"service": "lock.unlock", "entity_ids": ["light.hall"]}]},
     "actions.0.service"),
    ({"actions": [{"service": "light.turn_on", "entity_ids": ["switch.heater"]}]},
     "actions.0.entity_ids"),
    ({"actions": [{"service": "light.turn_off", "entity_ids": ["light.hall"],
                   "brightness_pct": 5}]}, "actions.0.brightness_pct"),
    ({"actions": [{"service": "light.turn_on", "entity_ids": ["light.gone"]}]}, "entity_id"),
])
def test_draft_validation(kw, param):
    with pytest.raises(SentinelError) as err:
        validate(spec(**kw), KNOWN.__contains__)
    assert err.value.code == "validation_failed" and err.value.details["param"] == param
