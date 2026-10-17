"""Draft wizard through the API: same validation as an import, no side effects (11.1)."""

from __future__ import annotations

import hashlib
import uuid

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


DRAFT = {
    "alias": "Hall light at dusk",
    "trigger": {"platform": "state", "entity_id": "binary_sensor.door", "to": "on"},
    "conditions": [{"entity_id": "sun.sun", "state": "below_horizon"}],
    "actions": [{"service": "light.turn_on", "entity_ids": ["light.hall"], "brightness_pct": 40}],
}


async def test_draft_lint_and_import(hass, hass_ws_client):
    for eid, state in (("binary_sensor.door", "off"), ("sun.sun", "above_horizon"),
                       ("light.hall", "off")):
        hass.states.async_set(eid, state)
    calls = async_mock_service(hass, "light", "turn_on")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, "modules_autopilot": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    res = await _call(client, type=f"{DOMAIN}/autopilot/draft", **DRAFT)
    assert res["success"], res
    draft = res["result"]
    assert draft["completeness"] == 1.0
    assert "brightness_pct: 40" in draft["yaml"] and "light.hall" in draft["yaml"]
    assert calls == []  # a draft never calls a service (AC AUT 03)

    bad = await _call(client, type=f"{DOMAIN}/autopilot/draft",
                      **{**DRAFT, "actions": [{"service": "light.turn_on",
                                               "entity_ids": ["light.nowhere"]}]})
    assert bad["error"]["code"] == "validation_failed"
    assert bad["error"]["details"]["entity_id"] == "light.nowhere"
    extra = await _call(client, type=f"{DOMAIN}/autopilot/draft", **DRAFT, shell="rm -rf /")
    assert extra["error"]["code"] == "invalid_format"  # unknown fields are rejected

    text = draft["yaml"]
    imp = await _call(client, type=f"{DOMAIN}/import/create", content_utf8=text,
                      content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                      resource_kind="automation", label="draft.yaml",
                      request_key=str(uuid.uuid4()))
    assert imp["success"], imp
    lint = await _call(client, type=f"{DOMAIN}/autopilot/analyze",
                       snapshot_id=imp["result"]["snapshot_id"])
    assert [f["rule_id"] for f in lint["result"]["findings"]] == \
        [f["rule_id"] for f in draft["findings"]]  # identical validation (AC AUT 04)
