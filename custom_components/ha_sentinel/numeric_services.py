"""Explicit numeric sources: manual battery mapping and signal scales (spec 5.1, 8.1, 27.1).

A battery mapping is part of the baseline observation (DOC004 must not depend on entity
names). A signal source is extra telemetry and needs `ingestion.numeric_sampling`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .core.errors import ErrorCode, SentinelError
from .core.models import new_uuid
from .modules.doctor.numeric import NumericSource, validate
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

MAX_SOURCES = 1000
RULE_OF_PURPOSE = {"battery": "DOC004", "signal": "DOC008"}


def _row_to_source(row: Any) -> NumericSource:
    return NumericSource(id=row["id"], subject_uid=row["subject_uid"], purpose=row["purpose"],
                         scale_min=row["scale_min"], scale_max=row["scale_max"],
                         threshold=row["threshold"])


class NumericSourceService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.sources: list[NumericSource] = []

    async def async_load(self) -> None:
        rows = await self.rt.db.run(lambda c: [dict(r) for r in c.execute(
            "SELECT * FROM numeric_sources ORDER BY purpose, subject_uid")])
        self.sources = [_row_to_source(r) for r in rows]

    def active(self, sampling_enabled: bool) -> list[NumericSource]:
        """Sources that produce samples now; signal sources need the sampling option."""
        return [s for s in self.sources if s.purpose == "battery" or sampling_enabled]

    def source_api(self, s: NumericSource, sampling_enabled: bool) -> dict[str, Any]:
        ent = self.rt.inventory.get(s.subject_uid)
        return {
            "id": s.id, "subject_uid": s.subject_uid,
            "subject_label": ent.current_alias if ent else None,
            "subject_deleted": ent is None or ent.deleted_at_us is not None,
            # Without a registry entry the identity lasts one boot (spec 6.1).
            "subject_persistent": ent is not None and ent.registry_id is not None,
            "purpose": s.purpose, "scale_min": s.scale_min, "scale_max": s.scale_max,
            "threshold": s.threshold,
            "sampling": s.purpose == "battery" or sampling_enabled,
        }

    async def set(self, *, subject_uid: str, purpose: str, scale_min: float,
                  scale_max: float, threshold: float | None, actor: str) -> NumericSource:
        validate(purpose, scale_min, scale_max, threshold)
        if purpose == "signal" and not self.rt.numeric_sampling:
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "numeric sampling is off",
                                reason="numeric_sampling_disabled")
        ent = self.rt.inventory.get(subject_uid)
        if ent is None or ent.deleted_at_us is not None:
            raise SentinelError(ErrorCode.NOT_FOUND, "unknown subject")
        now = self.rt.clock.now_us()

        def upsert(conn):
            with _tx(conn):
                row = conn.execute("SELECT id FROM numeric_sources WHERE subject_uid=? AND "
                                   "purpose=?", (subject_uid, purpose)).fetchone()
                count = conn.execute("SELECT COUNT(*) FROM numeric_sources").fetchone()[0]
                if row is None and count >= MAX_SOURCES:
                    raise SentinelError(ErrorCode.CONFLICT, "too many numeric sources",
                                        limit=MAX_SOURCES)
                sid = row["id"] if row is not None else new_uuid()
                conn.execute(
                    "INSERT INTO numeric_sources(id, subject_uid, purpose, scale_min, scale_max, "
                    "threshold, actor_id, created_us, updated_us) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(subject_uid, purpose) DO UPDATE SET "
                    "scale_min=excluded.scale_min, scale_max=excluded.scale_max, "
                    "threshold=excluded.threshold, actor_id=excluded.actor_id, "
                    "updated_us=excluded.updated_us",
                    (sid, subject_uid, purpose, scale_min, scale_max, threshold, actor, now, now))
                insert_audit(conn, actor, sid, "numeric.set", subject_uid, "ok", now)
                return sid

        sid = await self.rt.db.run(upsert)
        await self._apply([])
        await self.rt.config_changed(actor)
        return next(s for s in self.sources if s.id == sid)

    async def delete(self, source_id: str, actor: str) -> None:
        now = self.rt.clock.now_us()

        def remove(conn):
            with _tx(conn):
                row = conn.execute("SELECT subject_uid, purpose FROM numeric_sources WHERE id=?",
                                   (source_id,)).fetchone()
                if row is None:
                    raise SentinelError(ErrorCode.NOT_FOUND, "numeric source not found")
                conn.execute("DELETE FROM numeric_sources WHERE id=?", (source_id,))
                insert_audit(conn, actor, source_id, "numeric.delete", row["subject_uid"],
                             "ok", now)
                return dict(row)

        row = await self.rt.db.run(remove)
        # The finding was derived from this mapping only; close it explicitly.
        closed = self.rt.doctor.close(RULE_OF_PURPOSE[row["purpose"]], row["subject_uid"],
                                      now, "source_removed")
        await self._apply(closed)
        await self.rt.config_changed(actor)

    async def _apply(self, closed: list) -> None:
        await self.async_load()
        self.rt.refresh_numeric_tracking()
        await self.rt.commit_findings(closed)
        self.rt.bump()
