"""Jobs, retention purge and data deletion (spec 7.3, 15.3, 17.1, 27.4).

Run every function through DatabaseWorker.run(). Purge works in bounded batches so the
DB worker yields between them; nothing here touches files outside Sentinel's directory.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..core.errors import ErrorCode, SentinelError
from ..core.models import JobState, dumps_strict, new_uuid
from .repository import IDEMPOTENCY_TTL_US, _tx, insert_audit

DAY_US = 24 * 3600 * 1_000_000
JOB_RESULT_TTL_US = DAY_US
HEAVY_JOB_KINDS = ("export",)
MAX_HEAVY_JOBS = 2
SNAPSHOTS_PER_RESOURCE = 50
PURGE_BATCH = 5000


# --- Jobs -----------------------------------------------------------------------------


def create_job(conn: sqlite3.Connection, *, kind: str, request_key: str, request_hash: str,
               actor_id: str, now_us: int) -> tuple[str, bool]:
    """Insert a queued job idempotently. Returns (job_id, created)."""
    with _tx(conn):
        row = conn.execute(
            "SELECT id, request_hash, updated_at_us FROM jobs WHERE request_key=?",
            (request_key,),
        ).fetchone()
        if row is not None and now_us - row["updated_at_us"] < IDEMPOTENCY_TTL_US:
            if row["request_hash"] != request_hash:
                raise SentinelError(ErrorCode.CONFLICT, "request_key reused with other content")
            return row["id"], False
        if row is not None:
            conn.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
        marks = ",".join("?" * len(HEAVY_JOB_KINDS))
        running = conn.execute(
            f"SELECT COUNT(*) FROM jobs WHERE kind IN ({marks}) "
            "AND state IN ('queued','running')",
            HEAVY_JOB_KINDS,
        ).fetchone()[0]
        if kind in HEAVY_JOB_KINDS and running >= MAX_HEAVY_JOBS:
            raise SentinelError(ErrorCode.RATE_LIMITED, "too many heavy jobs running")
        job_id = new_uuid()
        conn.execute(
            "INSERT INTO jobs(id, actor_id, kind, state, updated_at_us, request_key, "
            "request_hash, checkpoint_json) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, actor_id, kind, str(JobState.QUEUED), now_us, request_key, request_hash,
             dumps_strict({"progress": 0, "cancel_requested": False})),
        )
        insert_audit(conn, actor_id, request_key, f"job.create.{kind}", job_id, "accepted",
                     now_us)
        return job_id, True


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, actor_id, kind, state, updated_at_us, checkpoint_json, result_json "
        "FROM jobs WHERE id=?", (job_id,),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["checkpoint"] = json.loads(out.pop("checkpoint_json") or "{}")
    out["result"] = json.loads(out.pop("result_json") or "null")
    return out


def update_job(conn: sqlite3.Connection, job_id: str, *, state: JobState | None = None,
               progress: float | None = None, result: dict | None = None,
               error_code: str | None = None, now_us: int) -> None:
    """Terminal states never go back to running (spec 6.1 Job invariant)."""
    with _tx(conn):
        row = conn.execute("SELECT state, checkpoint_json FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        if row is None or JobState(row["state"]).is_terminal:
            return
        checkpoint = json.loads(row["checkpoint_json"] or "{}")
        if progress is not None:
            checkpoint["progress"] = progress
        if error_code is not None:
            checkpoint["error_code"] = error_code
        conn.execute(
            "UPDATE jobs SET state=COALESCE(?, state), checkpoint_json=?, "
            "result_json=COALESCE(?, result_json), updated_at_us=? WHERE id=?",
            (str(state) if state else None, dumps_strict(checkpoint),
             dumps_strict(result) if result is not None else None, now_us, job_id),
        )


def request_cancel(conn: sqlite3.Connection, job_id: str, now_us: int) -> str:
    with _tx(conn):
        row = conn.execute("SELECT state, checkpoint_json FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        if row is None:
            raise SentinelError(ErrorCode.NOT_FOUND, "job not found")
        if JobState(row["state"]).is_terminal:
            return row["state"]
        checkpoint = json.loads(row["checkpoint_json"] or "{}")
        checkpoint["cancel_requested"] = True
        conn.execute("UPDATE jobs SET checkpoint_json=?, updated_at_us=? WHERE id=?",
                     (dumps_strict(checkpoint), now_us, job_id))
        return row["state"]


def cancel_requested(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute("SELECT checkpoint_json FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and json.loads(row["checkpoint_json"] or "{}").get("cancel_requested"))


def interrupt_unfinished(conn: sqlite3.Connection, now_us: int) -> int:
    """After a restart running work is interrupted, never blindly retried (spec 27.4)."""
    with _tx(conn):
        return conn.execute(
            "UPDATE jobs SET state='interrupted', updated_at_us=? "
            "WHERE state IN ('queued','running') AND kind NOT LIKE 'incident.%' "
            "AND kind NOT LIKE 'import.%'",
            (now_us,),
        ).rowcount


def find_export(conn: sqlite3.Connection, export_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT actor_id, result_json FROM jobs WHERE kind='export' AND state='succeeded' "
        "AND json_extract(result_json, '$.export_id')=?",
        (export_id,),
    ).fetchone()
    if row is None:
        return None
    return {"actor_id": row["actor_id"], **json.loads(row["result_json"])}


# --- Retention purge ---------------------------------------------------------------------


def purge_batch(conn: sqlite3.Connection, now_us: int, *, observations_days: int,
                incidents_days: int, snapshot_days: int = 30, audit_days: int = 180) -> dict:
    """Delete at most PURGE_BATCH rows per class; call repeatedly until it returns zeros."""
    counts: dict[str, int] = {}
    with _tx(conn):
        cutoff = now_us - observations_days * DAY_US
        # Samples leaving the retention survive as minute aggregates (spec 7.3).
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS purge_ids(id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM purge_ids")
        conn.execute("INSERT INTO purge_ids SELECT id FROM observations WHERE received_at_us < ? "
                     "LIMIT ?", (cutoff, PURGE_BATCH))
        _aggregate(conn, "SELECT o.subject_uid, "
                   "COALESCE(json_extract(o.payload_json, '$.purpose'), 'battery') AS kind, "
                   "(o.received_at_us / 60000000) * 60000000 AS bucket, COUNT(*) AS n, "
                   "MIN(v) AS vmin, MAX(v) AS vmax, SUM(v) AS vsum FROM (SELECT *, "
                   "CAST(json_extract(payload_json, '$.value') AS REAL) AS v FROM observations) o "
                   "JOIN purge_ids p ON p.id = o.id WHERE o.kind = 'numeric.sample' "
                   "AND o.subject_uid IS NOT NULL AND v IS NOT NULL "
                   "GROUP BY o.subject_uid, kind, bucket", "minute")
        counts["observations"] = conn.execute(
            "DELETE FROM observations WHERE id IN (SELECT id FROM purge_ids)").rowcount
        # Minute buckets older than 30 days become hourly; hourly ones are kept a year.
        _aggregate(conn, "SELECT subject_uid, kind, (bucket_us / 3600000000) * 3600000000, "
                   "SUM(n), MIN(vmin), MAX(vmax), SUM(vsum) FROM numeric_aggregates WHERE "
                   f"granularity='minute' AND bucket_us < {now_us - 30 * DAY_US} "
                   "GROUP BY subject_uid, kind, 3", "hour")
        counts["aggregates"] = conn.execute(
            "DELETE FROM numeric_aggregates WHERE (granularity='minute' AND bucket_us < ?) "
            "OR (granularity='hour' AND bucket_us < ?)",
            (now_us - 30 * DAY_US, now_us - 365 * DAY_US)).rowcount
        counts["probe_results"] = conn.execute(
            "DELETE FROM probe_results WHERE rowid IN (SELECT rowid FROM probe_results "
            "WHERE at_us < ? LIMIT ?)",
            (now_us - observations_days * DAY_US, PURGE_BATCH),
        ).rowcount
        counts["coverage"] = conn.execute(
            "DELETE FROM coverage WHERE rowid IN (SELECT rowid FROM coverage "
            "WHERE to_us IS NOT NULL AND to_us < ? LIMIT ?)",
            (now_us - observations_days * DAY_US, PURGE_BATCH),
        ).rowcount
        # Active incidents are never purged; evidence rows go with their incident.
        old = [r[0] for r in conn.execute(
            "SELECT id FROM incidents WHERE status='resolved' AND resolved_at_us < ? LIMIT ?",
            (now_us - incidents_days * DAY_US, PURGE_BATCH),
        )]
        for chunk in _chunks(old, 500):
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM notification_outbox WHERE incident_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM incident_evidence WHERE incident_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM incidents WHERE id IN ({marks})", chunk)
        counts["incidents"] = len(old)
        old_findings = [r[0] for r in conn.execute(
            "SELECT id FROM findings WHERE status='resolved' AND resolved_us < ? LIMIT ?",
            (now_us - incidents_days * DAY_US, PURGE_BATCH),
        )]
        for chunk in _chunks(old_findings, 500):
            marks = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM finding_evidence WHERE finding_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM findings WHERE id IN ({marks})", chunk)
        counts["findings"] = len(old_findings)
        # Snapshots: at most SNAPSHOTS_PER_RESOURCE per resource (newest kept, spec 7.3).
        counts["snapshots_capped"] = conn.execute(
            "DELETE FROM snapshots WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
            "(PARTITION BY resource_id ORDER BY created_us DESC, rowid DESC) AS rn "
            "FROM snapshots) WHERE rn > ? LIMIT ?)",
            (SNAPSHOTS_PER_RESOURCE, PURGE_BATCH)).rowcount
        # Snapshots: older than 30 days, but always keep the newest per resource.
        counts["snapshots"] = conn.execute(
            "DELETE FROM snapshots WHERE id IN (SELECT s.id FROM snapshots s WHERE "
            "s.created_us < ? AND s.id != (SELECT id FROM snapshots WHERE "
            "resource_id = s.resource_id ORDER BY created_us DESC, rowid DESC LIMIT 1) LIMIT ?)",
            (now_us - snapshot_days * DAY_US, PURGE_BATCH),
        ).rowcount
        counts["jobs"] = conn.execute(
            "DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE state IN "
            "('succeeded','failed','cancelled','interrupted') AND updated_at_us < ? LIMIT ?)",
            (now_us - JOB_RESULT_TTL_US, PURGE_BATCH),
        ).rowcount
        counts["audit"] = conn.execute(
            "DELETE FROM audit WHERE id IN (SELECT id FROM audit WHERE created_us < ? LIMIT ?)",
            (now_us - audit_days * DAY_US, PURGE_BATCH),
        ).rowcount
    return counts


def _aggregate(conn: sqlite3.Connection, select_sql: str, granularity: str) -> None:
    """Merge (subject, kind, bucket, n, min, max, sum) rows into numeric_aggregates."""
    rows = conn.execute(select_sql).fetchall()
    conn.executemany(
        "INSERT INTO numeric_aggregates(subject_uid, kind, granularity, bucket_us, n, vmin, "
        "vmax, vsum) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(subject_uid, kind, granularity, "
        "bucket_us) DO UPDATE SET n = n + excluded.n, vmin = MIN(vmin, excluded.vmin), "
        "vmax = MAX(vmax, excluded.vmax), vsum = vsum + excluded.vsum",
        [(r[0], r[1] if r[1] in ("battery", "signal") else "battery", granularity, r[2], r[3],
          r[4], r[5], r[6]) for r in rows])


def referenced_blobs(conn: sqlite3.Connection) -> set[str]:
    refs: set[str] = set()
    for row in conn.execute("SELECT blob_ref, raw_blob_ref FROM snapshots"):
        refs.add(row[0])
        if row[1]:
            refs.add(row[1])
    for row in conn.execute("SELECT diff_blob_ref FROM graph_revisions "
                            "WHERE diff_blob_ref IS NOT NULL"):
        refs.add(row[0])
    return refs


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --- Data deletion (spec 17.1) ---------------------------------------------------------------

DELETE_SCOPES = {
    "observations": ("observations", "coverage", "checkpoints"),
    "incidents": ("notification_outbox", "incident_evidence", "incidents",
                  "finding_evidence", "findings"),
    "config_history": ("operations", "change_plans", "snapshots", "resources",
                       "graph_revisions"),
}


def delete_scope(conn: sqlite3.Connection, scope: str, actor_id: str, request_id: str,
                 now_us: int) -> dict[str, int]:
    """Delete one data class. 'all' is handled by the runtime (unload, remove files)."""
    if scope not in DELETE_SCOPES:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "unknown scope")
    counts: dict[str, int] = {}
    with _tx(conn):
        for table in DELETE_SCOPES[scope]:
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608
        insert_audit(conn, actor_id, request_id, f"data.delete.{scope}", None, "ok", now_us)
    return counts
