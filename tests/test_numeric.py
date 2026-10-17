"""Explicit numeric sources: manual battery mapping and DOC008 signal (5.1, 8.1, 27.1)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
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
                                     **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["runtime"]


async def _tick(hass, freezer, seconds=10, n=1):
    for _ in range(n):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _active(runtime, rule):
    return [f.subject_uid for f in runtime.doctor.active_findings() if f.rule_id == rule]


async def test_manual_battery_mapping_drives_doc004(hass, hass_ws_client, freezer):
    hass.states.async_set("sensor.lock_power", "200")  # 0-255 raw level, no device_class
    _, runtime = await _setup(hass)
    await _tick(hass, freezer, 40)
    uid = runtime.inventory.uid_for_alias("sensor.lock_power")
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/numeric/set", subject_uid=uid, purpose="battery",
                      scale_min=0, scale_max=255, request_key=str(uuid.uuid4()))
    assert res["success"], res
    assert res["result"]["sampling"] is True  # battery mapping needs no sampling option
    await client.close()  # the time jumps below would outlive its token

    hass.states.async_set("sensor.lock_power", "40")  # 15.7 %
    await _tick(hass, freezer, 60)
    hass.states.async_set("sensor.lock_power", "38")
    await _tick(hass, freezer, 1800)
    hass.states.async_set("sensor.lock_power", "37")
    await _tick(hass, freezer, 10)
    await runtime.async_flush()  # telemetry is drained by the writer, not the tick
    assert _active(runtime, "DOC004") == [uid]

    await runtime.numeric.delete(res["result"]["id"], "admin")
    assert _active(runtime, "DOC004") == []  # closed with the mapping, never "recovered"


async def test_signal_source_requires_sampling_option(hass, hass_ws_client, freezer):
    hass.states.async_set("sensor.plug_lqi", "200")
    _, runtime = await _setup(hass)
    await _tick(hass, freezer, 40)
    uid = runtime.inventory.uid_for_alias("sensor.plug_lqi")
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/numeric/set", subject_uid=uid, purpose="signal",
                      scale_min=0, scale_max=255, threshold=60, request_key=str(uuid.uuid4()))
    assert res["error"]["code"] == "unsupported_capability"
    assert res["error"]["details"]["reason"] == "numeric_sampling_disabled"


async def test_signal_source_raises_doc008(hass, hass_ws_client, freezer):
    er.async_get(hass).async_get_or_create("sensor", "zha", "lqi",
                                           suggested_object_id="plug_lqi")
    hass.states.async_set("sensor.plug_lqi", "200")
    entry, runtime = await _setup(hass, ingestion_numeric_sampling=True)
    await _tick(hass, freezer, 40)
    uid = runtime.inventory.uid_for_alias("sensor.plug_lqi")
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/numeric/set", subject_uid=uid, purpose="signal",
                      scale_min=0, scale_max=255, threshold=60, request_key=str(uuid.uuid4()))
    assert res["success"], res
    await client.close()
    hass.states.async_set("sensor.plug_lqi", "40")
    await _tick(hass, freezer, 60)
    hass.states.async_set("sensor.plug_lqi", "42")
    await _tick(hass, freezer, 1800)
    hass.states.async_set("sensor.plug_lqi", "41")
    await _tick(hass, freezer, 10)
    await runtime.async_flush()
    assert _active(runtime, "DOC008") == [uid]
    assert runtime.active_incidents == 1

    # The mapping survives a reload and the baseline re-reads the current value.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert "sensor.plug_lqi" in runtime._numeric_aliases
