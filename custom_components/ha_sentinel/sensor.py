"""Diagnostic sensors (spec 14.3). No per-finding sensors and no graph in attributes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SentinelConfigEntry
from .const import DOMAIN
from .runtime import SIGNAL_UPDATED, SentinelRuntime


@dataclass(frozen=True, kw_only=True)
class SentinelSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SentinelRuntime], Any]


SENSORS = (
    SentinelSensorDescription(
        key="health_score",
        translation_key="health_score",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rt: rt.health().score,  # None -> unknown when coverage < 80 %
    ),
    SentinelSensorDescription(
        key="coverage_percent",
        translation_key="coverage_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda rt: (
            float(c) if (c := rt.health().coverage_pct) is not None else None
        ),
    ),
    SentinelSensorDescription(
        key="active_incidents",
        translation_key="active_incidents",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rt: rt.active_incidents,
    ),
    SentinelSensorDescription(
        key="dropped_observations_total",
        translation_key="dropped_observations_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rt: rt.queues.stats.dropped_total,
    ),
    SentinelSensorDescription(
        key="queue_depth",
        translation_key="queue_depth",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda rt: rt.queues.depth,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SentinelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(SentinelSensor(entry, d) for d in SENSORS)


class SentinelSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: SentinelSensorDescription

    def __init__(self, entry: SentinelConfigEntry, description: SentinelSensorDescription):
        self.entity_description = description
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "HA Sentinel",
            "entry_type": "service",
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._runtime)
