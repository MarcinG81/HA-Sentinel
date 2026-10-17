"""Notification outbox delivery (spec 14.3). Opt-in, local only.

Only Home Assistant persistent notifications are used: no external notify service gets
details of the home. The notification id is stable per incident, so a retried or uncertain
delivery replaces instead of duplicating. Repeats within 30 minutes are skipped unless the
severity escalated to critical. Resolved incidents dismiss their notification.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .const import DEFAULT_OPTIONS, OPT_NOTIFICATIONS_ENABLED
from .repositories.repository import _tx

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

_LOGGER = logging.getLogger(__name__)
S = 1_000_000
BACKOFF_S = (5, 30, 120)
LATE_RETRY_S = 600
GIVE_UP_S = 24 * 3600
COOLDOWN_S = 30 * 60
BATCH = 20

_TEXT = {
    "en": {
        "title": "HA Sentinel: {title}",
        "body": "Severity: {severity}. Affected: {subject}.\n\n"
                "Details, hypotheses and counter-evidence: "
                "[Sentinel → Doctor](/ha-sentinel/doctor)",
        "infra": "Common failure: {root}",
        "objects": "{n} objects",
        "sev": {"critical": "critical", "high": "high", "warning": "warning", "info": "info"},
        "rules": {"DOC001": "Entity unavailable", "DOC002": "Entity state unknown",
                  "DOC003": "Entity is flapping", "DOC004": "Low battery",
                  "DOC005": "Missing heartbeat", "DOC006": "Missing reference",
                  "DOC008": "Weak signal",
                  "DOC009": "Update available",
                  "DOC010": "Sentinel storage pressure", "NET001": "Endpoint unreachable"},
    },
    "pl": {
        "title": "HA Sentinel: {title}",
        "body": "Ważność: {severity}. Dotyczy: {subject}.\n\n"
                "Szczegóły, hipotezy i przeciwdowody: [Sentinel → Doctor](/ha-sentinel/doctor)",
        "infra": "Wspólna awaria: {root}",
        "objects": "obiektów: {n}",
        "sev": {"critical": "krytyczna", "high": "wysoka", "warning": "ostrzeżenie",
                "info": "informacja"},
        "rules": {"DOC001": "Encja niedostępna", "DOC002": "Nieznany stan encji",
                  "DOC003": "Encja migocze", "DOC004": "Niski poziom baterii",
                  "DOC005": "Brak sygnału heartbeat", "DOC006": "Brakujące odwołanie",
                  "DOC008": "Słaby sygnał",
                  "DOC009": "Dostępna aktualizacja",
                  "DOC010": "Presja magazynu Sentinel", "NET001": "Endpoint nieosiągalny"},
    },
}


def notification_id(incident_id: str) -> str:
    return f"ha_sentinel_{incident_id}"


class NotificationService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass: HomeAssistant = runtime.hass
        opts = {**DEFAULT_OPTIONS, **runtime.entry.options}
        self.enabled = bool(opts[OPT_NOTIFICATIONS_ENABLED])
        self.delivered = 0

    def _render(self, inc: dict[str, Any]) -> tuple[str, str]:
        lang = "pl" if (self.hass.config.language or "").lower().startswith("pl") else "en"
        tx = _TEXT[lang]
        summary = json.loads(inc["summary_json"])
        if summary.get("root_uid"):
            title = tx["infra"].format(root=self.rt.node_label(summary["root_uid"])["label"])
            subject = tx["objects"].format(n=summary.get("member_count", 0))
        else:
            rule = summary.get("title_key", "").removeprefix("rule.")
            title = tx["rules"].get(rule, rule)
            label = self.rt.subject_label(summary.get("subject_uid"))
            subject = label.get("name") or label.get("entity_id") or summary.get("subject_uid")
        return (tx["title"].format(title=title),
                tx["body"].format(severity=tx["sev"].get(inc["severity"], inc["severity"]),
                                  subject=subject))

    async def process(self) -> int:
        if not self.rt.storage_writable:
            return 0
        now = self.rt.clock.now_us()
        rows = await self.rt.db.run(lambda c: [dict(r) for r in c.execute(
            "SELECT o.id, o.incident_id, o.reason, o.attempt, o.created_us, i.status, "
            "i.severity, i.fingerprint, i.summary_json FROM notification_outbox o "
            "JOIN incidents i ON i.id = o.incident_id "
            "WHERE o.state IN ('pending','uncertain') AND o.next_try_us <= ? "
            "ORDER BY o.next_try_us LIMIT ?", (now, BATCH))])
        sent = 0
        for row in rows:
            if not self.enabled or row["status"] == "resolved" or row["status"] == "suppressed":
                await self._mark(row["id"], "skipped", now)
                continue
            if row["reason"] != "escalated" and await self._in_cooldown(row, now):
                await self._mark(row["id"], "skipped", now)
                continue
            try:
                title, message = self._render(row)
                persistent_notification.async_create(
                    self.hass, message, title, notification_id(row["incident_id"]))
            except Exception:  # noqa: BLE001 - retry with backoff, never break the loop
                _LOGGER.warning("Sentinel notification failed; will retry", exc_info=True)
                await self._retry(row, now)
                continue
            await self._mark(row["id"], "sent", now)
            sent += 1
        self.delivered += sent
        await self._dismiss_resolved(now)
        return sent

    async def _in_cooldown(self, row: dict[str, Any], now: int) -> bool:
        """Repeated information about the same object within 30 min is not re-sent."""
        recent = await self.rt.db.run(lambda c: c.execute(
            "SELECT COUNT(*) FROM notification_outbox o JOIN incidents i "
            "ON i.id = o.incident_id WHERE i.fingerprint=? AND o.state IN ('sent','dismissed') "
            "AND o.sent_us >= ?", (row["fingerprint"], now - COOLDOWN_S * S)).fetchone()[0])
        return recent > 0

    async def _mark(self, outbox_id: str, state: str, now: int) -> None:
        def update(conn):
            with _tx(conn):
                conn.execute("UPDATE notification_outbox SET state=?, sent_us=CASE WHEN ?='sent' "
                             "THEN ? ELSE sent_us END WHERE id=?",
                             (state, state, now, outbox_id))
        await self.rt.db.run(update)

    async def _retry(self, row: dict[str, Any], now: int) -> None:
        attempt = row["attempt"] + 1
        if now - row["created_us"] > GIVE_UP_S * S:
            await self._mark(row["id"], "failed", now)
            return
        delay = BACKOFF_S[attempt - 1] if attempt <= len(BACKOFF_S) else LATE_RETRY_S

        def update(conn):
            with _tx(conn):
                conn.execute("UPDATE notification_outbox SET attempt=?, next_try_us=? WHERE id=?",
                             (attempt, now + delay * S, row["id"]))
        await self.rt.db.run(update)

    async def _dismiss_resolved(self, now: int) -> None:
        rows = await self.rt.db.run(lambda c: [r[0] for r in c.execute(
            "SELECT DISTINCT o.incident_id FROM notification_outbox o JOIN incidents i "
            "ON i.id = o.incident_id WHERE o.state='sent' AND i.status='resolved'")])
        for incident_id in rows:
            persistent_notification.async_dismiss(self.hass, notification_id(incident_id))

            def update(conn, iid=incident_id):
                with _tx(conn):
                    conn.execute("UPDATE notification_outbox SET state='dismissed' "
                                 "WHERE incident_id=? AND state='sent'", (iid,))
            await self.rt.db.run(update)
