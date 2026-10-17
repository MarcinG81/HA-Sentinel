"""RCA end to end on a real HA instance (spec 9.3 reference case, scaled down)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
)


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def test_shared_integration_outage_becomes_one_incident(hass, hass_ws_client, freezer):
    broker = MockConfigEntry(domain="mqtt", title="MQTT broker",
                             state=ConfigEntryState.SETUP_RETRY)
    broker.add_to_hass(hass)
    other = MockConfigEntry(domain="zha", title="Zigbee", state=ConfigEntryState.NOT_LOADED)
    other.add_to_hass(hass)
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    for i in range(4):
        dev = dev_reg.async_get_or_create(config_entry_id=broker.entry_id,
                                          identifiers={("mqtt", f"d{i}")}, name=f"MQTT {i}")
        ent_reg.async_get_or_create("sensor", "mqtt", f"s{i}", suggested_object_id=f"mqtt_{i}",
                                    device_id=dev.id, config_entry=broker)
        hass.states.async_set(f"sensor.mqtt_{i}", "20")
    zdev = dev_reg.async_get_or_create(config_entry_id=other.entry_id,
                                       identifiers={("zha", "z")}, name="Zigbee plug")
    ent_reg.async_get_or_create("sensor", "zha", "z", suggested_object_id="zb", device_id=zdev.id,
                                config_entry=other)
    hass.states.async_set("sensor.zb", "1")

    sentinel = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                               options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                        OPT_DOCTOR_UNAVAILABLE_S: 30})
    sentinel.add_to_hass(hass)
    assert await hass.config_entries.async_setup(sentinel.entry_id)
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    for i in range(4):  # effects spread over ~15 s
        hass.states.async_set(f"sensor.mqtt_{i}", STATE_UNAVAILABLE)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=5))
        async_fire_time_changed(hass)
    runtime = hass.data[DOMAIN]["runtime"]
    for _ in range(15):  # wait for all four findings to mature and be correlated
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        if len(runtime.doctor.active_findings()) == 4:
            break
    for _ in range(3):  # let the final commit and correlation land
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    items = (await _call(client, type=f"{DOMAIN}/incidents/list"))["result"]["items"]
    assert len(items) == 1, items
    inc = items[0]
    assert inc["kind"] == "infrastructure" and inc["member_count"] == 4
    assert inc["root"]["label"] == "MQTT broker"
    assert inc["top_hypothesis"]["label"] == "strong"

    detail = (await _call(client, type=f"{DOMAIN}/incidents/get",
                          incident_id=inc["id"]))["result"]
    top = detail["hypotheses"][0]
    assert top["candidate_label"] == "MQTT broker"
    assert top["evidence_source"].startswith("config_entry_state")
    assert set(top["features"]) == {"T", "D", "P", "E", "K"}

    confirm = await _call(client, type=f"{DOMAIN}/incidents/confirm_cause",
                          incident_id=inc["id"], expected_revision=detail["revision"],
                          candidate_uid=top["candidate_uid"], verdict="confirmed",
                          request_key=str(uuid.uuid4()))
    assert confirm["success"], confirm
    detail = (await _call(client, type=f"{DOMAIN}/incidents/get",
                          incident_id=inc["id"]))["result"]
    assert detail["confirmed_cause"]["verdict"] == "confirmed"
