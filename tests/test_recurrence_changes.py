"""Recurrence of a resolved incident and change_related hypotheses (spec 9.1, 9.2)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
)
from custom_components.ha_sentinel.repositories import repository as repo

AUTOMATION = """
- id: pump_night
  alias: Pump at night
  triggers: [{trigger: state, entity_id: sun.sun, to: below_horizon}]
  actions: [{action: switch.turn_on, target: {entity_id: switch.pump}}]
"""


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _tick(hass, freezer, seconds=10, n=1):
    for _ in range(n):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def _import(client, text, resource_id=None):
    msg = {"type": f"{DOMAIN}/import/create", "content_utf8": text,
           "content_hash": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
           "resource_kind": "automation", "label": "pump.yaml",
           "request_key": str(uuid.uuid4())}
    if resource_id:
        msg["resource_id"] = resource_id
    res = await _call(client, **msg)
    assert res["success"], res
    return res["result"]


async def test_recurrence_and_change_related(hass, hass_ws_client, freezer):
    er.async_get(hass).async_get_or_create("switch", "demo", "pump", suggested_object_id="pump")
    hass.states.async_set("switch.pump", "on")
    hass.states.async_set("sun.sun", "above_horizon")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    await _tick(hass, freezer, 40)

    client = await hass_ws_client(hass)
    first = await _import(client, AUTOMATION)
    await _import(client, AUTOMATION.replace("Pump at night", "Pump late"), first["resource_id"])
    await client.close()
    await runtime.config.async_rebuild_graph()

    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    client = await hass_ws_client(hass)
    inc1 = (await _call(client, type=f"{DOMAIN}/incidents/list"))["result"]["items"][0]
    detail = (await _call(client, type=f"{DOMAIN}/incidents/get", incident_id=inc1["id"]))
    changes = detail["result"]["change_hypotheses"]
    assert [c["candidate_uid"] for c in changes] == [f"resource:{first['resource_id']}"]
    assert changes[0]["relation"] == "controls" and changes[0]["minutes_before"] <= 30
    assert detail["result"]["recurrence_of"] is None
    await client.close()

    hass.states.async_set("switch.pump", "on")
    await _tick(hass, freezer, 40, n=2)
    assert runtime.active_incidents == 0
    await _tick(hass, freezer, 3600)  # outside the change window now
    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    # (time jumps outlive the WS token: read through the repository)
    active = await runtime.db.run(repo.load_active_incidents)
    assert len(active) == 1 and active[0]["id"] != inc1["id"]
    inc2 = await runtime.db.run(lambda c: repo.get_incident(c, active[0]["id"]))
    assert inc2["summary"]["recurrence_of"] == inc1["id"]
    assert inc2["summary"]["change_hypotheses"] == []  # the change is older than 30 min
