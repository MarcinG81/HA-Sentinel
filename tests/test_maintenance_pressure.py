"""Maintenance windows (8.2), DOC009 updates and storage pressure / degradation (7.3, 21.2)."""

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


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["runtime"]


async def _tick(hass, freezer, seconds=10, n=1):
    for _ in range(n):
        # The writer wakes on real time while this clock is frozen: drain it first.
        if (rt := hass.data.get(DOMAIN, {}).get("runtime")) is not None:
            await rt.async_flush()
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_device_maintenance_suppresses_failures_and_leaves_score(hass, hass_ws_client,
                                                                      freezer):
    other = MockConfigEntry(domain="zha", title="Zigbee")
    other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id,
                                                 identifiers={("zha", "plug")}, name="Plug")
    er.async_get(hass).async_get_or_create("switch", "zha", "p", suggested_object_id="plug",
                                           device_id=dev.id, config_entry=other)
    hass.states.async_set("switch.plug", "on")
    hass.states.async_set("sensor.other", "1")
    runtime = await _setup(hass)
    await runtime.config.async_rebuild_graph()
    await _tick(hass, freezer, 40)
    client = await hass_ws_client(hass)
    win = await _call(client, type=f"{DOMAIN}/maintenance/create", scope_uid=f"device:{dev.id}",
                      minutes=10, reason="replacing the plug", request_key=str(uuid.uuid4()))
    assert win["success"], win
    assert win["result"]["active"] is True and win["result"]["scope_kind"] == "device"

    hass.states.async_set("switch.plug", STATE_UNAVAILABLE)
    await _tick(hass, freezer, n=8)
    assert runtime.active_incidents == 0  # planned work is not a failure
    assert runtime.overview()["health"]["maintenance_units"] == 1

    # When the window ends and the plug is still down, the failure is reported.
    await _tick(hass, freezer, 60, n=10)
    assert runtime.active_incidents == 1


async def test_cancelled_window_reports_failure(hass, hass_ws_client, freezer):
    hass.states.async_set("sensor.x", "1")
    runtime = await _setup(hass)
    await runtime.config.async_rebuild_graph()
    await _tick(hass, freezer, 40)
    uid = runtime.inventory.uid_for_alias("sensor.x")
    client = await hass_ws_client(hass)
    win = (await _call(client, type=f"{DOMAIN}/maintenance/create", scope_uid=uid, minutes=60,
                       reason="battery swap", request_key=str(uuid.uuid4())))["result"]
    hass.states.async_set("sensor.x", STATE_UNAVAILABLE)
    await _tick(hass, freezer, n=6)
    assert runtime.active_incidents == 0
    await _call(client, type=f"{DOMAIN}/maintenance/cancel", window_id=win["id"],
                request_key=str(uuid.uuid4()))
    await _tick(hass, freezer, 30, n=4)
    assert runtime.active_incidents == 1


async def test_doc009_update_available_is_information(hass, freezer):
    hass.states.async_set("update.hub_firmware", "off", {"latest_version": "1.2"})
    runtime = await _setup(hass)
    hass.states.async_set("update.hub_firmware", "on", {"latest_version": "1.3"})
    await runtime.async_flush()
    findings = [f for f in runtime.doctor.active_findings() if f.rule_id == "DOC009"]
    assert len(findings) == 1 and findings[0].severity.value == "info"
    assert runtime.health().score_exact == 100  # information carries no penalty
    hass.states.async_set("update.hub_firmware", "off", {"latest_version": "1.3"})
    await runtime.async_flush()
    assert not [f for f in runtime.doctor.active_findings() if f.rule_id == "DOC009"]


async def test_storage_pressure_doc010_and_degradation_blocks_heavy_work(
        hass, hass_ws_client, freezer, monkeypatch):
    runtime = await _setup(hass)
    budget = DEFAULT_OPTIONS["storage_max_total_mib"] * 1024 * 1024
    monkeypatch.setattr(runtime.data, "_usage_bytes", lambda: int(budget * 0.97))
    await _tick(hass, freezer, 301)  # pressure is sampled every 5 min
    await _tick(hass, freezer, 30)
    assert runtime.data.storage_level == 2
    assert runtime.degradation_level == 2
    doc010 = list(runtime.data.findings.values())
    assert doc010 and doc010[0].rule_id == "DOC010" and doc010[0].severity.value == "high"

    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/export/create", scopes=["diagnostics"],
                      request_key=str(uuid.uuid4()))
    assert res["error"]["details"]["reason"] == "degraded_level_2"
    assert (await _call(client, type=f"{DOMAIN}/overview"))["result"]["degradation_level"] == 2

    # Back below 75 %: DOC010 resolves, but the level only drops after 5 calm minutes.
    monkeypatch.setattr(runtime.data, "_usage_bytes", lambda: int(budget * 0.10))
    await _tick(hass, freezer, 301)
    await _tick(hass, freezer, 30)
    assert runtime.data.storage_level == 0 and not runtime.data.findings
    assert runtime.degradation_level == 2
    await _tick(hass, freezer, 30, n=11)
    assert runtime.degradation_level == 0


async def test_sentinel_never_observes_its_own_entities(hass, freezer):
    runtime = await _setup(hass)
    await _tick(hass, freezer, 60, n=15)  # health_score stays "unknown" during warm-up
    own = {e.entity_id for e in er.async_get(hass).entities.values() if e.platform == DOMAIN}
    assert own
    assert not [a for a in own if runtime.inventory.uid_for_alias(a)]
    assert runtime.active_incidents == 0
    await runtime.config.async_rebuild_graph()
    assert not [n for n in runtime.config.graph.nodes.values() if n.external_ref in own]
