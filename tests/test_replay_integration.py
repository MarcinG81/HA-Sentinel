"""Replay on a real HA with a real recorder: T10 (zero service calls), AC AUT 03."""

from __future__ import annotations

import hashlib
import uuid

from homeassistant.const import EVENT_CALL_SERVICE
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN

AUTOMATIONS = """- id: heat
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
      target:
        entity_id: switch.heater
    - delay: 10
"""


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def test_t10_replay_uses_history_and_calls_nothing(recorder_mock, hass, hass_ws_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("input_boolean.home", "on")
    hass.states.async_set("sensor.temp", "22")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.temp", "19")  # crosses below 20
    hass.states.async_set("sensor.temp", "18")  # stays below: no new trigger
    await async_wait_recording_done(hass)

    client = await hass_ws_client(hass)
    caps = {c["capability_id"]: c["status"] for c in (await _call(
        client, type=f"{DOMAIN}/capabilities"))["result"]["capabilities"]}
    assert caps["automation.replay"] == "supported"
    imp = (await _call(client, type=f"{DOMAIN}/import/create", content_utf8=AUTOMATIONS,
                       content_hash="sha256:" + hashlib.sha256(AUTOMATIONS.encode()).hexdigest(),
                       resource_kind="automation", label="a.yaml",
                       request_key=str(uuid.uuid4())))["result"]

    service_calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, lambda e: service_calls.append(e.data))
    res = await _call(client, type=f"{DOMAIN}/autopilot/replay",
                      snapshot_id=imp["snapshot_id"], hours=1)
    await hass.async_block_till_done()
    assert res["success"], res
    report = res["result"]
    assert service_calls == []  # replay never calls services, not even automation.trigger
    heat = [o for o in report["occurrences"] if o["automation"] == "Heat"]
    assert len(heat) == 1 and heat[0]["to"] == "19"
    assert heat[0]["would_run"] == "true"
    assert heat[0]["planned_actions"][0]["service"] == "switch.turn_on"
    assert heat[0]["plan_complete"] is False  # delay needs a time model
    assert report["supported_nodes"] == report["total_nodes"] == 1
    assert report["source"] == "recorder" and report["interpreter_version"] == 1


async def test_replay_without_recorder_is_unsupported(hass, hass_ws_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    imp = (await _call(client, type=f"{DOMAIN}/import/create", content_utf8=AUTOMATIONS,
                       content_hash="sha256:" + hashlib.sha256(AUTOMATIONS.encode()).hexdigest(),
                       resource_kind="automation", label="a.yaml",
                       request_key=str(uuid.uuid4())))["result"]
    res = await _call(client, type=f"{DOMAIN}/autopilot/replay", snapshot_id=imp["snapshot_id"])
    assert res["error"]["code"] == "unsupported_capability"
    assert res["error"]["details"]["reason"] == "recorder_unavailable"
