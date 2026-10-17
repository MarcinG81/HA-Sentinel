"""Managed file restore: register, preview, apply job, crash recovery (spec 10.3-10.4, 27).

Disabled unless the admin enables managed writes in the options. Sentinel never edits
configuration.yaml or files of the HA editor; the admin adds the include manually.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_MANAGED_DIR,
    OPT_MANAGED_RELOAD,
    OPT_MANAGED_WRITE_ENABLED,
)
from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.models import CapabilityStatus, JobState, dumps_strict, new_uuid
from .core.yaml_ast import parse
from .modules.autopilot.lint import lint
from .modules.autopilot.model import extract
from .modules.config_history.apply import (
    PLAN_TTL_S,
    Phase,
    Plan,
    PlanState,
    plan_hash,
    recovery_decision,
    run_apply,
)
from .modules.config_history.canonical import canonical_hash, semantic_diff, textual_diff
from .modules.config_history.importer import prepare_import
from .modules.config_history.managed import (
    ManagedPathError,
    atomic_replace,
    cleanup_temp_files,
    current_hash,
    read_managed,
    resolve_managed_path,
    sha256_bytes,
)
from .repositories import history, jobs
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

_LOGGER = logging.getLogger(__name__)
S = 1_000_000


class ChangeService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass: HomeAssistant = runtime.hass
        self._locks: dict[str, asyncio.Lock] = {}
        opts = {**DEFAULT_OPTIONS, **runtime.entry.options}
        self.enabled = bool(opts[OPT_MANAGED_WRITE_ENABLED])
        self.managed_dir = str(opts[OPT_MANAGED_DIR])
        self.reload_after_apply = bool(opts[OPT_MANAGED_RELOAD])
        # Options are the policy; any change creates a new revision and invalidates plans.
        digest = hashlib.sha256(json.dumps(opts, sort_keys=True, default=str).encode())
        self.policy_revision = int(digest.hexdigest()[:8], 16) or 1

    @property
    def capability(self) -> tuple[CapabilityStatus, str | None]:
        if not self.enabled:
            return CapabilityStatus.DISABLED, "managed_write_disabled"
        if not self.rt.storage_writable:
            return CapabilityStatus.UNAVAILABLE, "storage_read_only"
        return CapabilityStatus.SUPPORTED, None

    def _require_enabled(self) -> None:
        status, reason = self.capability
        if status is not CapabilityStatus.SUPPORTED:
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "managed writes unavailable",
                                reason=reason)

    async def _resolve(self, relative: str) -> Path:
        try:
            return await self.hass.async_add_executor_job(
                resolve_managed_path, self.hass.config.config_dir, self.managed_dir, relative)
        except ManagedPathError as err:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "managed path rejected",
                                reason=err.reason) from err

    # --- Startup recovery (AC HK 03, T14) -------------------------------------------------------

    async def async_recover(self) -> int:
        if not self.rt.storage_writable:
            return 0
        rows = await self.rt.db.run(lambda c: [dict(r) for r in c.execute(
            "SELECT o.id AS op_id, o.phase, o.before_hash, o.after_hash, p.id AS plan_id, "
            "p.target_hash, p.resource_id FROM operations o JOIN change_plans p "
            "ON p.id = o.plan_id WHERE p.state='applying'")])
        for row in rows:
            res = await self.rt.db.run(lambda c, rid=row["resource_id"]:
                                       history.get_resource(c, rid))
            actual = None
            if res is not None:
                path = Path(self.hass.config.config_dir) / res["locator"]["path"]
                actual = await self.hass.async_add_executor_job(current_hash, path)
                await self.hass.async_add_executor_job(cleanup_temp_files, path)
            state, reason = recovery_decision(row["phase"], row["before_hash"],
                                              row["after_hash"], row["target_hash"], actual)
            now = self.rt.clock.now_us()

            def finish(conn, row=row, state=state, reason=reason, now=now):
                with _tx(conn):
                    conn.execute("UPDATE operations SET phase=?, last_error=?, updated_us=? "
                                 "WHERE id=?", (Phase.DONE, f"recovered:{reason}", now,
                                                row["op_id"]))
                    conn.execute("UPDATE change_plans SET state=? WHERE id=?",
                                 (str(state), row["plan_id"]))
                    insert_audit(conn, None, "recovery", f"apply.recovered.{reason}",
                                 row["resource_id"], str(state), now)

            await self.rt.db.run(finish)
            _LOGGER.warning("Recovered interrupted apply %s: %s", row["plan_id"], reason)
        return len(rows)

    # --- Register and sync ----------------------------------------------------------------

    async def register(self, relative: str, label: str, exclusive: bool, actor: str,
                       request_id: str) -> dict[str, Any]:
        self._require_enabled()
        if not exclusive:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "exclusive ownership not confirmed",
                                reason="ownership_not_confirmed")
        path = await self._resolve(relative)
        raw, raw_hash_ = await self.hass.async_add_executor_job(read_managed, path)
        text = raw.decode("utf-8", errors="strict")
        prepared = await self.hass.async_add_executor_job(
            prepare_import, text, sha256_bytes(raw), "automation", False)
        now = self.rt.clock.now_us()
        existing = await self.rt.db.run(lambda c: history.find_managed(c, relative))
        if existing:
            raise SentinelError(ErrorCode.CONFLICT, "file already managed")
        result = await self.rt.db.run(lambda c: history.save_snapshot_tx(
            c, self.rt.config.blobs, resource_id=None, provider="managed", kind="automation",
            label=label, canonical_tree=prepared.tree, canonical_hash=prepared.canonical_hash,
            raw=raw, raw_hash=raw_hash_, redacted_paths=[], validation=prepared.validation,
            actor_id=actor, request_id=request_id, now_us=now, restorable=True,
            locator={"path": relative, "exclusive_owner_confirmed": True},
            write_policy="managed_write"))
        self.rt.config.schedule_graph_rebuild()
        return result

    async def sync(self, resource_id: str, actor: str | None) -> dict[str, Any]:
        """Snapshot the current file content (captures edits made outside Sentinel)."""
        res = await self._managed_resource(resource_id)
        path = await self._resolve(res["locator"]["path"])
        raw, raw_hash_ = await self.hass.async_add_executor_job(read_managed, path)
        try:
            prepared = await self.hass.async_add_executor_job(
                prepare_import, raw.decode(), sha256_bytes(raw), "automation", False)
            tree, chash, validation = prepared.tree, prepared.canonical_hash, prepared.validation
        except SentinelError as err:
            # A broken current file is still history: keep the raw bytes, mark invalid.
            tree, chash = None, canonical_hash(None)
            validation = {"parser": "invalid", **err.details}
        now = self.rt.clock.now_us()
        return await self.rt.db.run(lambda c: history.save_snapshot_tx(
            c, self.rt.config.blobs, resource_id=resource_id, provider="managed",
            kind="automation", label=res["label"], canonical_tree=tree, canonical_hash=chash,
            raw=raw, raw_hash=raw_hash_, redacted_paths=[], validation=validation,
            actor_id=actor, request_id=new_uuid(), now_us=now, restorable=tree is not None))

    async def _managed_resource(self, resource_id: str) -> dict[str, Any]:
        res = await self.rt.db.run(lambda c: history.get_resource(c, resource_id))
        if res is None:
            raise SentinelError(ErrorCode.NOT_FOUND, "resource not found")
        if res["provider"] != "managed" or res["write_policy"] != "managed_write":
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "resource is export only")
        return res

    # --- Preview ---------------------------------------------------------------------------------

    async def preview(self, resource_id: str, snapshot_id: str, actor: str) -> dict[str, Any]:
        self._require_enabled()
        res = await self._managed_resource(resource_id)
        snap = await self.rt.db.run(lambda c: history.snapshot_raw(c, self.rt.config.blobs,
                                                                   snapshot_id))
        if snap is None or snap["resource_id"] != resource_id:
            raise SentinelError(ErrorCode.NOT_FOUND, "snapshot not found")
        if not snap["restorable"] or snap["raw"] is None:
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "snapshot is not restorable")
        path = await self._resolve(res["locator"]["path"])
        current, base_hash = await self.hass.async_add_executor_job(read_managed, path)
        target = snap["raw"]
        target_hash = sha256_bytes(target)
        if base_hash == target_hash:
            raise SentinelError(ErrorCode.INVALID_REQUEST, "file already has this content",
                                reason="no_change")
        reg = None
        if self.rt.config is not None:
            from .adapters.registry import async_snapshot
            reg = async_snapshot(self.hass, self.rt.inventory)

        def analyse() -> dict[str, Any]:
            target_tree = parse(target).tree
            try:
                current_tree = parse(current).tree
            except Exception:  # noqa: BLE001 - a broken current file can still be replaced
                current_tree = None
            model = extract(target_tree, "automation")
            levels: dict[str, int] = {}
            if reg is not None:
                report = lint(model, entity_status=reg.entity_status,
                              service_exists=reg.service_exists, inventory_complete=True)
                for f in report.findings:
                    levels[f.level] = levels.get(f.level, 0) + 1
            return {
                "diff": {
                    "semantic": [c.as_dict() for c in semantic_diff(current_tree, target_tree)],
                    "textual": textual_diff(current.decode(errors="replace"),
                                            target.decode(errors="replace")),
                },
                "validation": {"parser": "ok", "lint": levels,
                               "completeness": round(model.completeness, 4)},
                "impact": {
                    "automations": sorted(r.label for r in model.runnables),
                    "entities": sorted({ref.value for r in model.runnables for ref in r.refs
                                        if ref.kind == "entity"}),
                },
                "expected_ids": sorted(r.id for r in model.runnables if r.id),
            }

        report = await self.hass.async_add_executor_job(analyse)
        now = self.rt.clock.now_us()
        expires_us = now + PLAN_TTL_S * S
        expires_at = us_to_rfc3339(expires_us)
        phash = plan_hash(resource_id=resource_id, base_hash=base_hash, target_hash=target_hash,
                          actor_id=actor, policy_revision=self.policy_revision,
                          validation=report["validation"], expires_at=expires_at)
        plan_id = new_uuid()
        stored = {**report, "snapshot_id": snapshot_id, "policy_revision": self.policy_revision,
                  "expires_at": expires_at}

        def insert(conn):
            with _tx(conn):
                conn.execute(
                    "INSERT INTO change_plans(id, resource_id, base_hash, target_hash, "
                    "plan_hash, state, actor_id, expires_us, report_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (plan_id, resource_id, base_hash, target_hash, phash,
                     str(PlanState.PREVIEWED), actor, expires_us, dumps_strict(stored)))
                insert_audit(conn, actor, plan_id, "apply.preview", resource_id, "ok", now)

        await self.rt.db.run(insert)
        return {"plan_id": plan_id, "plan_hash": phash, "resource_id": resource_id,
                "base_hash": base_hash, "target_hash": target_hash, "expires_at": expires_at,
                "state": str(PlanState.PREVIEWED), "policy_revision": self.policy_revision,
                "reload": self.reload_after_apply, **report}

    # --- Apply job -------------------------------------------------------------------------------

    async def start_apply(self, plan_id: str, phash: str, request_key: str,
                          request_hash: str, actor: str) -> dict[str, Any]:
        self._require_enabled()
        plan = await self._load_plan(plan_id)
        if plan["plan_hash"] != phash:
            raise SentinelError(ErrorCode.CONFLICT, "plan_hash does not match the preview")
        if plan["actor_id"] != actor:
            raise SentinelError(ErrorCode.FORBIDDEN, "plan was approved by another user")
        if plan["state"] != PlanState.PREVIEWED:
            raise SentinelError(ErrorCode.CONFLICT, f"plan is {plan['state']}")
        if plan["expires_us"] <= self.rt.clock.now_us():
            await self._set_plan_state(plan_id, PlanState.EXPIRED)
            raise SentinelError(ErrorCode.PLAN_EXPIRED, "plan expired; preview again")
        now = self.rt.clock.now_us()
        job_id, created = await self.rt.db.run(lambda c: jobs.create_job(
            c, kind="apply", request_key=request_key, request_hash=request_hash,
            actor_id=actor, now_us=now))
        if created:
            self.rt.entry.async_create_task(self.hass, self._run(job_id, plan_id, actor),
                                            f"{DOMAIN}_apply")
        return {"job_id": job_id, "state": "queued" if created else "existing"}

    async def _load_plan(self, plan_id: str) -> dict[str, Any]:
        row = await self.rt.db.run(lambda c: c.execute(
            "SELECT id, resource_id, base_hash, target_hash, plan_hash, state, actor_id, "
            "expires_us, report_json FROM change_plans WHERE id=?", (plan_id,)).fetchone())
        if row is None:
            raise SentinelError(ErrorCode.NOT_FOUND, "plan not found")
        out = dict(row)
        out["report"] = json.loads(out.pop("report_json"))
        return out

    async def _set_plan_state(self, plan_id: str, state: PlanState,
                              detail: str | None = None) -> None:
        now = self.rt.clock.now_us()

        def update(conn):
            with _tx(conn):
                row = conn.execute("SELECT report_json, resource_id, actor_id FROM change_plans "
                                   "WHERE id=?", (plan_id,)).fetchone()
                report = json.loads(row["report_json"])
                report["result_detail"] = detail
                conn.execute("UPDATE change_plans SET state=?, report_json=? WHERE id=?",
                             (str(state), dumps_strict(report), plan_id))
                insert_audit(conn, row["actor_id"], plan_id, f"apply.{state}",
                             row["resource_id"], detail or "ok", now)

        await self.rt.db.run(update)

    async def _run(self, job_id: str, plan_id: str, actor: str) -> None:
        db, clock = self.rt.db, self.rt.clock
        await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.RUNNING, progress=0.1,
                                               now_us=clock.now_us()))
        try:
            outcome = await self._execute(plan_id, actor)
            job_state = JobState.SUCCEEDED if outcome[0] in (
                PlanState.APPLIED, PlanState.ROLLED_BACK, PlanState.CONFLICT,
                PlanState.MANUAL_RELOAD_REQUIRED, PlanState.MANUAL_RECOVERY_REQUIRED,
                PlanState.EXPIRED) else JobState.FAILED
            await db.run(lambda c: jobs.update_job(
                c, job_id, state=job_state, progress=1.0,
                result={"plan_id": plan_id, "plan_state": str(outcome[0]),
                        "detail": outcome[1]}, now_us=clock.now_us()))
        except SentinelError as err:
            code = str(err.code)
            await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.FAILED,
                                                   error_code=code, now_us=clock.now_us()))
        except Exception:
            _LOGGER.exception("Sentinel apply failed")
            await db.run(lambda c: jobs.update_job(c, job_id, state=JobState.FAILED,
                                                   error_code="internal_error",
                                                   now_us=clock.now_us()))
        if self.rt.config is not None:
            self.rt.config.schedule_graph_rebuild()
        self.rt.bump()

    async def _execute(self, plan_id: str, actor: str) -> tuple[PlanState, str | None]:
        # Re-check everything right before the side effect (spec 16.2, 27.4).
        user = await self.hass.auth.async_get_user(actor)
        if user is None or not user.is_active or not user.is_admin:
            await self._set_plan_state(plan_id, PlanState.FAILED, "actor_no_longer_admin")
            raise SentinelError(ErrorCode.FORBIDDEN, "actor is no longer an active admin")
        self._require_enabled()
        plan = await self._load_plan(plan_id)
        if plan["state"] != PlanState.PREVIEWED:
            raise SentinelError(ErrorCode.CONFLICT, f"plan is {plan['state']}")
        if plan["expires_us"] <= self.rt.clock.now_us():
            await self._set_plan_state(plan_id, PlanState.EXPIRED)
            return PlanState.EXPIRED, "plan_expired"
        if plan["report"]["policy_revision"] != self.policy_revision:
            await self._set_plan_state(plan_id, PlanState.EXPIRED, "policy_changed")
            return PlanState.EXPIRED, "policy_changed"
        res = await self._managed_resource(plan["resource_id"])
        path = await self._resolve(res["locator"]["path"])
        snap = await self.rt.db.run(lambda c: history.snapshot_raw(
            c, self.rt.config.blobs, plan["report"]["snapshot_id"]))
        if snap is None or snap["raw"] is None or sha256_bytes(snap["raw"]) != plan["target_hash"]:
            await self._set_plan_state(plan_id, PlanState.FAILED, "target_snapshot_missing")
            return PlanState.FAILED, "target_snapshot_missing"

        lock = self._locks.setdefault(plan["resource_id"], asyncio.Lock())
        async with lock:
            await self._set_plan_state(plan_id, PlanState.APPLYING)
            outcome = await run_apply(
                Plan(plan_id, plan["resource_id"], plan["base_hash"], plan["target_hash"],
                     snap["raw"], tuple(plan["report"].get("expected_ids", []))),
                journal=_Journal(self, res, actor),
                files=_Files(self.hass, path),
                reloader=_Reload(self.hass, self.reload_after_apply),
            )
        if outcome.state in (PlanState.APPLIED, PlanState.ROLLED_BACK,
                             PlanState.MANUAL_RELOAD_REQUIRED,
                             PlanState.MANUAL_RECOVERY_REQUIRED):
            await self.sync(plan["resource_id"], actor)  # record what is now on disk
        return outcome.state, outcome.detail


class _Journal:
    def __init__(self, svc: ChangeService, resource: dict[str, Any], actor: str) -> None:
        self.svc, self.resource, self.actor = svc, resource, actor

    async def prepare(self, plan: Plan, before: bytes, before_hash: str) -> str:
        svc = self.svc
        op_id = new_uuid()
        now = svc.rt.clock.now_us()
        prepared = await svc.hass.async_add_executor_job(
            _tree_for_snapshot, before)

        def write(conn):
            # One transaction: before snapshot + journal + audit. If any part fails (disk
            # full, locked DB) nothing is written to the file (T13).
            with _tx(conn):
                history.save_snapshot(
                    conn, svc.rt.config.blobs, resource_id=plan.resource_id,
                    provider="managed", kind="automation", label=self.resource["label"],
                    canonical_tree=prepared[0], canonical_hash=prepared[1], raw=before,
                    raw_hash=before_hash, redacted_paths=[], validation={"parser": prepared[2]},
                    actor_id=self.actor, request_id=plan.id, now_us=now,
                    restorable=prepared[0] is not None)
                conn.execute(
                    "INSERT INTO operations(id, plan_id, phase, before_hash, updated_us) "
                    "VALUES (?,?,?,?,?)", (op_id, plan.id, str(Phase.PREPARED), before_hash, now))
                insert_audit(conn, self.actor, plan.id, "apply.prepared", plan.resource_id,
                             "ok", now)

        await svc.rt.db.run(write)
        return op_id

    async def record(self, op_id: str, phase: Phase, *, after_hash: str | None = None,
                     error: str | None = None) -> None:
        now = self.svc.rt.clock.now_us()
        await self.svc.rt.db.run(lambda c: _update_op(c, op_id, phase, after_hash, error, now))

    async def finish(self, plan_id: str, state: PlanState, detail: str | None = None) -> None:
        await self.svc._set_plan_state(plan_id, state, detail)


def _update_op(conn, op_id, phase, after_hash, error, now) -> None:
    with _tx(conn):
        conn.execute(
            "UPDATE operations SET phase=?, after_hash=COALESCE(?, after_hash), "
            "last_error=COALESCE(?, last_error), updated_us=? WHERE id=?",
            (str(phase), after_hash, error, now, op_id))


def _tree_for_snapshot(raw: bytes) -> tuple[Any, str, str]:
    try:
        tree = parse(raw).tree
        return tree, canonical_hash(tree), "ok"
    except Exception:  # noqa: BLE001 - keep the bytes even when the YAML is broken
        return None, canonical_hash(None), "invalid"


class _Files:
    def __init__(self, hass: HomeAssistant, path: Path) -> None:
        self.hass, self.path = hass, path

    async def read(self) -> tuple[bytes, str]:
        return await self.hass.async_add_executor_job(read_managed, self.path)

    async def current_hash(self) -> str | None:
        return await self.hass.async_add_executor_job(current_hash, self.path)

    async def replace(self, data: bytes, expected_hash: str, fault) -> str:
        return await self.hass.async_add_executor_job(atomic_replace, self.path, data,
                                                      expected_hash, fault)


class _Reload:
    """automation.reload, then check that every expected automation id is visible.

    Visibility does not prove semantic correctness (spec 10.4)."""

    def __init__(self, hass: HomeAssistant, enabled: bool) -> None:
        self.hass, self.enabled = hass, enabled

    def available(self) -> bool:
        return self.enabled and self.hass.services.has_service("automation", "reload")

    async def reload(self) -> None:
        await self.hass.services.async_call("automation", "reload", blocking=True)

    async def verify(self, expected_ids: tuple[str, ...]) -> bool:
        if not expected_ids:
            return True
        visible = {s.attributes.get("id") for s in self.hass.states.async_all("automation")}
        return set(expected_ids) <= visible
