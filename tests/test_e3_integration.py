"""E3 over the real WebSocket API: import, history, diff, lint, graph."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN

V1 = """- id: heat_on
  alias: Heat on
  triggers:
    - trigger: numeric_state
      entity_id: sensor.temp
      below: 20
  actions:
    - action: climate.turn_on
      target:
        entity_id: climate.living
"""
# Same behaviour, only a comment: textual change only (AC HK 01).
V1_COMMENT = "# heating\n" + V1
V2 = V1.replace("climate.living", "climate.livng")


def _h(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
async def client(hass: HomeAssistant, hass_ws_client):
    sentinel = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                               options=dict(DEFAULT_OPTIONS))
    sentinel.add_to_hass(hass)
    other = MockConfigEntry(domain="mqtt", title="MQTT")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("mqtt", "thermo")}, name="Thermo")
    er.async_get(hass).async_get_or_create("sensor", "mqtt", "temp", suggested_object_id="temp",
                                           device_id=device.id, config_entry=other)
    hass.states.async_set("sensor.temp", "21")
    hass.states.async_set("climate.living", "off")
    hass.services.async_register("climate", "turn_on", lambda call: None)
    assert await hass.config_entries.async_setup(sentinel.entry_id)
    await hass.async_block_till_done()
    return await hass_ws_client(hass)


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _import(client, text, label="automations.yaml", **extra):
    return await _call(client, type=f"{DOMAIN}/import/create", content_utf8=text,
                       content_hash=_h(text), resource_kind="automation", label=label,
                       request_key=str(uuid.uuid4()), **extra)


async def test_import_history_diff_and_lint(hass, client):
    first = await _import(client, V1)
    assert first["success"], first
    res = first["result"]
    assert res["raw_kept"] is True and res["restorable"] is False
    rid = res["resource_id"]

    again = await _import(client, V1, resource_id=rid)
    assert again["result"]["deduplicated"] is True

    comment = await _import(client, V1_COMMENT, resource_id=rid)
    changed = await _import(client, V2, resource_id=rid)
    assert not comment["result"]["deduplicated"] and not changed["result"]["deduplicated"]

    snaps = (await _call(client, type=f"{DOMAIN}/snapshots/list", resource_id=rid))["result"]
    assert len(snaps["items"]) == 3

    d1 = (await _call(client, type=f"{DOMAIN}/snapshots/diff",
                      from_snapshot_id=res["snapshot_id"],
                      to_snapshot_id=comment["result"]["snapshot_id"]))["result"]
    assert d1["semantic"] == [] and d1["textual_changed"] is True
    assert d1["semantic_changed"] is False
    d2 = (await _call(client, type=f"{DOMAIN}/snapshots/diff",
                      from_snapshot_id=comment["result"]["snapshot_id"],
                      to_snapshot_id=changed["result"]["snapshot_id"]))["result"]
    assert [c["path"] for c in d2["semantic"]] == ["/0/actions/0/target/entity_id"]

    lint = (await _call(client, type=f"{DOMAIN}/autopilot/analyze",
                        snapshot_id=changed["result"]["snapshot_id"]))["result"]
    aut001 = next(f for f in lint["findings"] if f["rule_id"] == "AUT001")
    assert aut001["params"]["entity_id"] == "climate.livng"
    assert aut001["span"]["line"] == 10
    assert lint["completeness"] == 1.0

    ok = (await _call(client, type=f"{DOMAIN}/autopilot/analyze",
                      snapshot_id=res["snapshot_id"]))["result"]
    assert [f["rule_id"] for f in ok["findings"]] == []

    export = (await _call(client, type=f"{DOMAIN}/snapshots/get",
                          snapshot_id=comment["result"]["snapshot_id"]))["result"]
    assert export["source"] == "raw" and export["content"].startswith("# heating")

    resources = (await _call(client, type=f"{DOMAIN}/resources/list"))["result"]["items"]
    kinds = {r["kind"] for r in resources}
    assert kinds == {"automation", "sentinel_options"}  # type S snapshot exists too


async def test_plain_secret_refused_then_redacted(hass, client):
    text = "- alias: x\n  actions:\n    - action: rest_command.go\n      data:\n" \
           "        password: hunter2\n"
    refused = await _import(client, text)
    assert refused["error"]["code"] == "validation_failed"
    accepted = await _import(client, text, redact_secrets=True)
    res = accepted["result"]
    assert res["raw_kept"] is False and res["restorable"] is False
    assert res["redacted_paths"] == ["/0/actions/0/data/password"]
    export = (await _call(client, type=f"{DOMAIN}/snapshots/get",
                          snapshot_id=res["snapshot_id"]))["result"]
    assert "hunter2" not in export["content"] and export["source"] == "rendered"


async def test_bad_yaml_and_hash_mismatch_rejected(hass, client):
    bomb = "a: !!python/object/apply:os.system ['id']"
    assert (await _import(client, bomb))["error"]["code"] == "validation_failed"
    msg = await _call(client, type=f"{DOMAIN}/import/create", content_utf8=V1,
                      content_hash=_h("other"), resource_kind="automation", label="x",
                      request_key=str(uuid.uuid4()))
    assert msg["error"]["code"] == "invalid_request"


async def test_graph_dependents_after_import(hass, client):
    await _import(client, V1)
    runtime = hass.data[DOMAIN]["runtime"]
    await runtime.config.async_rebuild_graph()
    search = (await _call(client, type=f"{DOMAIN}/graph/search", q="sensor.temp"))["result"]
    node = next(n for n in search["items"] if n["external_ref"] == "sensor.temp")
    deps = (await _call(client, type=f"{DOMAIN}/graph/query", uid=node["uid"],
                        direction="dependents", depth=2))["result"]
    assert "Heat on" in {n["label"] for n in deps["nodes"]}
    up = (await _call(client, type=f"{DOMAIN}/graph/query", uid=node["uid"],
                      direction="dependencies", depth=3))["result"]
    assert [e["relation"] for e in up["edges"]] == ["provided_by", "managed_by"]


async def test_validation_errors_carry_safe_details(hass, client):
    text = "- alias: x\n  data:\n    token: abc123\n"
    err = (await _import(client, text))["error"]
    assert err["details"] == {"reason": "plain_secrets", "paths": ["/0/data/token"]}
    assert "abc123" not in str(err)
    bad = (await _import(client, "a: [1, 2\nb: 3"))["error"]
    assert bad["details"]["reason"] == "syntax" and bad["details"]["line"] >= 1
