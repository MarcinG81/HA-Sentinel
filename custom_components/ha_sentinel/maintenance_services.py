"""Planned maintenance windows (spec 8.2).

A window covers an entity, a device or a whole integration entry, has start, end, reason
and actor, and expires on its own. During a window Doctor raises no new failure for the
covered subjects and the health score excludes them (counted separately). Muting
notifications is different: a muted failure stays in the score.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.models import new_uuid
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

S = 1_000_000
KIND_BY_PREFIX = {"device:": "device", "config_entry:": "integration"}
MAX_MINUTES = 7 * 24 * 60


def scope_kind(uid: str) -> str:
    for prefix, kind in KIND_BY_PREFIX.items():
        if uid.startswith(prefix):
            return kind
    return "entity"


class MaintenanceService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass: HomeAssistant = runtime.hass
        self.windows: list[dict[str, Any]] = []
        # entity uid -> (device scope uid, integration scope uid), refreshed on demand.
        self._parents: dict[str, tuple[str | None, str | None]] = {}

    async def async_load(self) -> None:
        now = self.rt.clock.now_us()
        self.windows = await self.rt.db.run(lambda c: [dict(r) for r in c.execute(
            "SELECT * FROM maintenance_windows WHERE cancelled_us IS NULL AND end_us > ? "
            "ORDER BY start_us", (now,))])
        self.refresh_parents()

    @callback
    def refresh_parents(self) -> None:
        reg = er.async_get(self.hass)
        devices = {d.uid: d.ha_device_id for d in self.rt.inventory.devices()}
        parents: dict[str, tuple[str | None, str | None]] = {}
        for ent in self.rt.inventory.active_entities():
            entry = reg.async_get(ent.current_alias)
            dev = devices.get(ent.device_uid) if ent.device_uid else None
            parents[ent.uid] = (f"device:{dev}" if dev else None,
                                f"config_entry:{entry.config_entry_id}"
                                if entry and entry.config_entry_id else None)
        self._parents = parents

    def active_at(self, now_us: int) -> list[dict[str, Any]]:
        return [w for w in self.windows if w["start_us"] <= now_us < w["end_us"]]

    def covers(self, uid: str, now_us: int | None = None) -> bool:
        now = self.rt.clock.now_us() if now_us is None else now_us
        active = self.active_at(now)
        if not active:
            return False
        device, integration = self._parents.get(uid, (None, None))
        scopes = {uid, device, integration} - {None}
        return any(w["scope_id"] in scopes for w in active)

    def window_api(self, w: dict[str, Any]) -> dict[str, Any]:
        now = self.rt.clock.now_us()
        return {
            "id": w["id"], "scope_kind": w["scope_kind"], "scope_id": w["scope_id"],
            "label": w["label"], "reason": w["reason"],
            "start": us_to_rfc3339(w["start_us"]), "end": us_to_rfc3339(w["end_us"]),
            "active": w["start_us"] <= now < w["end_us"],
        }

    async def create(self, *, scope_uid: str, label: str, minutes: int, reason: str,
                     actor: str) -> dict[str, Any]:
        if not 1 <= minutes <= MAX_MINUTES:
            raise SentinelError(ErrorCode.INVALID_REQUEST, "duration out of range")
        graph = self.rt.config.graph if self.rt.config is not None else None
        node = graph.nodes.get(scope_uid) if graph is not None else None
        if node is None or node.type not in ("entity", "device", "integration_entry"):
            raise SentinelError(ErrorCode.NOT_FOUND, "unknown maintenance scope")
        now = self.rt.clock.now_us()
        wid = new_uuid()
        row = {"id": wid, "scope_kind": scope_kind(scope_uid), "scope_id": scope_uid,
               "label": label or node.label, "start_us": now, "end_us": now + minutes * 60 * S,
               "reason": reason, "actor_id": actor, "created_us": now, "cancelled_us": None}

        def insert(conn):
            with _tx(conn):
                conn.execute(
                    "INSERT INTO maintenance_windows(id, scope_kind, scope_id, label, start_us, "
                    "end_us, reason, actor_id, created_us) VALUES (?,?,?,?,?,?,?,?,?)",
                    (wid, row["scope_kind"], scope_uid, row["label"], row["start_us"],
                     row["end_us"], reason, actor, now))
                insert_audit(conn, actor, wid, "maintenance.create", scope_uid, "ok", now)

        await self.rt.db.run(insert)
        await self.async_load()  # also refreshes entity -> device/integration parents
        self.rt.bump()
        return self.window_api(row)

    async def cancel(self, window_id: str, actor: str) -> None:
        now = self.rt.clock.now_us()

        def update(conn):
            with _tx(conn):
                n = conn.execute("UPDATE maintenance_windows SET cancelled_us=? WHERE id=? "
                                 "AND cancelled_us IS NULL", (now, window_id)).rowcount
                if n == 0:
                    raise SentinelError(ErrorCode.NOT_FOUND, "window not found")
                insert_audit(conn, actor, window_id, "maintenance.cancel", None, "ok", now)

        await self.rt.db.run(update)
        await self.async_load()
        self.rt.bump()
