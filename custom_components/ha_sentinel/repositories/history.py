"""Resources and snapshots of the configuration history (spec 10). Run via DatabaseWorker."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..core.errors import ErrorCode, SentinelError
from ..core.models import dumps_strict, new_uuid
from .blobs import BlobStore
from .repository import _tx, insert_audit

MAX_SNAPSHOTS_PER_RESOURCE = 50
RESOURCE_KINDS = ("automation", "script", "blueprint", "sentinel_options")


def save_snapshot(
    conn: sqlite3.Connection,
    blobs: BlobStore,
    *,
    resource_id: str | None,
    provider: str,
    kind: str,
    label: str,
    canonical_tree: Any,
    canonical_hash: str,
    raw: bytes | None,
    raw_hash: str | None,
    redacted_paths: list[str],
    validation: dict[str, Any],
    actor_id: str | None,
    request_id: str,
    now_us: int,
    restorable: bool = False,
    locator: dict[str, Any] | None = None,
    write_policy: str = "export_only",
) -> dict[str, Any]:
    """Store a snapshot; identical canonical and raw content is deduplicated.

    Only snapshots of managed files (type M) with the raw original kept are restorable.

    The caller owns the transaction (see repository.run_idempotent) when it needs
    idempotency; otherwise use save_snapshot_tx.
    """
    if kind not in RESOURCE_KINDS:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "unknown resource kind")
    if resource_id is None:
        resource_id = new_uuid()
        conn.execute(
            "INSERT INTO resources(id, provider, locator_json, current_hash, write_policy, kind, "
            "label, created_us) VALUES (?,?,?,?,?,?,?,?)",
            (resource_id, provider, dumps_strict(locator or {"label": label}), canonical_hash,
             write_policy, kind, label, now_us),
        )
    else:
        row = conn.execute("SELECT kind FROM resources WHERE id=?", (resource_id,)).fetchone()
        if row is None:
            raise SentinelError(ErrorCode.NOT_FOUND, "resource not found")
        if row["kind"] != kind:
            raise SentinelError(ErrorCode.CONFLICT, "resource kind mismatch")
        latest = conn.execute(
            "SELECT id, hash, raw_hash FROM snapshots WHERE resource_id=? "
            "ORDER BY created_us DESC, rowid DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        if latest and latest["hash"] == canonical_hash and latest["raw_hash"] == raw_hash:
            return {"resource_id": resource_id, "snapshot_id": latest["id"],
                    "deduplicated": True}

    # Blobs are content-addressed, so writing them before COMMIT is safe: an orphan blob
    # after a crash is harmless and is reused by the next identical write.
    # Stored in source key order so exports stay readable; the hash sorts keys itself.
    blob_ref = blobs.put(json.dumps(canonical_tree, ensure_ascii=False,
                                    allow_nan=False).encode())
    raw_ref = blobs.put(raw) if raw is not None else None
    snapshot_id = new_uuid()
    conn.execute(
        "INSERT INTO snapshots(id, resource_id, hash, blob_ref, created_us, schema_version, "
        "restorable, raw_hash, raw_blob_ref, redacted_paths_json, validation_json, "
        "parser_version, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snapshot_id, resource_id, canonical_hash, blob_ref, now_us, 1,
         1 if restorable and raw is not None else 0,
         raw_hash, raw_ref, dumps_strict(redacted_paths), dumps_strict(validation), 1,
         actor_id),
    )
    conn.execute("UPDATE resources SET current_hash=?, label=? WHERE id=?",
                 (canonical_hash, label, resource_id))
    # Keep at most 50 snapshots per resource, always keeping the newest (spec 7.3).
    conn.execute(
        "DELETE FROM snapshots WHERE resource_id=? AND id NOT IN (SELECT id FROM snapshots "
        "WHERE resource_id=? ORDER BY created_us DESC, rowid DESC LIMIT ?)",
        (resource_id, resource_id, MAX_SNAPSHOTS_PER_RESOURCE),
    )
    insert_audit(conn, actor_id, request_id, f"snapshot.create.{kind}", resource_id, "ok",
                 now_us)
    return {"resource_id": resource_id, "snapshot_id": snapshot_id, "deduplicated": False}


def save_snapshot_tx(conn: sqlite3.Connection, blobs: BlobStore, **kwargs) -> dict[str, Any]:
    with _tx(conn):
        return save_snapshot(conn, blobs, **kwargs)


def find_resource(conn: sqlite3.Connection, provider: str, kind: str) -> str | None:
    row = conn.execute("SELECT id FROM resources WHERE provider=? AND kind=? LIMIT 1",
                       (provider, kind)).fetchone()
    return row["id"] if row else None


def list_resources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT r.id, r.provider, r.kind, r.label, r.write_policy, r.created_us, "
        "(SELECT COUNT(*) FROM snapshots s WHERE s.resource_id=r.id) AS snapshots, "
        "(SELECT MAX(created_us) FROM snapshots s WHERE s.resource_id=r.id) AS updated_us "
        "FROM resources r ORDER BY updated_us DESC, r.id"
    ).fetchall()
    return [dict(r) for r in rows]


def _snapshot_meta(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "resource_id": row["resource_id"],
        "canonical_hash": row["hash"],
        "raw_hash": row["raw_hash"],
        "created_us": row["created_us"],
        "restorable": bool(row["restorable"]),
        "raw_available": row["raw_blob_ref"] is not None,
        "redacted_paths": json.loads(row["redacted_paths_json"]),
        "validation": json.loads(row["validation_json"]),
        "parser_version": row["parser_version"],
    }


_META = ("id, resource_id, hash, raw_hash, created_us, restorable, raw_blob_ref, blob_ref, "
         "redacted_paths_json, validation_json, parser_version")


def list_snapshots(conn: sqlite3.Connection, resource_id: str, limit: int,
                   after: tuple[int, str] | None = None) -> list[dict[str, Any]]:
    params: list[Any] = [resource_id]
    where = "resource_id=?"
    if after is not None:
        where += " AND (created_us < ? OR (created_us = ? AND id < ?))"
        params += [after[0], after[0], after[1]]
    rows = conn.execute(
        f"SELECT {_META} FROM snapshots WHERE {where} ORDER BY created_us DESC, rowid DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [_snapshot_meta(r) for r in rows]


def get_snapshot(conn: sqlite3.Connection, blobs: BlobStore,
                 snapshot_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_META}, (SELECT kind FROM resources WHERE id=resource_id) AS kind, "
        "(SELECT label FROM resources WHERE id=resource_id) AS label "
        "FROM snapshots WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    meta = _snapshot_meta(row)
    meta["kind"] = row["kind"]
    meta["label"] = row["label"]
    meta["tree"] = json.loads(blobs.get(row["blob_ref"]))
    meta["raw"] = blobs.get(row["raw_blob_ref"]).decode() if row["raw_blob_ref"] else None
    return meta


def latest_imports(conn: sqlite3.Connection, blobs: BlobStore) -> list[dict[str, Any]]:
    """Newest snapshot of every imported automation/script/blueprint for the graph."""
    rows = conn.execute(
        "SELECT r.id AS resource_id, r.kind, r.label, s.id AS snapshot_id, s.blob_ref, "
        "s.created_us FROM resources r JOIN snapshots s ON s.id = (SELECT id FROM snapshots "
        "WHERE resource_id=r.id ORDER BY created_us DESC, rowid DESC LIMIT 1) "
        "WHERE r.kind IN ('automation','script','blueprint')"
    ).fetchall()
    return [{**dict(r), "tree": json.loads(blobs.get(r["blob_ref"]))} for r in rows]


def get_resource(conn: sqlite3.Connection, resource_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, provider, kind, label, write_policy, locator_json, current_hash "
        "FROM resources WHERE id=?", (resource_id,),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["locator"] = json.loads(out.pop("locator_json"))
    return out


def find_managed(conn: sqlite3.Connection, relative_path: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM resources WHERE provider='managed' "
        "AND json_extract(locator_json, '$.path')=?", (relative_path,),
    ).fetchone()
    return row["id"] if row else None


def snapshot_raw(conn: sqlite3.Connection, blobs: BlobStore, snapshot_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, resource_id, raw_hash, raw_blob_ref, restorable FROM snapshots WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "resource_id": row["resource_id"], "raw_hash": row["raw_hash"],
            "restorable": bool(row["restorable"]),
            "raw": blobs.get(row["raw_blob_ref"]) if row["raw_blob_ref"] else None}
