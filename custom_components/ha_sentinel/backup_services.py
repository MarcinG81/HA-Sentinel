"""Home Assistant backup status and an explicit "create backup" request (spec 10.5).

Full disaster recovery belongs to HA Backup. Sentinel only shows the state of the
existing backup entities with a link to the settings, and can ask HA to start a backup
on an explicit admin action. An accepted request does not mean the backup finished:
the result is read back from the backup entities.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .core.errors import ErrorCode, SentinelError
from .core.models import new_uuid
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

BACKUP_DOMAIN = "backup"
# Prefer the automatic backup: it uses the locations and encryption the admin set up.
SERVICES = ("create_automatic", "create")
SETTINGS_PATH = "/config/backup"
ACCEPT_TIMEOUT_S = 30
_KEYS = {  # translation_key of the backup sensors -> field
    "backup_manager_state": "manager_state",
    "last_successful_automatic_backup": "last_successful",
    "last_attempted_automatic_backup": "last_attempted",
    "next_scheduled_automatic_backup": "next_scheduled",
}


class BackupService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass: HomeAssistant = runtime.hass

    def _service(self) -> str | None:
        return next((s for s in SERVICES if self.hass.services.has_service(BACKUP_DOMAIN, s)),
                    None)

    def status(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for entry in er.async_get(self.hass).entities.values():
            if entry.platform != BACKUP_DOMAIN or entry.translation_key not in _KEYS:
                continue
            state = self.hass.states.get(entry.entity_id)
            values[_KEYS[entry.translation_key]] = {
                "entity_id": entry.entity_id,
                "state": state.state if state else None,
            }
        return {
            "available": BACKUP_DOMAIN in self.hass.config.components,
            "service": self._service(),
            "settings_path": SETTINGS_PATH,
            "entities": values,
        }

    async def create(self, actor: str) -> dict[str, Any]:
        service = self._service()
        if service is None:
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "backup action not available",
                                reason="backup_unavailable")
        now = self.rt.clock.now_us()
        request_id = new_uuid()
        # Audit before the side effect (spec 5.2); HA re-checks admin rights for the user.
        await self.rt.db.run(lambda c: _audit(c, actor, request_id, service, "requested", now))
        try:
            # The backup action returns once the backup has started (it then runs in the
            # background), so waiting here surfaces a refusal such as "no location set".
            await asyncio.wait_for(self.hass.services.async_call(
                BACKUP_DOMAIN, service, {}, blocking=True, context=Context(user_id=actor)),
                ACCEPT_TIMEOUT_S)
        except TimeoutError:
            pass  # still starting; the backup entities will show the outcome
        except HomeAssistantError as err:
            await self.rt.db.run(lambda c: _audit(c, actor, request_id, service, "rejected",
                                                  self.rt.clock.now_us()))
            raise SentinelError(ErrorCode.CONFLICT, "backup request rejected",
                                reason="backup_rejected") from err
        await self.rt.db.run(lambda c: _audit(c, actor, request_id, service, "accepted",
                                              self.rt.clock.now_us()))
        return {"accepted": True, "service": f"{BACKUP_DOMAIN}.{service}",
                "settings_path": SETTINGS_PATH}


def _audit(conn: Any, actor: str, request_id: str, service: str, result: str,
           now_us: int) -> None:
    with _tx(conn):
        insert_audit(conn, actor, request_id, "backup.request", f"{BACKUP_DOMAIN}.{service}",
                     result, now_us)
