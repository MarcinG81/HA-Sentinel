"""Registry snapshot adapter (spec 12.1). Collects plain data on the HA loop so that graph
building and lint can run in an executor without touching HA objects.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN
from ..core.inventory import Inventory
from ..modules.dependency_mapper.graph import DeviceInfo, EntityInfo, EntryInfo
from .compat import belongs_to, device_entry_ids
from .compat import devices as compat_devices
from .ha_states import is_observed


@dataclass(slots=True)
class RegistrySnapshot:
    entities: list[EntityInfo]
    devices: list[DeviceInfo]
    entries: list[EntryInfo]
    areas: dict[str, str]
    # Lint context
    active_entity_ids: frozenset[str]
    renamed: dict[str, str]  # old alias -> current alias
    deleted: frozenset[str]
    services: dict[str, frozenset[str]]

    def entity_status(self, entity_id: str) -> tuple[str, str | None]:
        if entity_id in self.active_entity_ids:
            return "active", None
        if entity_id in self.renamed:
            return "renamed", self.renamed[entity_id]
        if entity_id in self.deleted:
            return "deleted", None
        return "unknown", None

    def service_exists(self, domain: str, service: str) -> bool:
        return service in self.services.get(domain, frozenset())


@callback
def async_snapshot(hass: HomeAssistant, inventory: Inventory) -> RegistrySnapshot:
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    entities: list[EntityInfo] = []
    seen: set[str] = set()
    for entry in ent_reg.entities.values():
        if not is_observed(entry.entity_id) or entry.platform == DOMAIN:
            continue
        # Registry id first: a rename must not create a second node (AC MAP 01).
        uid = (inventory.uid_for_registry(entry.id) or inventory.uid_for_alias(entry.entity_id)
               or f"entity:{entry.id}")
        state = hass.states.get(entry.entity_id)
        entities.append(EntityInfo(
            uid=uid, entity_id=entry.entity_id,
            name=(state.name if state else None) or entry.name or entry.original_name,
            device_id=entry.device_id, area_id=entry.area_id,
            config_entry_id=entry.config_entry_id,
        ))
        seen.add(entry.entity_id)
    for state in hass.states.async_all():
        # Entities without a registry entry still exist and can be referenced.
        if (state.entity_id in seen or not is_observed(state.entity_id)
                or ent_reg.async_get(state.entity_id) is not None):
            continue  # registered entities were handled (or deliberately skipped) above
        uid = inventory.uid_for_alias(state.entity_id) or f"entity:{state.entity_id}"
        entities.append(EntityInfo(uid, state.entity_id, state.name, None, None, None))
        seen.add(state.entity_id)

    # Sentinel's own device is not part of the home being monitored.
    own = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    devices = [
        DeviceInfo(d.id, d.name_by_user or d.name, d.area_id, device_entry_ids(d))
        for d in compat_devices(dev_reg) if not belongs_to(d, own)
    ]
    entries = [EntryInfo(e.entry_id, e.domain, e.title)
               for e in hass.config_entries.async_entries()]
    areas = {a.id: a.name for a in area_reg.async_list_areas()}

    # Privacy-excluded domains are real entities too: they must not look "missing" to lint.
    active = frozenset(seen | {s.entity_id for s in hass.states.async_all()}
                       | set(ent_reg.entities.keys()))
    renamed: dict[str, str] = {}
    deleted: set[str] = set()
    for ent in inventory.all_entities():
        for alias in ent.aliases:
            if alias.alias == ent.current_alias or alias.alias in active:
                continue
            if ent.deleted_at_us is None:
                renamed[alias.alias] = ent.current_alias
        if ent.deleted_at_us is not None and ent.current_alias not in active:
            deleted.add(ent.current_alias)
    services = {domain: frozenset(svcs) for domain, svcs in
                hass.services.async_services().items()}
    return RegistrySnapshot(entities, devices, entries, areas, active, renamed,
                            frozenset(deleted), services)
