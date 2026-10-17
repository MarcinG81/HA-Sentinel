"""Inventory: stable identity of entities and devices (spec 6.1, AC MAP 01-02, T07).

EntityUid comes from the registry entry ID; entity_id is a mutable alias. An entity
without a registry entry gets a local UUID bound to boot_id and is never merged across
restarts. Inventory never deletes HA objects - it only marks tombstones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import new_uuid


@dataclass(slots=True)
class AliasRecord:
    alias: str
    valid_from_us: int
    valid_to_us: int | None = None
    boot_id: str | None = None


@dataclass(slots=True)
class EntityRecord:
    uid: str
    domain: str
    current_alias: str
    first_seen_us: int
    last_seen_us: int
    registry_id: str | None = None
    device_uid: str | None = None
    deleted_at_us: int | None = None
    aliases: list[AliasRecord] = field(default_factory=list)


@dataclass(slots=True)
class DeviceRecord:
    uid: str
    ha_device_id: str
    deleted_at_us: int | None = None


@dataclass(slots=True)
class InventoryChange:
    """A change to be persisted by the repository."""

    entity: EntityRecord | None = None
    device: DeviceRecord | None = None
    closed_alias: AliasRecord | None = None
    new_alias: AliasRecord | None = None
    # A new registry entity reusing the entity_id of a tombstone: the same physical
    # thing *maybe*; never merged automatically, only suggested (spec 6.1).
    same_as: str | None = None


class Inventory:
    def __init__(self, boot_id: str) -> None:
        self.boot_id = boot_id
        self._by_uid: dict[str, EntityRecord] = {}
        self._by_registry: dict[str, str] = {}
        self._by_alias: dict[str, str] = {}  # active entities only
        self._devices: dict[str, DeviceRecord] = {}  # ha_device_id -> record

    def __len__(self) -> int:
        return sum(1 for e in self._by_uid.values() if e.deleted_at_us is None)

    def load(self, entities: list[EntityRecord], devices: list[DeviceRecord],
             now_us: int | None = None) -> list[InventoryChange]:
        """Load persisted records; returns tombstones to persist.

        Registry-less entities from a previous boot are never merged automatically
        (spec 6.1): their identity ended with that boot, so they are tombstoned here
        instead of staying "active" next to the new identity of the same entity_id.
        """
        changes: list[InventoryChange] = []
        for dev in devices:
            self._devices[dev.ha_device_id] = dev
        for ent in entities:
            self._by_uid[ent.uid] = ent
            if ent.registry_id is not None:
                self._by_registry[ent.registry_id] = ent.uid
            if ent.deleted_at_us is not None:
                continue
            if ent.registry_id is not None:
                self._by_alias[ent.current_alias] = ent.uid
            elif now_us is not None:
                ent.deleted_at_us = now_us
                changes.append(InventoryChange(entity=ent))
        return changes

    def get(self, uid: str) -> EntityRecord | None:
        return self._by_uid.get(uid)

    def uid_for_registry(self, registry_id: str) -> str | None:
        """Stable lookup that survives a rename not yet seen in a state event."""
        return self._by_registry.get(registry_id)

    def uid_for_alias(self, entity_id: str) -> str | None:
        return self._by_alias.get(entity_id)

    def devices(self) -> list[DeviceRecord]:
        return list(self._devices.values())

    def all_entities(self) -> list[EntityRecord]:
        """Including tombstones; used for alias history (spec 22.1)."""
        return list(self._by_uid.values())

    def active_entities(self) -> list[EntityRecord]:
        return [e for e in self._by_uid.values() if e.deleted_at_us is None]

    def _device_uid(self, ha_device_id: str | None, changes: list[InventoryChange]) -> str | None:
        if ha_device_id is None:
            return None
        dev = self._devices.get(ha_device_id)
        if dev is None:
            dev = DeviceRecord(uid=new_uuid(), ha_device_id=ha_device_id)
            self._devices[ha_device_id] = dev
            changes.append(InventoryChange(device=dev))
        return dev.uid

    def observe(
        self,
        *,
        entity_id: str,
        registry_id: str | None,
        device_id: str | None,
        now_us: int,
    ) -> tuple[str, list[InventoryChange]]:
        """Resolve an entity to its UID, recording an alias change if any."""
        changes: list[InventoryChange] = []
        domain = entity_id.split(".", 1)[0]
        uid = self._by_registry.get(registry_id) if registry_id else None
        if uid is None and registry_id is None:
            uid = self._by_alias.get(entity_id)
            if uid is not None and self._by_uid[uid].registry_id is not None:
                uid = None  # alias belongs to a registry entity, but this one has no entry

        device_uid = self._device_uid(device_id, changes)

        if uid is None:
            ent = EntityRecord(
                uid=new_uuid(),
                domain=domain,
                current_alias=entity_id,
                first_seen_us=now_us,
                last_seen_us=now_us,
                registry_id=registry_id,
                device_uid=device_uid,
            )
            alias = AliasRecord(
                alias=entity_id,
                valid_from_us=now_us,
                boot_id=None if registry_id else self.boot_id,
            )
            ent.aliases.append(alias)
            self._by_uid[ent.uid] = ent
            if registry_id:
                self._by_registry[registry_id] = ent.uid
            self._by_alias[entity_id] = ent.uid
            same_as = self._tombstone_with_alias(entity_id) if registry_id else None
            changes.append(InventoryChange(entity=ent, new_alias=alias, same_as=same_as))
            return ent.uid, changes

        ent = self._by_uid[uid]
        ent.last_seen_us = now_us
        dirty = False
        if ent.deleted_at_us is not None:
            # The same registry entry reappeared.
            ent.deleted_at_us = None
            dirty = True
        if device_uid is not None and ent.device_uid != device_uid:
            ent.device_uid = device_uid
            dirty = True
        if ent.current_alias != entity_id:
            change = InventoryChange(entity=ent)
            for rec in ent.aliases:
                if rec.valid_to_us is None:
                    rec.valid_to_us = now_us
                    change.closed_alias = rec
            if self._by_alias.get(ent.current_alias) == uid:
                del self._by_alias[ent.current_alias]
            new_alias = AliasRecord(alias=entity_id, valid_from_us=now_us)
            ent.aliases.append(new_alias)
            ent.current_alias = entity_id
            self._by_alias[entity_id] = uid
            change.new_alias = new_alias
            changes.append(change)
        elif dirty:
            changes.append(InventoryChange(entity=ent))
        return uid, changes

    def _tombstone_with_alias(self, entity_id: str) -> str | None:
        """Most recently deleted registry entity that last had this entity_id."""
        best: EntityRecord | None = None
        for ent in self._by_uid.values():
            if (ent.deleted_at_us is not None and ent.registry_id is not None
                    and ent.current_alias == entity_id
                    and (best is None or ent.deleted_at_us > (best.deleted_at_us or 0))):
                best = ent
        return best.uid if best else None

    def remove(self, uid: str, now_us: int) -> InventoryChange | None:
        """Tombstone: alias and edge history is kept (AC MAP 02)."""
        ent = self._by_uid.get(uid)
        if ent is None or ent.deleted_at_us is not None:
            return None
        ent.deleted_at_us = now_us
        if self._by_alias.get(ent.current_alias) == uid:
            del self._by_alias[ent.current_alias]
        return InventoryChange(entity=ent)
