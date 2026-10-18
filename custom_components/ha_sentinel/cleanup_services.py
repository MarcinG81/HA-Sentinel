"""Cleanup report: Home Assistant entities and devices inactive longer than the cleanup
window (option, default 30 days).

This is a report only. Sentinel never removes objects from Home Assistant (spec 3.2:
no automatic removal of HA objects; 1.1: no mass repair of entities); each item links to
the HA page where the admin decides. Disabled entities are skipped: switching them off was
already a decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .adapters.compat import device_entry_ids
from .adapters.compat import devices as compat_devices
from .const import DEFAULT_OPTIONS, DOMAIN, OPT_CLEANUP_WINDOW_DAYS
from .core.clock import us_to_rfc3339
from .modules.doctor.engine import STATELESS_DOMAINS

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

MAX_ITEMS = 500
S = 1_000_000


def _us(value: datetime | None) -> int | None:
    return int(value.timestamp() * S) if value else None


class CleanupService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime

    @property
    def window_days(self) -> int:
        opts = {**DEFAULT_OPTIONS, **self.rt.entry.options}
        return int(opts[OPT_CLEANUP_WINDOW_DAYS])

    def report(self) -> dict[str, Any]:
        hass = self.rt.hass
        now = self.rt.clock.now_us()
        cutoff = now - self.window_days * 86_400 * S
        loaded = {e.entry_id for e in hass.config_entries.async_entries()
                  if e.state is ConfigEntryState.LOADED}
        known_entries = {e.entry_id for e in hass.config_entries.async_entries()}
        ent_reg = er.async_get(hass)
        stale_by_device: dict[str, int] = {}
        entities_by_device: dict[str, int] = {}
        items: list[dict[str, Any]] = []

        for entry in ent_reg.entities.values():
            if entry.platform == DOMAIN or entry.disabled_by is not None:
                continue
            if entry.device_id:
                entities_by_device[entry.device_id] = entities_by_device.get(entry.device_id,
                                                                             0) + 1
            state = hass.states.get(entry.entity_id)
            reason, since = None, None
            if entry.config_entry_id and entry.config_entry_id not in known_entries:
                reason, since = "integration_removed", _us(entry.modified_at)
            elif entry.config_entry_id and entry.config_entry_id not in loaded:
                reason, since = "integration_not_loaded", _us(entry.modified_at)
            elif state is None:
                reason, since = "no_state", _us(entry.modified_at)
            elif state.attributes.get("restored"):
                # HA keeps showing it, but no integration provides it any more.
                reason, since = "orphaned", _us(state.last_changed)
            elif state.state == STATE_UNAVAILABLE or (
                    state.state == STATE_UNKNOWN and entry.domain not in STATELESS_DOMAINS):
                reason, since = f"state_{state.state}", _us(state.last_changed)
            if reason is None or since is None or since > cutoff:
                continue
            if entry.device_id:
                stale_by_device[entry.device_id] = stale_by_device.get(entry.device_id, 0) + 1
            items.append({
                "kind": "entity", "id": entry.entity_id,
                "name": (state.name if state else None) or entry.name or entry.original_name,
                "reason": reason, "since": us_to_rfc3339(since),
                "days": (now - since) // (86_400 * S),
                "link": f"/config/entities?search={entry.entity_id}",
            })

        dev_reg = dr.async_get(hass)
        for dev in compat_devices(dev_reg):
            total = entities_by_device.get(dev.id, 0)
            entries = device_entry_ids(dev)
            if entries and all(e not in known_entries for e in entries):
                reason = "integration_removed"
            elif total and stale_by_device.get(dev.id, 0) == total:
                reason = "all_entities_inactive"
            else:
                continue
            items.append({
                "kind": "device", "id": dev.id, "name": dev.name_by_user or dev.name,
                "reason": reason, "since": None, "days": None,
                "entities": total, "link": f"/config/devices/device/{dev.id}",
            })

        items.sort(key=lambda i: (i["kind"] != "device", -(i["days"] or 0), i["name"] or ""))
        return {"window_days": self.window_days, "items": items[:MAX_ITEMS],
                "truncated": len(items) > MAX_ITEMS,
                "last_purge": self._last_purge()}

    def _last_purge(self) -> dict[str, Any] | None:
        last = self.rt.data.last_purge if self.rt.data else None
        if not last:
            return None
        return {"at": us_to_rfc3339(last["at_us"]), "entities": last.get("entities", 0),
                "devices": last.get("devices", 0)}
