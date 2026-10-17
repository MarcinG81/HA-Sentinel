"""binary_sensor.sentinel_degraded: Sentinel liveness, separate from home health (spec 20.2)."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SentinelConfigEntry
from .const import DOMAIN
from .runtime import SIGNAL_UPDATED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SentinelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SentinelDegraded(entry)])


class SentinelDegraded(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "sentinel_degraded"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: SentinelConfigEntry) -> None:
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_sentinel_degraded"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self._runtime.degraded

    @property
    def extra_state_attributes(self) -> dict:
        return {"reason_code": self._runtime.reason_code}
