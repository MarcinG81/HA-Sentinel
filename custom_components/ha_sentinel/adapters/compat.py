"""Compatibility layer for Home Assistant APIs that changed between supported versions.

Supported range: HA 2026.5 .. 2026.10 (see docs/COMPATIBILITY.md). Each helper uses the
current API when present and falls back to the older one, so neither line emits
deprecation warnings. No reflection on private attributes, no blanket exception catching.
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.helpers import device_registry as dr


def devices(registry: dr.DeviceRegistry) -> list[dr.DeviceEntry]:
    """All device entries.

    HA 2026.10 iterates `registry.devices` as entries and deprecates mapping access;
    older versions expose a mapping whose iteration yields device ids.
    """
    items = list(iter(registry.devices))
    if items and isinstance(items[0], str):
        return list(registry.devices.values())
    return items


def device_entry_ids(device: dr.DeviceEntry) -> tuple[str, ...]:
    """Config entries of a device.

    HA 2026.10 gives a device a single `config_entry_id` and deprecates
    `config_entries`; older versions only have the set.
    """
    if hasattr(device, "config_entry_id"):
        return (device.config_entry_id,) if device.config_entry_id else ()
    return tuple(sorted(device.config_entries))


def belongs_to(device: dr.DeviceEntry, entry_ids: Iterable[str]) -> bool:
    wanted = set(entry_ids)
    return any(e in wanted for e in device_entry_ids(device))
