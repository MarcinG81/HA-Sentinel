"""Queries against sentinel.db. Synchronous functions - call them via DatabaseWorker.run()."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from ..core.inventory import AliasRecord, DeviceRecord, EntityRecord, InventoryChange
from ..core.models import (
    CoverageSegment,
    Finding,
    FindingStatus,
    IncidentStatus,
    Observation,
    Severity,
    dumps_strict,
    new_uuid,
)


def insert_observations(conn: sqlite3.Connection, batch: Iterable[Observation]) -> int:
    """Insert a batch in one transaction; transport duplicates are ignored (spec 5.3)."""
    rows = [
        (
            o.id, o.ingest_seq, o.boot_id, o.source_id, o.source_event_id, str(o.kind),
            o.subject_uid, o.received_at_us, o.occurred_at_us, o.payload_json(),
            dumps_strict(o.quality.as_dict()), o.schema_version,
        )
        for o in batch
    ]
    if not rows:
        return 0
    with _tx(conn):
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO observations(id, ingest_seq, boot_id, source_id, "
            "source_event_id, kind, subject_uid, received_at_us, occurred_at_us, payload_json, "
            "quality_json, schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        return conn.total_changes - before


def insert_coverage(conn: sqlite3.Connection, segments: Iterable[CoverageSegment]) -> None:
    with _tx(conn):
        conn.executemany(
            "INSERT INTO coverage(source_id, scope, from_us, to_us, status, reason) "
            "VALUES (?,?,?,?,?,?)",
            [(s.source_id, s.scope, s.from_us, s.to_us, str(s.status), s.reason)
             for s in segments],
        )


def load_coverage_since(conn: sqlite3.Connection, since_us: int) -> list[CoverageSegment]:
    from ..core.models import CoverageStatus

    rows = conn.execute(
        "SELECT source_id, scope, from_us, to_us, status, reason FROM coverage "
        "WHERE to_us IS NULL OR to_us >= ? ORDER BY from_us",
        (since_us,),
    ).fetchall()
    return [
        CoverageSegment(r["source_id"], r["scope"], r["from_us"], r["to_us"],
                        CoverageStatus(r["status"]), r["reason"])
        for r in rows
    ]


def save_checkpoint(conn: sqlite3.Connection, consumer: str, boot_id: str, seq: int) -> None:
    with _tx(conn):
        conn.execute(
            "INSERT INTO checkpoints(consumer, boot_id, ingest_seq) VALUES (?,?,?) "
            "ON CONFLICT(consumer) DO UPDATE SET boot_id=excluded.boot_id, "
            "ingest_seq=excluded.ingest_seq",
            (consumer, boot_id, seq),
        )


def load_checkpoint(conn: sqlite3.Connection, consumer: str) -> tuple[str, int] | None:
    row = conn.execute(
        "SELECT boot_id, ingest_seq FROM checkpoints WHERE consumer=?", (consumer,)
    ).fetchone()
    return (row["boot_id"], row["ingest_seq"]) if row else None


def last_observation_us(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(received_at_us) FROM observations").fetchone()
    return row[0]


# --- Inventory ---------------------------------------------------------------


def apply_inventory_changes(conn: sqlite3.Connection, changes: Iterable[InventoryChange]) -> None:
    with _tx(conn):
        for ch in changes:
            if ch.device is not None:
                d = ch.device
                conn.execute(
                    "INSERT INTO devices(uid, ha_device_id, metadata_json, deleted_at_us) "
                    "VALUES (?,?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                    "deleted_at_us=excluded.deleted_at_us",
                    (d.uid, d.ha_device_id, "{}", d.deleted_at_us),
                )
            if ch.entity is not None:
                e = ch.entity
                conn.execute(
                    "INSERT INTO entities(uid, registry_id, current_alias, domain, first_seen_us, "
                    "last_seen_us, deleted_at_us) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(uid) DO UPDATE SET current_alias=excluded.current_alias, "
                    "last_seen_us=excluded.last_seen_us, deleted_at_us=excluded.deleted_at_us",
                    (e.uid, e.registry_id, e.current_alias, e.domain, e.first_seen_us,
                     e.last_seen_us, e.deleted_at_us),
                )
                if ch.same_as is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO identity_suggestions(id, kind, old_uid, new_uid, "
                        "reason, created_us) VALUES (?, 'entity', ?, ?, 'same_entity_id', ?)",
                        (new_uuid(), ch.same_as, e.uid, e.first_seen_us))
                if e.device_uid is not None:
                    conn.execute("DELETE FROM entity_devices WHERE entity_uid=?", (e.uid,))
                    conn.execute(
                        "INSERT INTO entity_devices(entity_uid, device_uid) VALUES (?,?)",
                        (e.uid, e.device_uid),
                    )
                if ch.closed_alias is not None:
                    a = ch.closed_alias
                    conn.execute(
                        "UPDATE entity_aliases SET valid_to_us=? WHERE entity_uid=? AND alias=? "
                        "AND valid_from_us=?",
                        (a.valid_to_us, e.uid, a.alias, a.valid_from_us),
                    )
                if ch.new_alias is not None:
                    a = ch.new_alias
                    conn.execute(
                        "INSERT INTO entity_aliases(entity_uid, alias, boot_id, valid_from_us, "
                        "valid_to_us) VALUES (?,?,?,?,?)",
                        (e.uid, a.alias, a.boot_id, a.valid_from_us, a.valid_to_us),
                    )


def load_inventory(conn: sqlite3.Connection) -> tuple[list[EntityRecord], list[DeviceRecord]]:
    devices = [
        DeviceRecord(uid=r["uid"], ha_device_id=r["ha_device_id"],
                     deleted_at_us=r["deleted_at_us"])
        for r in conn.execute("SELECT uid, ha_device_id, deleted_at_us FROM devices")
    ]
    dev_by_entity = {
        r["entity_uid"]: r["device_uid"]
        for r in conn.execute("SELECT entity_uid, device_uid FROM entity_devices")
    }
    aliases: dict[str, list[AliasRecord]] = {}
    for r in conn.execute(
        "SELECT entity_uid, alias, boot_id, valid_from_us, valid_to_us FROM entity_aliases "
        "ORDER BY valid_from_us"
    ):
        aliases.setdefault(r["entity_uid"], []).append(
            AliasRecord(r["alias"], r["valid_from_us"], r["valid_to_us"], r["boot_id"])
        )
    entities = [
        EntityRecord(
            uid=r["uid"], domain=r["domain"], current_alias=r["current_alias"],
            first_seen_us=r["first_seen_us"], last_seen_us=r["last_seen_us"],
            registry_id=r["registry_id"], device_uid=dev_by_entity.get(r["uid"]),
            deleted_at_us=r["deleted_at_us"], aliases=aliases.get(r["uid"], []),
        )
        for r in conn.execute("SELECT * FROM entities")
    ]
    return entities, devices


# --- Findings and incidents ------------------------------------------------------


def upsert_finding(conn: sqlite3.Connection, f: Finding, now_us: int) -> None:
    """Persist a finding and its evidence. Incidents are derived by sync_incidents."""
    with _tx(conn):
        conn.execute(
            "INSERT INTO findings(id, fingerprint, rule_id, rule_version, subject_uid, status, "
            "severity, confidence, coverage_json, first_us, last_us, resolved_us, details_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status, severity=excluded.severity, last_us=excluded.last_us, "
            "resolved_us=excluded.resolved_us, coverage_json=excluded.coverage_json, "
            "details_json=excluded.details_json",
            (f.id, f.fingerprint, f.rule_id, f.rule_version, f.subject_uid, str(f.status),
             str(f.severity), f.confidence, dumps_strict({"status": str(f.coverage)}),
             f.first_seen_us, f.last_seen_us, f.resolved_at_us,
             dumps_strict(f.details or {})),
        )
        for obs_id in f.evidence_ids:
            conn.execute(
                "INSERT OR IGNORE INTO finding_evidence(finding_id, observation_id, digest, "
                "summary_json) VALUES (?, (SELECT id FROM observations WHERE id=?), ?, ?)",
                (f.id, obs_id, obs_id, dumps_strict({"rule_id": f.rule_id})),
            )


def load_active_incidents(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, fingerprint, opened_at_us, summary_json FROM incidents "
        "WHERE status IN ('open','acknowledged','suppressed')"
    ).fetchall()
    out = []
    for r in rows:
        summary = json.loads(r["summary_json"])
        out.append({"id": r["id"], "fingerprint": r["fingerprint"],
                    "opened_at_us": r["opened_at_us"],
                    "finding_ids": summary.get("finding_ids", []),
                    "root_uid": summary.get("root_uid")})
    return out


_KEEP_ON_UPDATE = ("confirmed_cause", "recurrence_of")


def recent_config_changes(conn: sqlite3.Connection, since_us: int) -> list[dict]:
    """Snapshots newer than `since_us` that changed an existing automation resource.

    A first import is Sentinel learning about a file, not a change in HA, so only
    snapshots with a predecessor count.
    """
    return [dict(r) for r in conn.execute(
        "SELECT s.resource_id, r.label, MAX(s.created_us) AS changed_at_us FROM snapshots s "
        "JOIN resources r ON r.id = s.resource_id WHERE s.created_us >= ? "
        "AND r.kind IN ('automation','script','blueprint') AND EXISTS (SELECT 1 FROM "
        "snapshots p WHERE p.resource_id = s.resource_id AND p.rowid < s.rowid) "
        "GROUP BY s.resource_id, r.label", (since_us,))]


def sync_incidents(conn: sqlite3.Connection, plans: list, now_us: int) -> bool:
    """Apply correlator plans in one transaction (spec 9.1). Returns True if anything changed.

    - new fingerprint -> open incident + notification outbox entry
    - changed members, severity or hypotheses -> revision + 1
    - merged incidents -> resolved with summary.merged_into (not a recovery claim)
    - active incidents without any active finding -> resolved (recovery observed)
    """
    changed = False
    with _tx(conn):
        active = {
            r["fingerprint"]: r for r in conn.execute(
                "SELECT id, fingerprint, severity, summary_json FROM incidents "
                "WHERE status IN ('open','acknowledged','suppressed')"
            ).fetchall()
        }
        planned: set[str] = set()
        merged: dict[str, str] = {}
        for plan in plans:
            planned.add(plan.fingerprint)
            summary = {
                "title_key": plan.title_key,
                "finding_ids": plan.finding_ids,
                "subject_uid": plan.subject_uid,
                "root_uid": plan.root_uid,
                "member_count": len(plan.finding_ids),
                "hypotheses": plan.hypotheses,
                "change_hypotheses": getattr(plan, "changes", []),
            }
            row = active.get(plan.fingerprint)
            if row is None:
                incident_id = new_uuid()
                # A failure after a resolved one is a new incident that remembers it (9.1).
                prev = conn.execute(
                    "SELECT id FROM incidents WHERE fingerprint=? AND status='resolved' AND "
                    "json_extract(summary_json, '$.merged_into') IS NULL "
                    "ORDER BY resolved_at_us DESC, rowid DESC LIMIT 1",
                    (plan.fingerprint,)).fetchone()
                if prev is not None:
                    summary["recurrence_of"] = prev["id"]
                conn.execute(
                    "INSERT INTO incidents(id, fingerprint, status, severity, opened_at_us, "
                    "updated_at_us, revision, summary_json) VALUES (?,?,?,?,?,?,?,?)",
                    (incident_id, plan.fingerprint, str(IncidentStatus.OPEN),
                     str(plan.severity), plan.opened_at_us, now_us, 1, dumps_strict(summary)),
                )
                _enqueue(conn, incident_id, 1, "opened", now_us)
                changed = True
            else:
                incident_id = row["id"]
                old = json.loads(row["summary_json"])
                for key in _KEEP_ON_UPDATE:
                    if key in old:
                        summary[key] = old[key]
                if old != summary or row["severity"] != str(plan.severity):
                    conn.execute(
                        "UPDATE incidents SET summary_json=?, severity=?, updated_at_us=?, "
                        "revision=revision+1 WHERE id=?",
                        (dumps_strict(summary), str(plan.severity), now_us, incident_id),
                    )
                    changed = True
                    if plan.severity is Severity.CRITICAL and row["severity"] != "critical":
                        # A new critical severity breaks the notification cooldown.
                        rev = conn.execute("SELECT revision FROM incidents WHERE id=?",
                                           (incident_id,)).fetchone()[0]
                        _enqueue(conn, incident_id, rev, "escalated", now_us)
            for merged_id in plan.merge_incident_ids:
                merged[merged_id] = incident_id
        for fp, row in active.items():
            if fp in planned:
                continue
            summary = json.loads(row["summary_json"])
            if row["id"] in merged:
                summary["merged_into"] = merged[row["id"]]
            conn.execute(
                "UPDATE incidents SET status='resolved', resolved_at_us=?, updated_at_us=?, "
                "summary_json=?, revision=revision+1 WHERE id=?",
                (now_us, now_us, dumps_strict(summary), row["id"]),
            )
            changed = True
    return changed


def _enqueue(conn: sqlite3.Connection, incident_id: str, revision: int, reason: str,
             now_us: int) -> None:
    """Outbox entry in the same transaction as the incident change (spec 8.2, 14.3)."""
    conn.execute(
        "INSERT OR IGNORE INTO notification_outbox(id, incident_id, revision, reason, state, "
        "next_try_us, created_us) VALUES (?,?,?,?,?,?,?)",
        (new_uuid(), incident_id, revision, reason, "pending", now_us, now_us),
    )


def confirm_cause(conn: sqlite3.Connection, incident_id: str, expected_revision: int,
                  candidate_uid: str | None, verdict: str, actor_id: str, request_id: str,
                  now_us: int) -> dict:
    """Admin assessment of a hypothesis, stored separately from the ranking (spec 9.2).

    The caller owns the transaction (see run_idempotent).
    """
    from ..core.errors import ErrorCode, SentinelError

    _load_for_mutation(conn, incident_id, expected_revision)
    row = conn.execute("SELECT summary_json FROM incidents WHERE id=?", (incident_id,)).fetchone()
    summary = json.loads(row["summary_json"])
    known = {h["candidate_uid"] for h in summary.get("hypotheses", [])}
    if candidate_uid is not None and candidate_uid not in known:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "unknown hypothesis candidate")
    summary["confirmed_cause"] = (
        None if candidate_uid is None else
        {"candidate_uid": candidate_uid, "verdict": verdict, "by": actor_id, "at_us": now_us}
    )
    conn.execute(
        "UPDATE incidents SET summary_json=?, updated_at_us=?, revision=revision+1 WHERE id=?",
        (dumps_strict(summary), now_us, incident_id),
    )
    insert_audit(conn, actor_id, request_id, f"incident.cause.{verdict}", incident_id, "ok",
                 now_us)
    return {"revision": expected_revision + 1}


def count_active_incidents(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM incidents WHERE status IN ('open','acknowledged','suppressed')"
    ).fetchone()
    return int(row[0])


_INCIDENT_COLUMNS = (
    "id, fingerprint, status, severity, opened_at_us, updated_at_us, resolved_at_us, "
    "revision, summary_json, previous_status, suppressed_until_us, suppress_reason, "
    "acknowledged_by, acknowledged_at_us"
)


def list_incidents(
    conn: sqlite3.Connection,
    limit: int,
    active_only: bool,
    after: tuple[int, str] | None = None,
) -> list[dict]:
    """Keyset pagination over (updated_at_us DESC, id ASC) (spec 15.3)."""
    clauses: list[str] = []
    params: list[object] = []
    if active_only:
        clauses.append("status IN ('open','acknowledged','suppressed')")
    if after is not None:
        clauses.append("(updated_at_us < ? OR (updated_at_us = ? AND id > ?))")
        params += [after[0], after[0], after[1]]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT {_INCIDENT_COLUMNS} FROM incidents {where} "
        "ORDER BY updated_at_us DESC, id ASC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [_incident_row(r) for r in rows]


def _incident_row(r: sqlite3.Row) -> dict:
    out = dict(r)
    out["summary"] = json.loads(out.pop("summary_json"))
    return out


def get_incident(conn: sqlite3.Connection, incident_id: str) -> dict | None:
    """Incident with its findings and evidence; evidence payloads are allowlisted."""
    row = conn.execute(
        f"SELECT {_INCIDENT_COLUMNS} FROM incidents WHERE id=?", (incident_id,)
    ).fetchone()
    if row is None:
        return None
    incident = _incident_row(row)
    finding_ids = incident["summary"].get("finding_ids", [])
    findings = []
    for fid in finding_ids:
        f = conn.execute(
            "SELECT id, rule_id, rule_version, subject_uid, status, severity, confidence, "
            "coverage_json, first_us, last_us, resolved_us, details_json FROM findings "
            "WHERE id=?",
            (fid,),
        ).fetchone()
        if f is None:
            continue
        evidence = []
        for e in conn.execute(
            "SELECT fe.digest, fe.summary_json, o.kind, o.received_at_us, o.occurred_at_us, "
            "o.payload_json FROM finding_evidence fe LEFT JOIN observations o "
            "ON o.id = fe.observation_id WHERE fe.finding_id=?",
            (fid,),
        ):
            payload = json.loads(e["payload_json"]) if e["payload_json"] else None
            evidence.append({
                "digest": e["digest"],
                "kind": e["kind"],
                "received_at_us": e["received_at_us"],
                "occurred_at_us": e["occurred_at_us"],
                "payload": _safe_evidence_payload(payload),
                "evidence_expired": payload is None,
            })
        fd = dict(f)
        fd["coverage"] = json.loads(fd.pop("coverage_json")).get("status")
        fd["details"] = safe_details(json.loads(fd.pop("details_json") or "{}"))
        fd["evidence"] = evidence
        findings.append(fd)
    incident["findings"] = findings
    return incident


_EVIDENCE_FIELDS = ("from", "to", "availability", "value", "unit", "device_class")
# Finding details shown in the API: only these fields, whatever a rule stored.
_DETAIL_FIELDS = ("resolution", "value", "threshold", "entries", "latest_version", "ratio",
                  "outcome", "resource_label", "imported_at", "missing_count", "stale_count",
                  "missing", "stale", "window_start", "window_end")
_REF_FIELDS = ("entity_id", "current", "pointer")


def safe_details(details: dict | None) -> dict:
    out = {}
    for key in _DETAIL_FIELDS:
        if key not in (details or {}):
            continue
        value = details[key]
        if key in ("missing", "stale") and isinstance(value, list):
            value = [{k: str(v[k]) for k in _REF_FIELDS if k in v}
                     for v in value[:20] if isinstance(v, dict)]
        elif not isinstance(value, (str, int, float, bool)) and value is not None:
            continue
        out[key] = value
    return out


def _safe_evidence_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    return {k: payload[k] for k in _EVIDENCE_FIELDS if k in payload}


def _load_for_mutation(conn: sqlite3.Connection, incident_id: str, expected_revision: int):
    from ..core.errors import ErrorCode, SentinelError

    row = conn.execute(
        "SELECT status, revision FROM incidents WHERE id=?", (incident_id,)
    ).fetchone()
    if row is None:
        raise SentinelError(ErrorCode.NOT_FOUND, "incident not found")
    if row["revision"] != expected_revision:
        raise SentinelError(ErrorCode.CONFLICT, "incident changed", revision=row["revision"])
    return row


def acknowledge_incident(
    conn: sqlite3.Connection, incident_id: str, expected_revision: int, actor_id: str,
    request_id: str, now_us: int,
) -> dict:
    """open -> acknowledged; acknowledged means read, not fixed (spec 29).

    The caller owns the transaction (see run_idempotent).
    """
    from ..core.errors import ErrorCode, SentinelError

    row = _load_for_mutation(conn, incident_id, expected_revision)
    if row["status"] != "open":
        raise SentinelError(ErrorCode.CONFLICT, "only open incidents can be acknowledged",
                            status=row["status"])
    conn.execute(
        "UPDATE incidents SET status='acknowledged', acknowledged_by=?, "
        "acknowledged_at_us=?, updated_at_us=?, revision=revision+1 WHERE id=?",
        (actor_id, now_us, now_us, incident_id),
    )
    insert_audit(conn, actor_id, request_id, "incident.ack", incident_id, "ok", now_us)
    return {"revision": expected_revision + 1, "status": "acknowledged"}


def suppress_incident(
    conn: sqlite3.Connection, incident_id: str, expected_revision: int, until_us: int,
    reason: str, actor_id: str, request_id: str, now_us: int,
) -> dict:
    """open|acknowledged -> suppressed until a deadline; muting does not change the failure.

    The caller owns the transaction (see run_idempotent).
    """
    from ..core.errors import ErrorCode, SentinelError

    if until_us <= now_us:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "until must be in the future")
    row = _load_for_mutation(conn, incident_id, expected_revision)
    if row["status"] not in ("open", "acknowledged"):
        raise SentinelError(ErrorCode.CONFLICT, "incident cannot be suppressed",
                            status=row["status"])
    conn.execute(
        "UPDATE incidents SET status='suppressed', previous_status=?, suppressed_until_us=?, "
        "suppress_reason=?, updated_at_us=?, revision=revision+1 WHERE id=?",
        (row["status"], until_us, reason, now_us, incident_id),
    )
    insert_audit(conn, actor_id, request_id, "incident.suppress", incident_id, "ok", now_us)
    return {"revision": expected_revision + 1, "status": "suppressed"}


IDEMPOTENCY_TTL_US = 24 * 3600 * 1_000_000


def run_idempotent(
    conn: sqlite3.Connection,
    *,
    request_key: str,
    request_hash: str,
    actor_id: str,
    kind: str,
    now_us: int,
    op,
) -> dict:
    """Execute `op(conn)` once per request_key within one transaction (spec 15.1, 27.4).

    Same key and hash within 24 h returns the stored result; same key with a different
    hash is a conflict.
    """
    from ..core.errors import ErrorCode, SentinelError

    with _tx(conn):
        existing = conn.execute(
            "SELECT request_hash, result_json, updated_at_us FROM jobs WHERE request_key=?",
            (request_key,),
        ).fetchone()
        if existing is not None and now_us - existing["updated_at_us"] < IDEMPOTENCY_TTL_US:
            if existing["request_hash"] != request_hash:
                raise SentinelError(ErrorCode.CONFLICT, "request_key reused with other content")
            return json.loads(existing["result_json"])
        if existing is not None:
            conn.execute("DELETE FROM jobs WHERE request_key=?", (request_key,))
        result = op(conn)
        conn.execute(
            "INSERT INTO jobs(id, actor_id, kind, state, updated_at_us, request_key, "
            "request_hash, result_json) VALUES (?,?,?,?,?,?,?,?)",
            (new_uuid(), actor_id, kind, "succeeded", now_us, request_key, request_hash,
             dumps_strict(result)),
        )
        return result


def expire_suppressions(conn: sqlite3.Connection, now_us: int) -> int:
    """Expired suppression returns to the previous active status (spec 9.1)."""
    with _tx(conn):
        cur = conn.execute(
            "UPDATE incidents SET status=COALESCE(previous_status, 'open'), previous_status=NULL, "
            "suppressed_until_us=NULL, suppress_reason=NULL, updated_at_us=?, "
            "revision=revision+1 WHERE status='suppressed' AND suppressed_until_us <= ?",
            (now_us, now_us),
        )
        return cur.rowcount


def insert_audit(
    conn: sqlite3.Connection, actor_id: str | None, request_id: str, operation: str,
    resource_id: str | None, result: str, now_us: int,
) -> None:
    """Audit rows never contain secrets or full payloads. Caller owns the transaction."""
    conn.execute(
        "INSERT INTO audit(id, actor_id, request_id, operation, resource_id, result, created_us) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_uuid(), actor_id, request_id, operation, resource_id, result, now_us),
    )


def list_coverage_gaps(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT source_id, scope, from_us, to_us, status, reason FROM coverage "
        "ORDER BY from_us DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def storage_stats(conn: sqlite3.Connection) -> dict:
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    return {
        "db_bytes": page_count * page_size,
        "observations": conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
        "aggregates": conn.execute("SELECT COUNT(*) FROM numeric_aggregates").fetchone()[0],
        "snapshots": conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
        "audit_bytes": audit_bytes(conn),
        "schema_version": conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
    }


def audit_bytes(conn: sqlite3.Connection) -> int:
    """Logical size of the audit log (spec 7.3 budget), not counting index pages."""
    return conn.execute(
        "SELECT COALESCE(SUM(LENGTH(id) + LENGTH(COALESCE(actor_id, '')) + LENGTH(request_id) "
        "+ LENGTH(operation) + LENGTH(COALESCE(resource_id, '')) + LENGTH(result) + 8), 0) "
        "FROM audit").fetchone()[0]


class _tx:
    """BEGIN IMMEDIATE / COMMIT for a connection in autocommit mode."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self._conn.execute("BEGIN IMMEDIATE")
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self._conn.execute("ROLLBACK" if exc_type else "COMMIT")


def load_active_findings(conn: sqlite3.Connection) -> list[Finding]:
    from ..core.models import CoverageStatus, Severity

    rows = conn.execute(
        "SELECT id, rule_id, rule_version, subject_uid, status, severity, confidence, "
        "coverage_json, first_us, last_us, details_json FROM findings WHERE status='active'"
    ).fetchall()
    out = []
    for r in rows:
        cov = json.loads(r["coverage_json"]).get("status", "complete")
        out.append(Finding(
            id=r["id"], rule_id=r["rule_id"], rule_version=r["rule_version"],
            subject_uid=r["subject_uid"], status=FindingStatus.ACTIVE,
            severity=Severity(r["severity"]), first_seen_us=r["first_us"],
            last_seen_us=r["last_us"], confidence=r["confidence"],
            coverage=CoverageStatus(cov), details=json.loads(r["details_json"] or "{}"),
        ))
    return out
