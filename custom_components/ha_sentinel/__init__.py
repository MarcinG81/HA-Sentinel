"""HA Sentinel integration entry point."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .adapters.panel import async_register_panel, async_remove_panel
from .const import DOMAIN
from .runtime import SentinelRuntime
from .websocket_api import async_register_api

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

type SentinelConfigEntry = ConfigEntry[SentinelRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: SentinelConfigEntry) -> bool:
    runtime = SentinelRuntime(hass, entry)
    await runtime.async_start()
    try:
        entry.runtime_data = runtime
        hass.data.setdefault(DOMAIN, {})["runtime"] = runtime
        # Registered once per domain; handlers answer not_ready while no entry is loaded.
        async_register_api(hass)
        integration = await async_get_integration(hass, DOMAIN)
        await async_register_panel(hass, str(integration.version))
    except Exception:
        # Never leave listeners, timers or an open DB behind (spec 2.2).
        hass.data.get(DOMAIN, {}).pop("runtime", None)
        await runtime.async_stop()
        raise
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SentinelConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_remove_panel(hass)
        hass.data.get(DOMAIN, {}).pop("runtime", None)
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: SentinelConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
