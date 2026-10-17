"""Module switches and observed domains (spec 27.1, 4.1)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
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


async def _setup(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["runtime"]


async def _tick(hass, freezer, seconds=10, n=1):
    for _ in range(n):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_disabled_modules_answer_unsupported(hass, hass_ws_client):
    _, runtime = await _setup(hass, modules_network=False, modules_config_history=False,
                              modules_dependency_mapper=False)
    client = await hass_ws_client(hass)
    for msg, module in (
        ({"type": f"{DOMAIN}/autopilot/analyze", "snapshot_id": str(uuid.uuid4())},
         "autopilot"),  # off by default (spec 27.1)
        ({"type": f"{DOMAIN}/network/inventory"}, "network"),
        ({"type": f"{DOMAIN}/resources/list"}, "config_history"),
        ({"type": f"{DOMAIN}/graph/query", "uid": "x"}, "dependency_mapper"),
    ):
        err = (await _call(client, **msg))["error"]
        assert err["code"] == "unsupported_capability", msg
        assert err["details"] == {"reason": "module_disabled", "module": module}
    assert runtime.network is None
    caps = {c["capability_id"]: c for c in
            (await _call(client, type=f"{DOMAIN}/capabilities"))["result"]["capabilities"]}
    assert caps["network.probe"]["status"] == "disabled"
    modules = (await _call(client, type=f"{DOMAIN}/overview"))["result"]["modules"]
    assert modules["network"] == modules["config_history"] == "disabled"
    assert modules["doctor"] == "ready"
    # Search stays available: it is an inventory lookup used by other cards.
    assert (await _call(client, type=f"{DOMAIN}/graph/search", q="x"))["success"]


async def test_doctor_off_closes_findings_and_score_is_unknown(hass, freezer):
    hass.states.async_set("switch.pump", "on")
    entry, runtime = await _setup(hass)
    await _tick(hass, freezer, 40)
    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    assert runtime.active_incidents == 1

    hass.config_entries.async_update_entry(entry, options={**entry.options,
                                                           "modules_doctor": False})
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.doctor.active_findings() == []
    assert runtime.active_incidents == 0
    overview = runtime.overview()
    assert overview["modules"]["doctor"] == "disabled"
    assert overview["health"]["score"] is None  # no rules ran: never "healthy"
    hass.states.async_set("switch.pump", "on")
    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    assert runtime.doctor.active_findings() == []


async def test_allowed_domains_limit_observation(hass, freezer):
    hass.states.async_set("switch.pump", "on")
    hass.states.async_set("sensor.temp", "21")
    hass.states.async_set("person.anna", "home")
    _, runtime = await _setup(hass, ingestion_allowed_domains=["switch", "person"])
    await _tick(hass, freezer, 40)
    assert runtime.inventory.uid_for_alias("switch.pump") is not None
    assert runtime.inventory.uid_for_alias("sensor.temp") is None
    assert runtime.inventory.uid_for_alias("person.anna") is None  # privacy wins
