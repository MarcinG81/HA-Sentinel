"""Identity suggestions (spec 6.1) and registry reconciliation (spec 5.1).

Deleting and re-adding an object does not prove it is the same thing. When a new
registry entity reuses the entity_id of a tombstone, Sentinel only suggests continuity.
Confirming moves the per-object settings (rule overrides, numeric sources, the health
weight of an entity unit) from the old identity to the new one; history stays where it
was recorded. Rejecting keeps both apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

MOVED_TABLES = (("rule_overrides", "subject_uid"), ("numeric_sources", "subject_uid"),
                ("unit_weights", "unit_uid"))


class IdentityService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime

    def _label(self, uid: str) -> dict[str, Any]:
        ent = self.rt.inventory.get(uid)
        return {"uid": uid, "entity_id": ent.current_alias if ent else None,
                "deleted": ent is None or ent.deleted_at_us is not None}

    async def pending(self) -> list[dict[str, Any]]:
        rows = await self.rt.db.run(lambda c: [dict(r) for r in c.execute(
            "SELECT id, old_uid, new_uid, reason, created_us FROM identity_suggestions "
            "WHERE decision IS NULL ORDER BY created_us DESC LIMIT 100")])
        return [{"id": r["id"], "old": self._label(r["old_uid"]), "new": self._label(r["new_uid"]),
                 "reason": r["reason"], "created": us_to_rfc3339(r["created_us"])}
                for r in rows]

    async def decide(self, suggestion_id: str, confirm: bool, actor: str) -> dict[str, int]:
        now = self.rt.clock.now_us()

        def write(conn):
            with _tx(conn):
                row = conn.execute("SELECT old_uid, new_uid FROM identity_suggestions WHERE "
                                   "id=? AND decision IS NULL", (suggestion_id,)).fetchone()
                if row is None:
                    raise SentinelError(ErrorCode.NOT_FOUND, "suggestion not found")
                moved: dict[str, int] = {}
                if confirm:
                    for table, column in MOVED_TABLES:
                        # The new identity keeps its own settings if it already has some.
                        moved[table] = conn.execute(
                            f"UPDATE OR IGNORE {table} SET {column}=? WHERE {column}=?",  # noqa: S608
                            (row["new_uid"], row["old_uid"])).rowcount
                conn.execute("UPDATE identity_suggestions SET decision=?, decided_by=?, "
                             "decided_us=? WHERE id=?",
                             ("confirmed" if confirm else "rejected", actor, now, suggestion_id))
                insert_audit(conn, actor, suggestion_id,
                             "identity.confirm" if confirm else "identity.reject",
                             row["new_uid"], "ok", now)
                return moved

        moved = await self.rt.db.run(write)
        if confirm and any(moved.values()):
            await self.rt.reload_object_settings()
            await self.rt.config_changed(actor)
        self.rt.bump()
        return moved
