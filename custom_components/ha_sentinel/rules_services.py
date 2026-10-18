"""Per-object rule overrides and heartbeat contracts (spec 8.1, 27.1, 27.2).

Overrides live in the Sentinel DB (one source of truth, never also in options). Every
write is audited and gets a new policy revision. An override stays attached to its
subject UID after the subject is deleted (tombstone) and never moves to a new entity
with the same entity_id.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import device_registry as dr

from .core.errors import ErrorCode, SentinelError
from .core.models import new_uuid
from .modules.doctor.overrides import RuleOverride, validate
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

MAX_OVERRIDES = 2000
MAX_BULK = 200
DEVICE_PREFIX = "device:"
PER_ENTITY_RULES = frozenset({"DOC005", "DOC007"})


def _row_to_override(row: Any) -> RuleOverride:
    return RuleOverride(id=row["id"], rule_id=row["rule_id"], subject_uid=row["subject_uid"],
                        enabled=bool(row["enabled"]), params=json.loads(row["params_json"]),
                        policy_revision=row["policy_revision"])


class RuleService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.overrides: list[RuleOverride] = []
        self._updated: dict[str, int] = {}

    async def async_load(self) -> None:
        def load(conn):
            return [dict(r) for r in conn.execute(
                "SELECT * FROM rule_overrides ORDER BY rule_id, subject_uid")]

        rows = await self.rt.db.run(load)
        self.overrides = [_row_to_override(r) for r in rows]
        self._updated = {r["id"]: r["updated_us"] for r in rows}

    def override_api(self, o: RuleOverride) -> dict[str, Any]:
        if o.subject_uid.startswith(DEVICE_PREFIX):
            dev = dr.async_get(self.rt.hass).async_get(o.subject_uid.removeprefix(DEVICE_PREFIX))
            return {
                "id": o.id, "rule_id": o.rule_id, "subject_uid": o.subject_uid,
                "subject_kind": "device",
                "subject_label": (dev.name_by_user or dev.name) if dev else None,
                "subject_deleted": dev is None, "subject_persistent": True,
                "enabled": o.enabled, "params": o.params, "policy_revision": o.policy_revision,
            }
        ent = self.rt.inventory.get(o.subject_uid)
        return {
            "id": o.id, "rule_id": o.rule_id, "subject_uid": o.subject_uid,
            "subject_kind": "entity",
            "subject_label": ent.current_alias if ent else None,
            "subject_deleted": ent is None or ent.deleted_at_us is not None,
            # Without a registry entry the identity lasts one boot (spec 6.1).
            "subject_persistent": ent is not None and ent.registry_id is not None,
            "enabled": o.enabled, "params": o.params, "policy_revision": o.policy_revision,
        }

    def _check_subject(self, rule_id: str, subject_uid: str) -> None:
        if subject_uid.startswith(DEVICE_PREFIX):
            # A device-wide setting covers its entities, also ones added later. Contracts
            # (heartbeat, expected runs) stay per entity: they describe one sensor or one
            # automation, not a whole device.
            if rule_id in PER_ENTITY_RULES:
                raise SentinelError(ErrorCode.VALIDATION_FAILED, "rule is set per entity",
                                    param="subject_uid")
            if dr.async_get(self.rt.hass).async_get(
                    subject_uid.removeprefix(DEVICE_PREFIX)) is None:
                raise SentinelError(ErrorCode.NOT_FOUND, "unknown device")
            return
        ent = self.rt.inventory.get(subject_uid)
        if ent is None or ent.deleted_at_us is not None:
            raise SentinelError(ErrorCode.NOT_FOUND, "unknown subject")
        if rule_id == "DOC007" and ent.domain != "automation":
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "expected runs need an automation",
                                param="subject_uid")

    async def set(self, *, rule_id: str, subject_uid: str, enabled: bool,
                  params: dict[str, Any], actor: str) -> dict[str, Any]:
        ids = await self.set_many(rule_id=rule_id, subject_uids=[subject_uid],
                                  enabled=enabled, params=params, actor=actor)
        return self.override_api(next(o for o in self.overrides if o.id == ids[0]))

    async def set_many(self, *, rule_id: str, subject_uids: list[str], enabled: bool,
                       params: dict[str, Any], actor: str) -> list[str]:
        """One validated, audited transaction for one or many subjects (e.g. an incident)."""
        clean = validate(rule_id, enabled, params)
        subjects = list(dict.fromkeys(subject_uids))
        if not 1 <= len(subjects) <= MAX_BULK:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "one to 200 subjects",
                                param="subject_uids")
        for uid in subjects:
            self._check_subject(rule_id, uid)
        now = self.rt.clock.now_us()

        def upsert(conn):
            with _tx(conn):
                count = conn.execute("SELECT COUNT(*) FROM rule_overrides").fetchone()[0]
                revision = conn.execute("SELECT COALESCE(MAX(policy_revision), 0) + 1 "
                                        "FROM rule_overrides").fetchone()[0]
                ids = []
                for uid in subjects:
                    row = conn.execute("SELECT id FROM rule_overrides WHERE rule_id=? AND "
                                       "subject_uid=?", (rule_id, uid)).fetchone()
                    if row is None:
                        count += 1
                        if count > MAX_OVERRIDES:
                            raise SentinelError(ErrorCode.CONFLICT, "too many overrides",
                                                limit=MAX_OVERRIDES)
                    oid = row["id"] if row is not None else new_uuid()
                    conn.execute(
                        "INSERT INTO rule_overrides(id, rule_id, subject_uid, enabled, "
                        "params_json, policy_revision, actor_id, created_us, updated_us) "
                        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(rule_id, subject_uid) DO UPDATE "
                        "SET enabled=excluded.enabled, params_json=excluded.params_json, "
                        "policy_revision=excluded.policy_revision, actor_id=excluded.actor_id, "
                        "updated_us=excluded.updated_us",
                        (oid, rule_id, uid, int(enabled), json.dumps(clean, sort_keys=True),
                         revision, actor, now, now))
                    insert_audit(conn, actor, oid, "rules.set", uid, "ok", now)
                    ids.append(oid)
                return ids

        ids = await self.rt.db.run(upsert)
        await self._apply()
        await self.rt.config_changed(actor)
        return ids

    async def delete(self, override_id: str, actor: str) -> None:
        now = self.rt.clock.now_us()

        def remove(conn):
            with _tx(conn):
                row = conn.execute("SELECT subject_uid FROM rule_overrides WHERE id=?",
                                   (override_id,)).fetchone()
                if row is None:
                    raise SentinelError(ErrorCode.NOT_FOUND, "override not found")
                conn.execute("DELETE FROM rule_overrides WHERE id=?", (override_id,))
                insert_audit(conn, actor, override_id, "rules.delete", row["subject_uid"],
                             "ok", now)

        await self.rt.db.run(remove)
        await self._apply()
        await self.rt.config_changed(actor)

    async def _apply(self) -> None:
        await self.async_load()
        closed = self.rt.doctor.set_overrides(self.overrides, self.rt.clock.now_us())
        self.rt.refresh_heartbeat_tracking()
        self.rt.reschedule_doctor()  # new deadlines (heartbeat, expected runs) need a timer
        await self.rt.commit_findings(closed)
        self.rt.bump()
