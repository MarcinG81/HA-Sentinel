"""Export jobs, retention purge and data deletion (spec 7.3, 15.4, 17.1).

File I/O runs in executors; SQLite runs on the DB worker. Only files inside
<config>/ha_sentinel are ever removed, never Recorder data or HA backups.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DATA_DIR,
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_CLEANUP_WINDOW_DAYS,
    OPT_RETENTION_INCIDENTS_DAYS,
    OPT_RETENTION_OBSERVATIONS_DAYS,
    OPT_STORAGE_MAX_TOTAL_MIB,
    SCHEMA_VERSION,
)
from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.models import Finding, FindingStatus, JobState, Severity, new_uuid
from .modules.export.builder import ExportTooLargeError, build_export
from .repositories import history, jobs
from .repositories import repository as repo

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

_LOGGER = logging.getLogger(__name__)
PURGE_INTERVAL = timedelta(hours=1)
EXPORT_TTL_US = jobs.JOB_RESULT_TTL_US
AUDIT_BUDGET_BYTES = 64 * 1024 * 1024  # spec 7.3
BLOB_GRACE_S = 3600
MAX_PURGE_ROUNDS = 200


class DataServices:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass = runtime.hass
        self.root = Path(self.hass.config.path(DATA_DIR))
        self.exports_dir = self.root / "exports"
        self._unsubs: list[CALLBACK_TYPE] = []
        self.last_purge: dict[str, Any] | None = None
        self.storage_ratio = 0.0
        self.storage_level = 0  # 0 ok, 1 soft (>= 80 %), 2 hard (>= 95 %)
        self.findings: dict[str, Finding] = {}
        self._purge_task: asyncio.Task | None = None

    async def async_start(self) -> None:
        if self.rt.storage_writable:
            now = self.rt.clock.now_us()
            await self.rt.db.run(lambda c: jobs.interrupt_unfinished(c, now))
        self._unsubs.append(async_track_time_interval(self.hass, self._purge_tick,
                                                      PURGE_INTERVAL))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._purge_task is not None and not self._purge_task.done():
            # Let a running purge finish its batch before the database closes.
            await asyncio.wait({self._purge_task}, timeout=5)

    # --- Storage pressure (spec 7.3, DOC010) ---------------------------------------------------

    def restore(self, findings: list[Finding]) -> list[Finding]:
        """Take over a restored DOC010; the next pressure check confirms or closes it."""
        rest = []
        for f in findings:
            if f.rule_id == "DOC010" and f.subject_uid == "sentinel:storage":
                self.findings[f.subject_uid] = f
            else:
                rest.append(f)
        return rest

    def _usage_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    async def check_audit_budget(self) -> bool:
        """Audit over its budget blocks new mutations (spec 7.3); purge may free it."""
        used = await self.rt.db.run(repo.audit_bytes)
        self.rt.audit_blocked = used > AUDIT_BUDGET_BYTES
        return self.rt.audit_blocked

    async def check_pressure(self) -> int:
        """Soft 80 % / hard 95 % of the storage budget; resolves below 75 %."""
        opts = {**DEFAULT_OPTIONS, **self.rt.entry.options}
        budget = int(opts[OPT_STORAGE_MAX_TOTAL_MIB]) * 1024 * 1024
        used = await self.hass.async_add_executor_job(self._usage_bytes)
        if self.rt.storage_writable:
            await self.check_audit_budget()
        self.storage_ratio = used / budget if budget else 0.0
        level = 2 if self.storage_ratio >= 0.95 else 1 if self.storage_ratio >= 0.80 else 0
        if level == 0 and self.storage_level and self.storage_ratio >= 0.75:
            level = 1  # hysteresis
        self.storage_level = level
        now = self.rt.clock.now_us()
        uid = "sentinel:storage"
        current = self.findings.get(uid)
        severity = Severity.HIGH if level == 2 else Severity.WARNING
        if level and (current is None or current.severity is not severity):
            f = Finding(id=current.id if current else new_uuid(), rule_id="DOC010",
                        rule_version=1, subject_uid=uid, status=FindingStatus.ACTIVE,
                        severity=severity,
                        first_seen_us=current.first_seen_us if current else now,
                        last_seen_us=now, details={"ratio": round(self.storage_ratio, 3)})
            self.findings[uid] = f
            if self.rt.storage_writable:
                await self.rt.db.run(lambda c: repo.upsert_finding(c, f, now))
        elif not level and current is not None:
            current.status = FindingStatus.RESOLVED
            current.resolved_at_us = current.last_seen_us = now
            del self.findings[uid]
            if self.rt.storage_writable:
                await self.rt.db.run(lambda c: repo.upsert_finding(c, current, now))
        return level

    # --- Export -----------------------------------------------------------------------------

    async def start_export(self, *, scopes: list[str], redacted: bool, request_key: str,
                           request_hash: str, actor_id: str) -> dict[str, Any]:
        if not self.rt.storage_writable:
            raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE, "storage is read-only")
        self.rt.require_degradation_below(2)
        now = self.rt.clock.now_us()
        job_id, created = await self.rt.db.run(lambda c: jobs.create_job(
            c, kind="export", request_key=request_key, request_hash=request_hash,
            actor_id=actor_id, now_us=now))
        if created:
            self.rt.entry.async_create_task(
                self.hass, self._run_export(job_id, scopes, redacted, actor_id),
                f"{DOMAIN}_export")
        return {"job_id": job_id, "state": "queued" if created else "existing"}

    async def _run_export(self, job_id: str, scopes: list[str], redacted: bool,
                          actor_id: str) -> None:
        db = self.rt.db
        clock = self.rt.clock
        try:
            await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.RUNNING,
                                                   progress=0.05, now_us=clock.now_us()))
            sections = await self._collect(scopes, redacted, job_id)
            if sections is None:
                return
            export_id = new_uuid()
            salt = secrets.token_bytes(32)
            created_at = us_to_rfc3339(clock.now_us())
            data = await self.hass.async_add_executor_job(lambda: build_export(
                scopes=scopes, redacted=redacted, salt=salt, created_at=created_at,
                versions={"sentinel": self.rt.version, "home_assistant": HA_VERSION,
                          "schema": SCHEMA_VERSION, "api": 1},
                **sections))
            if await db.run(lambda c: jobs.cancel_requested(c, job_id)):
                await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.CANCELLED,
                                                       now_us=clock.now_us()))
                return
            path = await self.hass.async_add_executor_job(self._write_export, export_id, data)
            result = {"export_id": export_id, "bytes": len(data), "scopes": sorted(scopes),
                      "redacted": redacted, "file": path.name,
                      "expires_us": clock.now_us() + EXPORT_TTL_US}
            await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.SUCCEEDED,
                                                   progress=1.0, result=result,
                                                   now_us=clock.now_us()))
        except ExportTooLargeError:
            await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.FAILED,
                                                   error_code="export_too_large",
                                                   now_us=clock.now_us()))
        except Exception:
            _LOGGER.exception("Sentinel export failed")
            await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.FAILED,
                                                   error_code="internal_error",
                                                   now_us=clock.now_us()))
        self.rt.bump()

    async def _collect(self, scopes: list[str], redacted: bool,
                       job_id: str) -> dict[str, Any] | None:
        db = self.rt.db
        sections: dict[str, Any] = {"diagnostics": None, "incidents": None, "resources": None,
                                    "snapshots": None, "yaml_files": None}
        steps = len(scopes)
        for i, scope in enumerate(sorted(scopes)):
            # Cancel checkpoint between steps (spec 21.2).
            if await db.run(lambda c: jobs.cancel_requested(c, job_id)):
                await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.CANCELLED,
                                                       now_us=self.rt.clock.now_us()))
                return None
            if scope == "diagnostics":
                diag = await self.rt.diagnostics()
                diag["capabilities"] = [c.as_dict() for c in self.rt.capabilities.values()]
                diag["modules"] = self.rt.overview()["modules"]
                diag["counters"] = self.rt.overview()["counters"]
                sections["diagnostics"] = diag
            elif scope == "incidents":
                sections["incidents"] = await db.run(
                    lambda c: repo.list_incidents(c, 1000, False))
            elif scope == "config_history":
                resources = await db.run(history.list_resources)
                snaps: list[dict] = []
                files: dict[str, str] = {}
                for r in resources:
                    rows = await db.run(lambda c, rid=r["id"]: history.list_snapshots(c, rid, 50))
                    snaps += rows
                    if not redacted and rows and self.rt.config is not None:
                        text = (await self.rt.config.export_text(rows[0]["id"]))["content"]
                        files[f"{r['kind']}-{r['id'][:8]}.yaml"] = text
                sections["resources"] = resources
                sections["snapshots"] = snaps
                sections["yaml_files"] = files
            progress = 0.1 + 0.8 * (i + 1) / steps
            await db.run(lambda c, pr=progress: jobs.update_job(
                c, job_id, progress=pr, now_us=self.rt.clock.now_us()))
        return sections

    def _write_export(self, export_id: str, data: bytes) -> Path:
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        path = self.exports_dir / f"{export_id}.zip"
        fd, tmp = tempfile.mkstemp(dir=self.exports_dir, prefix=".tmp-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path

    async def export_file(self, export_id: str, user_id: str, is_admin: bool) -> Path:
        """Authorisation for downloads: current admin and the export's owner (spec 15.4)."""
        if not is_admin:
            raise SentinelError(ErrorCode.FORBIDDEN, "admin required")
        meta = await self.rt.db.run(lambda c: jobs.find_export(c, export_id))
        if meta is None or meta["actor_id"] != user_id:
            raise SentinelError(ErrorCode.NOT_FOUND, "export not found")
        if meta["expires_us"] < self.rt.clock.now_us():
            raise SentinelError(ErrorCode.NOT_FOUND, "export expired")
        path = self.exports_dir / f"{export_id}.zip"
        if not await self.hass.async_add_executor_job(path.is_file):
            raise SentinelError(ErrorCode.NOT_FOUND, "export file missing")
        return path

    # --- Retention purge -----------------------------------------------------------------------

    @callback
    def _purge_tick(self, _now) -> None:
        if self.rt.stopping or (self._purge_task is not None and not self._purge_task.done()):
            return
        self._purge_task = self.rt.entry.async_create_task(self.hass, self.async_purge(),
                                                           f"{DOMAIN}_purge")

    async def async_purge(self) -> dict[str, int]:
        if not self.rt.storage_writable or self.rt.stopping:
            return {}
        opts = {**DEFAULT_OPTIONS, **self.rt.entry.options}
        totals: dict[str, int] = {}
        for _ in range(MAX_PURGE_ROUNDS):
            now = self.rt.clock.now_us()
            counts = await self.rt.db.run(lambda c, now=now: jobs.purge_batch(
                c, now, observations_days=int(opts[OPT_RETENTION_OBSERVATIONS_DAYS]),
                incidents_days=int(opts[OPT_RETENTION_INCIDENTS_DAYS]),
                cleanup_days=int(opts[OPT_CLEANUP_WINDOW_DAYS])))
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
            if not any(counts.values()):
                break
        if totals.get("entities") or totals.get("devices"):
            cutoff = self.rt.clock.now_us() - int(opts[OPT_CLEANUP_WINDOW_DAYS]) * 86_400_000_000
            self.rt.inventory.forget_deleted_before(cutoff)
            await self.rt.reload_object_settings()
        refs = await self.rt.db.run(jobs.referenced_blobs)
        totals["blobs"] = await self.hass.async_add_executor_job(self._purge_files, refs)
        await self.rt.db.run(lambda c: c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall())
        await self.check_audit_budget()
        self.last_purge = {"at_us": self.rt.clock.now_us(), **totals}
        return totals

    def _purge_files(self, referenced: set[str]) -> int:
        removed = 0
        blobs = self.root / "blobs"
        # Only blobs older than the grace period: a blob written after the reference list
        # was read (new snapshot, new graph revision) must not be taken for an orphan.
        young = time.time() - BLOB_GRACE_S
        if blobs.is_dir():
            for path in blobs.glob("*/*"):
                try:
                    if path.stat().st_mtime > young:
                        continue
                except FileNotFoundError:
                    continue
                if path.name.startswith(".tmp-") or f"sha256:{path.name}" not in referenced:
                    path.unlink(missing_ok=True)
                    removed += 1
        if self.exports_dir.is_dir():
            cutoff = time.time() - EXPORT_TTL_US / 1_000_000
            for path in self.exports_dir.iterdir():
                if path.stat().st_mtime < cutoff or path.name.startswith(".tmp-"):
                    path.unlink(missing_ok=True)
        return removed

    # --- Deletion --------------------------------------------------------------------------------

    async def delete_scope(self, scope: str, actor_id: str) -> dict[str, Any]:
        if scope == "all":
            # Not an entry task: unloading waits for entry tasks, which would deadlock.
            self.hass.async_create_task(self._delete_all(), f"{DOMAIN}_wipe")
            return {"scope": "all", "scheduled": True}
        now = self.rt.clock.now_us()
        counts = await self.rt.db.run(
            lambda c: jobs.delete_scope(c, scope, actor_id, new_uuid(), now))
        if scope == "config_history":
            refs = await self.rt.db.run(jobs.referenced_blobs)
            await self.hass.async_add_executor_job(self._purge_files, refs)
            if self.rt.config is not None:
                await self.rt.config.async_rebuild_graph()
        if scope == "incidents":
            # Failures that are still active are facts; they are re-derived immediately.
            await self.rt.async_correlate()
        self.rt.bump()
        return {"scope": scope, "deleted": counts}

    async def _delete_all(self) -> None:
        """Stop ingestion and close the DB by unloading, remove Sentinel files, start fresh."""
        entry_id = self.rt.entry.entry_id
        hass = self.hass
        _LOGGER.warning("Deleting all HA Sentinel data on admin request")
        await hass.config_entries.async_unload(entry_id)
        await hass.async_add_executor_job(_remove_sentinel_files, self.root)
        await hass.config_entries.async_setup(entry_id)


def _remove_sentinel_files(root: Path) -> None:
    if not root.is_dir():
        return
    for name in ("blobs", "exports"):
        shutil.rmtree(root / name, ignore_errors=True)
    for path in root.glob("sentinel*.db*"):
        path.unlink(missing_ok=True)
