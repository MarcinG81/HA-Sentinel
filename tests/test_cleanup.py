"""Cleanup window: Sentinel tombstones purged, HA objects reported (never removed)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN

DAY = 86_400_000_000


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, "cleanup_window_days": 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["runtime"]


async def test_old_tombstones_and_their_settings_are_purged(hass, freezer):
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "demo", "gone", suggested_object_id="gone")
    reg.async_get_or_create("sensor", "demo", "kept", suggested_object_id="kept")
    hass.states.async_set("sensor.gone", "1")
    hass.states.async_set("sensor.kept", "1")
    runtime = await _setup(hass)
    gone = runtime.inventory.uid_for_alias("sensor.gone")
    await runtime.rules.set(rule_id="DOC001", subject_uid=gone, enabled=False, params={},
                            actor="admin")
    reg.async_remove("sensor.gone")
    hass.states.async_remove("sensor.gone")
    await hass.async_block_till_done()
    await runtime.async_flush()
    assert runtime.inventory.get(gone).deleted_at_us is not None

    totals = await runtime.data.async_purge()
    assert totals["entities"] == 0  # inside the window: the tombstone and setting stay
    assert len(runtime.rules.overrides) == 1

    freezer.tick(timedelta(days=31))
    totals = await runtime.data.async_purge()
    assert totals["entities"] == 1
    assert runtime.inventory.get(gone) is None and runtime.rules.overrides == []
    left = await runtime.db.run(lambda c: c.execute(
        "SELECT COUNT(*) FROM entities WHERE uid=?", (gone,)).fetchone()[0])
    assert left == 0
    assert runtime.inventory.uid_for_alias("sensor.kept") is not None


async def test_cleanup_report_lists_but_never_removes(hass, hass_ws_client, freezer):
    hub = MockConfigEntry(domain="demo", title="Demo")
    hub.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=hub.entry_id,
                                                 identifiers={("demo", "dryer")},
                                                 name="Old dryer")
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "demo", "dryer", suggested_object_id="dryer",
                            device_id=dev.id, config_entry=hub)
    reg.async_get_or_create("sensor", "demo", "fine", suggested_object_id="fine")
    reg.async_get_or_create("button", "demo", "press", suggested_object_id="press")
    off = reg.async_get_or_create("sensor", "demo", "off", suggested_object_id="off")
    reg.async_update_entity(off.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    hass.states.async_set("sensor.dryer", STATE_UNAVAILABLE)
    hass.states.async_set("sensor.fine", "1")
    hass.states.async_set("button.press", "unknown")
    hass.states.async_set("sensor.ghost", "1", {"restored": True})
    runtime = await _setup(hass)
    client = await hass_ws_client(hass)
    report = (await _call(client, type=f"{DOMAIN}/cleanup/report"))["result"]
    assert report["window_days"] == 30 and report["items"] == []  # nothing old yet
    await client.close()

    freezer.tick(timedelta(days=31))
    hass.states.async_set("sensor.fine", "2")  # still active
    report = runtime.cleanup.report()
    by_id = {i["id"]: i for i in report["items"]}
    # The demo entry is not loaded in this test, so its entities count as such.
    assert by_id["sensor.dryer"]["reason"] in ("state_unavailable", "integration_not_loaded")
    assert by_id[dev.id]["kind"] == "device"
    assert by_id[dev.id]["link"] == f"/config/devices/device/{dev.id}"
    assert "button.press" not in by_id  # unknown by nature
    assert "sensor.off" not in by_id  # disabled on purpose
    assert reg.async_get("sensor.dryer") is not None  # nothing removed from HA
