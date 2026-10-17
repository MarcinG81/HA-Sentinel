"""E3 domain tests: safe YAML (T09), diff (AC HK 01), lint (AC AUT), graph (AC MAP, T11)."""

from __future__ import annotations

import time

import pytest

from custom_components.ha_sentinel.core.yaml_ast import YamlRejected, parse
from custom_components.ha_sentinel.modules.autopilot.lint import lint
from custom_components.ha_sentinel.modules.autopilot.model import extract
from custom_components.ha_sentinel.modules.config_history.canonical import (
    REDACTED,
    canonical_hash,
    find_plain_secrets,
    raw_hash,
    redact,
    semantic_diff,
)
from custom_components.ha_sentinel.modules.dependency_mapper.graph import (
    DependencyGraph,
    DeviceInfo,
    Edge,
    EntityInfo,
    EntryInfo,
    ImportedResource,
    Node,
    build_graph,
)

AUTOMATIONS = """
- id: heat_on
  alias: Heat on
  triggers:
    - trigger: numeric_state
      entity_id: sensor.temp
      below: 20
  actions:
    - action: climate.turn_on
      target:
        entity_id: climate.living
- id: heat_off
  alias: Heat off
  triggers:
    - trigger: numeric_state
      entity_id: sensor.temp
      above: 20
  actions:
    - action: climate.turn_off
      target:
        entity_id: climate.living
"""


# --- T09: safe parser ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "reason"),
    [
        ("a: &a [x,x,x,x,x,x,x,x,x,x]\nb: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a,*a]\n"
         "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b,*b]\nd: [*c,*c,*c,*c,*c,*c,*c,*c,*c,*c]\n",
         "too_many_nodes"),
        ("a: !!python/object/apply:os.system ['id']", "unknown_tag"),
        ("a: !custom x", "unknown_tag"),
        ("a: " + "[" * 60 + "]" * 60, "too_deep"),
        ("a: 1\na: 2", "duplicate_key"),
        ("a: .nan", "non_finite"),
        ("a: 1\n---\nb: 2", "multi_document"),
        ("a: [1, 2", "syntax"),
    ],
)
def test_t09_rejections(src, reason):
    start = time.monotonic()
    with pytest.raises(YamlRejected) as err:
        parse(src)
    assert err.value.reason == reason
    assert time.monotonic() - start < 1.0


def test_t09_size_limit_checked_before_parsing():
    with pytest.raises(YamlRejected) as err:
        parse(b"a: " + b"x" * (1024 * 1024))
    assert err.value.reason == "too_large"


def test_secret_and_include_stay_symbolic():
    parsed = parse("password: !secret db_pass\nextra: !include other.yaml\n")
    assert parsed.tree == {"password": {"$ref": "secret", "arg": "db_pass"},
                           "extra": {"$ref": "include", "arg": "other.yaml"}}
    assert find_plain_secrets(parsed.tree) == []  # a reference is not a plain secret


def test_source_spans_point_to_fragment():
    parsed = parse(AUTOMATIONS)
    span = parsed.span("/1/actions/0/target/entity_id")
    assert (span.line, span.column) == (21, 20)  # fixture starts with a blank line


# --- Canonical hash and diff ----------------------------------------------------------


def test_ac_hk_01_comment_is_textual_only_and_reorder_is_semantic():
    base = "alias: x\nactions:\n  - action: light.turn_on\n  - action: light.turn_off\n"
    commented = "# note\nalias: x   # trailing\nactions:\n  - action: light.turn_on\n" \
                "  - action: light.turn_off\n"
    reordered = "alias: x\nactions:\n  - action: light.turn_off\n  - action: light.turn_on\n"
    a, b, c = parse(base).tree, parse(commented).tree, parse(reordered).tree
    assert raw_hash(base.encode()) != raw_hash(commented.encode())
    assert canonical_hash(a) == canonical_hash(b)
    assert semantic_diff(a, b) == []
    changes = semantic_diff(a, c)
    assert canonical_hash(a) != canonical_hash(c)
    assert [(ch.op, ch.path) for ch in changes] == [
        ("replace", "/actions/0/action"), ("replace", "/actions/1/action")]


def test_key_order_does_not_change_canonical_hash():
    assert canonical_hash(parse("a: 1\nb: 2").tree) == canonical_hash(parse("b: 2\na: 1").tree)


def test_plain_secrets_detected_and_redacted():
    tree = parse("rest:\n  - url: http://x\n    password: hunter2\n    api_key: abc\n").tree
    found = find_plain_secrets(tree)
    assert found == ["/rest/0/password", "/rest/0/api_key"]
    red = redact(tree, found)
    assert red["rest"][0]["password"] == REDACTED and red["rest"][0]["url"] == "http://x"


# --- Lint -------------------------------------------------------------------------------


def _lint(src: str, kind: str = "automation", known=("sensor.temp", "climate.living"),
          renamed=None, complete=True):
    parsed = parse(src)
    model = extract(parsed.tree, kind)

    def status(entity_id):
        if renamed and entity_id in renamed:
            return "renamed", renamed[entity_id]
        return ("active", None) if entity_id in known else ("unknown", None)

    report = lint(model, entity_status=status, service_exists=lambda d, s: d != "bogus",
                  inventory_complete=complete)
    return parsed, model, report


def test_ac_aut_01_missing_literal_entity_points_to_ast_path():
    src = AUTOMATIONS.replace("climate.living\n- id", "climate.livng\n- id", 1)
    parsed, _, report = _lint(src)
    f = next(f for f in report.findings if f.rule_id == "AUT001")
    assert f.level == "error"
    assert f.pointer == "/0/actions/0/target/entity_id"
    assert f.as_dict(parsed)["span"]["line"] == 11


def test_aut001_is_warning_with_incomplete_inventory_and_reports_rename():
    src = AUTOMATIONS.replace("climate.living\n- id", "climate.livng\n- id", 1)
    _, _, report = _lint(src, complete=False)
    assert next(f for f in report.findings if f.rule_id == "AUT001").level == "warning"
    _, _, report = _lint(AUTOMATIONS, known=("climate.living",),
                         renamed={"sensor.temp": "sensor.temperature"})
    f = next(f for f in report.findings if f.rule_id == "AUT001")
    assert f.message_key == "aut001.renamed" and f.params["new"] == "sensor.temperature"


def test_ac_aut_02_template_not_executed_and_lowers_completeness():
    src = """
- alias: dyn
  triggers: [{trigger: state, entity_id: sensor.temp}]
  actions:
    - action: "{{ 'light.turn_' ~ states('sensor.temp') }}"
    - action: light.turn_on
      target: {entity_id: "{{ states('input_text.x') }}"}
"""
    _, model, report = _lint(src)
    rules = {f.rule_id for f in report.findings}
    assert "AUT007" in rules
    assert report.completeness < 1.0
    assert model.runnables[0].calls[0].dynamic_target is True


def test_templated_target_lowers_completeness():
    src = """
- alias: t
  triggers: [{trigger: state, entity_id: sensor.temp}]
  actions:
    - action: light.turn_on
      target: {entity_id: "{{ 'light.' ~ states('sensor.temp') }}"}
"""
    _, model, _ = _lint(src)
    assert model.completeness == 0.5


def test_aut002_unknown_service_and_aut008_risk():
    src = """
- alias: r
  triggers: [{trigger: state, entity_id: sensor.temp, to: "on"}]
  actions:
    - action: bogus.do
    - action: lock.unlock
      target: {entity_id: lock.front}
"""
    _, _, report = _lint(src, known=("sensor.temp", "lock.front"))
    assert {(f.rule_id, f.level) for f in report.findings} >= {("AUT002", "warning"),
                                                              ("AUT008", "risk")}


def test_ac_aut_05_cycle_with_breaker_is_potential_loop():
    src = """
- id: a
  alias: A
  triggers: [{trigger: state, entity_id: light.one}]
  conditions: [{condition: state, entity_id: input_boolean.guard, state: "on"}]
  actions: [{action: light.toggle, target: {entity_id: light.two}}]
- id: b
  alias: B
  triggers: [{trigger: state, entity_id: light.two}]
  actions: [{action: light.toggle, target: {entity_id: light.one}}]
"""
    _, _, report = _lint(src, known=("light.one", "light.two", "input_boolean.guard"))
    loop = [f for f in report.findings if f.rule_id == "AUT004"]
    assert len(loop) == 1
    assert loop[0].heuristic and loop[0].level == "warning"
    assert set(loop[0].params["cycle"]) == {"A", "B"}
    assert "condition" in loop[0].params["possible_breakers"]


def test_aut003_conflict_aut006_hysteresis_aut009_duplicates():
    src = AUTOMATIONS + """
- id: heat_on
  alias: Dup
  triggers:
    - trigger: numeric_state
      entity_id: sensor.temp
      below: 20
  actions:
    - action: climate.turn_off
      target: {entity_id: climate.living}
"""
    _, _, report = _lint(src)
    rules = [f.rule_id for f in report.findings]
    assert "AUT009" in rules
    assert "AUT003" in rules  # same trigger, opposite actions
    hyst = AUTOMATIONS.replace("climate.turn_on", "switch.turn_on").replace(
        "climate.turn_off", "switch.turn_off").replace("climate.living", "switch.heater")
    hyst = hyst.replace("below: 20", "above: 20", 1).replace("above: 20\n  actions:\n    - "
                                                             "action: switch.turn_off",
                                                             "below: 20\n  actions:\n    - "
                                                             "action: switch.turn_off")
    _, _, report = _lint(hyst, known=("sensor.temp", "switch.heater"))
    assert [f.rule_id for f in report.findings if f.rule_id == "AUT006"] == ["AUT006"]


def test_aut005_single_mode_with_delay_and_aut010_unknowns():
    src = """
- alias: slow
  triggers: [{trigger: state, entity_id: sensor.temp}]
  actions: [{delay: 30}]
- alias: bp
  use_blueprint: {path: motion.yaml}
- alias: weird
  triggers: [{trigger: quantum, entity_id: sensor.temp}]
  actions: [{teleport: true}]
"""
    _, _, report = _lint(src)
    rules = [f.rule_id for f in report.findings]
    assert "AUT005" in rules
    assert rules.count("AUT010") == 3  # blueprint, unknown trigger, unknown action


def test_script_and_blueprint_kinds():
    _, model, _ = _lint("morning:\n  sequence:\n    - action: script.wake\n", kind="script")
    assert [r.kind for r in model.runnables] == ["script"]
    assert any(ref.kind == "script" for ref in model.runnables[0].refs)
    bp = ("blueprint:\n  name: M\n  domain: automation\ntriggers:\n  - trigger: state\n"
          "    entity_id: !input motion\nactions:\n  - action: light.turn_on\n")
    _, model, _ = _lint(bp, kind="blueprint")
    assert any(ref.kind == "blueprint_input" for ref in model.runnables[0].refs)


# --- Graph ------------------------------------------------------------------------------


def _graph(entities=None, imports=()):
    entities = entities or [
        EntityInfo("u-temp", "sensor.temp", "Temperature", "d1", "kitchen", "ce1"),
        EntityInfo("u-climate", "climate.living", "Living", "d2", "kitchen", "ce1"),
    ]
    return build_graph(
        entities=entities,
        devices=[DeviceInfo("d1", "Thermo", "kitchen", ("ce1",)),
                 DeviceInfo("d2", "Heater", "kitchen", ("ce1",))],
        entries=[EntryInfo("ce1", "mqtt", "MQTT")],
        areas={"kitchen": "Kitchen"},
        imports=list(imports), revision=1,
    )


def _imported(src=AUTOMATIONS):
    return ImportedResource("res1", "automations.yaml", extract(parse(src).tree, "automation"),
                            "2026-10-17T10:00:00Z")


def test_dependents_of_sensor_lists_automations_with_provenance():
    g = _graph(imports=[_imported()])
    res = g.query("u-temp", "dependents", 2)
    labels = {n["label"] for n in res["nodes"]}
    assert {"Heat on", "Heat off"} <= labels
    edge = next(e for e in res["edges"] if e["relation"] == "reads")
    assert edge["provenance"] == "static_parse" and edge["confidence"] == 1.0
    assert edge["evidence_ref"].startswith("res1#/")


def test_dependencies_follow_device_and_integration():
    res = _graph().query("u-temp", "dependencies", 3)
    assert [e["relation"] for e in res["edges"]] == ["provided_by", "managed_by"]


def test_ac_map_01_rename_keeps_single_node():
    g1 = _graph()
    renamed = [EntityInfo("u-temp", "sensor.temperature", "Temperature", "d1", None, "ce1"),
               EntityInfo("u-climate", "climate.living", "Living", "d2", None, "ce1")]
    g2 = _graph(entities=renamed)
    assert len([n for n in g2.nodes.values() if n.type == "entity"]) == \
        len([n for n in g1.nodes.values() if n.type == "entity"])


def test_unknown_reference_becomes_unresolved_node():
    g = _graph(imports=[_imported(AUTOMATIONS.replace("sensor.temp", "sensor.gone"))])
    assert g.unresolved_count == 1
    assert "unresolved:entity:sensor.gone" in g.nodes


def test_ac_map_04_area_does_not_propagate_impact():
    res = _graph().query("area:kitchen", "dependents", 5)
    assert res["edges"] == []  # member_of is grouping, not followed by default
    res = _graph().query("area:kitchen", "dependents", 1, include_grouping=True)
    assert {e["relation"] for e in res["edges"]} == {"member_of"}


def test_controls_is_listed_but_not_traversed():
    g = _graph(imports=[_imported()])
    res = g.query("u-climate", "dependents", 5)
    assert {e["relation"] for e in res["edges"]} == {"controls"}


def test_t11_cycle_with_100k_edges_terminates_within_limits():
    g = DependencyGraph(revision=7)
    n = 20_000
    for i in range(n):
        g.add_node(Node(f"n{i}", "entity", f"n{i}"))
    for i in range(n):
        for k in (1, 2, 3, 4, 5):  # branching factor 5 with wrap-around cycles
            g.add_edge(Edge(f"n{i}", f"n{(i * 5 + k) % n}", "depends_on", "manual", 1.0))
    assert g.edge_count == 100_000
    start = time.monotonic()
    res = g.query("n0", "dependencies", 5)
    assert time.monotonic() - start < 2.0
    assert res["truncated"] is True
    assert len(res["nodes"]) <= 1000 and len(res["edges"]) <= 3000
    assert res["graph_revision"] == 7
