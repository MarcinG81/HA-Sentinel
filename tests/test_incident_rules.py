"""Switching a rule off straight from an incident: per objects or for the whole device."""

from __future__ import annotations

import uuid
from datetime import timedelta

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


async def _tick(hass, freezer, seconds=10, n=1):
    for _ in range(n):
        if (rt := hass.data.get(DOMAIN, {}).get("runtime")) is not None:
            await rt.async_flush()
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_disable_rule_for_device_from_incident(hass, hass_ws_client, freezer):
    hub = MockConfigEntry(domain="miele", title="Miele")
    hub.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=hub.entry_id,
                                                 identifiers={("miele", "dryer")},
                                                 name="Tumble dryer")
    reg = er.async_get(hass)
    for i in range(3):
        reg.async_get_or_create("sensor", "miele", f"d{i}", suggested_object_id=f"dryer_{i}",
                                device_id=dev.id, config_entry=hub)
        hass.states.async_set(f"sensor.dryer_{i}", "idle")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    await runtime.config.async_rebuild_graph()
    await _tick(hass, freezer, 40)
    for i in range(3):
        hass.states.async_set(f"sensor.dryer_{i}", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    await runtime.async_correlate()
    assert runtime.active_incidents == 1

    client = await hass_ws_client(hass)
    inc = (await _call(client, type=f"{DOMAIN}/incidents/list"))["result"]["items"][0]
    detail = (await _call(client, type=f"{DOMAIN}/incidents/get", incident_id=inc["id"]))
    rules = detail["result"]["rules"]
    assert [r["rule_id"] for r in rules] == ["DOC001"]
    assert len(rules[0]["subject_uids"]) == 3
    assert [x["entity_id"] for x in rules[0]["subjects"]] == [
        "sensor.dryer_0", "sensor.dryer_1", "sensor.dryer_2"]
    assert rules[0]["device"] == {"uid": f"device:{dev.id}", "name": "Tumble dryer"}

    res = await _call(client, type=f"{DOMAIN}/rules/set", rule_id="DOC001",
                      subject_uid=rules[0]["device"]["uid"], enabled=False,
                      request_key=str(uuid.uuid4()))
    assert res["success"] and res["result"]["subject_kind"] == "device"
    await runtime.async_correlate()
    assert runtime.active_incidents == 0
    contract = await _call(client, type=f"{DOMAIN}/rules/set", rule_id="DOC005",
                           subject_uid=f"device:{dev.id}", enabled=True,
                           params={"period_s": 60}, request_key=str(uuid.uuid4()))
    assert contract["error"]["details"]["param"] == "subject_uid"  # contracts stay per entity
    await client.close()

    # A sensor added to the device later is covered by the device-wide setting.
    reg.async_get_or_create("sensor", "miele", "d9", suggested_object_id="dryer_9",
                            device_id=dev.id, config_entry=hub)
    hass.states.async_set("sensor.dryer_9", "idle")
    await _tick(hass, freezer, 10)
    hass.states.async_set("sensor.dryer_9", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    assert [f for f in runtime.doctor.active_findings() if f.rule_id == "DOC001"] == []


async def test_bulk_disable_for_incident_subjects(hass, hass_ws_client):
    for i in range(3):
        hass.states.async_set(f"sensor.watch_{i}", "1")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    uids = [runtime.inventory.uid_for_alias(f"sensor.watch_{i}") for i in range(3)]
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/rules/set_many", rule_id="DOC001",
                      subject_uids=uids, enabled=False, request_key=str(uuid.uuid4()))
    assert res["success"] and len(res["result"]["override_ids"]) == 3
    assert {o.subject_uid for o in runtime.rules.overrides} == set(uids)
    revisions = {o.policy_revision for o in runtime.rules.overrides}
    assert len(revisions) == 1  # one change, one policy revision
