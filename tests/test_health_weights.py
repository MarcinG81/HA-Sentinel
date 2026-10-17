"""Health score unit weights and components (spec 8.3)."""

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


async def test_weights_change_the_score_and_components_are_visible(hass, hass_ws_client,
                                                                   freezer):
    hub = MockConfigEntry(domain="zha", title="Zigbee")
    hub.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=hub.entry_id,
                                                 identifiers={("zha", "pump")}, name="Pump")
    er.async_get(hass).async_get_or_create("switch", "zha", "p", suggested_object_id="pump",
                                           device_id=dev.id, config_entry=hub)
    hass.states.async_set("switch.pump", "on")
    hass.states.async_set("sensor.a", "1")
    hass.states.async_set("sensor.b", "1")
    hass.states.async_set("sensor.c", "1")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    for _ in range(100):  # 15 min coverage window
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    for _ in range(5):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert runtime.health().score == 85  # 4 units, high = 0.6: 100 * (1 - 0.6 / 4)

    client = await hass_ws_client(hass)
    comp = (await _call(client, type=f"{DOMAIN}/health/components"))["result"]
    pump = next(u for u in comp["units"] if u["kind"] == "device")
    assert pump["label"] == "Pump" and pump["rules"] == ["DOC001"] and pump["weight"] == 1
    res = await _call(client, type=f"{DOMAIN}/health/weight", unit_uid=pump["unit"], weight=3,
                      request_key=str(uuid.uuid4()))
    assert res["success"], res
    assert res["result"]["score"] == 70  # 100 * (1 - 3 * 0.6 / 6) = 70
    bad = await _call(client, type=f"{DOMAIN}/health/weight", unit_uid="nope", weight=2,
                      request_key=str(uuid.uuid4()))
    assert bad["error"]["code"] == "not_found"

    # Weights survive a reload; weight 1 removes the override.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.unit_weights == {pump["unit"]: 3}
    await runtime.set_unit_weight(pump["unit"], 1, "admin")
    assert runtime.unit_weights == {}
