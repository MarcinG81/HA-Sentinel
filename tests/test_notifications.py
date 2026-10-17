"""Notification outbox delivery (spec 14.3): opt-in, dedup, cooldown, dismissal."""

from __future__ import annotations

import uuid
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.const import STATE_UNAVAILABLE
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
    OPT_NOTIFICATIONS_ENABLED,
)

NAME = {"friendly_name": "Hall temperature"}


async def _setup(hass, enabled: bool):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_NOTIFICATIONS_ENABLED: enabled,
                                     OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.hall_temp", "21", NAME)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["runtime"]


async def _tick(hass, freezer, seconds=10, n=1):
    for _ in range(n):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _notifications(hass) -> dict:
    return persistent_notification._async_get_or_create_notifications(hass)


async def _outbox_states(runtime) -> list[str]:
    return await runtime.db.run(lambda c: [r[0] for r in c.execute(
        "SELECT state FROM notification_outbox ORDER BY created_us")])


async def test_disabled_by_default_nothing_is_sent(hass, freezer):
    runtime = await _setup(hass, enabled=False)
    await _tick(hass, freezer, 40)
    hass.states.async_set("sensor.hall_temp", STATE_UNAVAILABLE, NAME)
    await _tick(hass, freezer, n=8)
    assert runtime.active_incidents == 1
    assert not [n for n in _notifications(hass) if n.startswith("ha_sentinel_")]
    assert await _outbox_states(runtime) == ["skipped"]


async def test_sent_once_dismissed_on_recovery_and_cooldown(hass, freezer):
    runtime = await _setup(hass, enabled=True)
    await _tick(hass, freezer, 40)
    hass.states.async_set("sensor.hall_temp", STATE_UNAVAILABLE, NAME)
    await _tick(hass, freezer, n=8)
    ours = {k: v for k, v in _notifications(hass).items() if k.startswith("ha_sentinel_")}
    assert len(ours) == 1
    note = next(iter(ours.values()))
    assert "Hall temperature" in note["message"] and "/ha-sentinel/doctor" in note["message"]

    hass.states.async_set("sensor.hall_temp", "21", NAME)  # recovery: 30 s stability
    await _tick(hass, freezer, n=8)
    assert runtime.active_incidents == 0
    assert not [k for k in _notifications(hass) if k.startswith("ha_sentinel_")]
    assert await _outbox_states(runtime) == ["dismissed"]

    # The same object fails again within 30 min: a new incident, but no new notification.
    hass.states.async_set("sensor.hall_temp", STATE_UNAVAILABLE, NAME)
    await _tick(hass, freezer, n=8)
    assert runtime.active_incidents == 1
    assert not [k for k in _notifications(hass) if k.startswith("ha_sentinel_")]
    assert (await _outbox_states(runtime))[-1] == "skipped"
