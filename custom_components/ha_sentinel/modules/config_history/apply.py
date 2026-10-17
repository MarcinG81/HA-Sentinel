"""Journaled apply, rollback and crash recovery for managed files (spec 10.4, 27.3-27.4).

The flow never runs a write twice: every side effect is preceded by a durable journal
record, and recovery decides from the journal phase plus the real file hash, not from the
job status. Reverting a file never reverts physical actions already taken by devices.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .managed import ContentChangedError, FaultHook

PLAN_TTL_S = 10 * 60
CONTRACT_VERSION = 1


class PlanState(StrEnum):
    PREVIEWED = "previewed"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    CONFLICT = "conflict"
    EXPIRED = "expired"
    FAILED = "failed"
    MANUAL_RELOAD_REQUIRED = "manual_reload_required"
    MANUAL_RECOVERY_REQUIRED = "manual_recovery_required"


TERMINAL = frozenset({PlanState.APPLIED, PlanState.ROLLED_BACK, PlanState.CONFLICT,
                      PlanState.EXPIRED, PlanState.FAILED, PlanState.MANUAL_RELOAD_REQUIRED,
                      PlanState.MANUAL_RECOVERY_REQUIRED})


class Phase(StrEnum):
    PREPARED = "prepared"
    FILE_WRITTEN = "file_written"
    RELOADED = "reloaded"
    VERIFIED = "verified"
    ROLLBACK_WRITTEN = "rollback_written"
    DONE = "done"


def plan_hash(*, resource_id: str, base_hash: str, target_hash: str, actor_id: str,
              policy_revision: int, validation: dict[str, Any], expires_at: str) -> str:
    """Covers everything the approval refers to; the state is excluded (spec 27.3)."""
    payload = {
        "resource_id": resource_id, "base_hash": base_hash, "target_hash": target_hash,
        "actor_id": actor_id, "policy_revision": policy_revision,
        "capability_contract": CONTRACT_VERSION, "validation": validation,
        "expires_at": expires_at,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Plan:
    id: str
    resource_id: str
    base_hash: str
    target_hash: str
    target: bytes
    expected_automation_ids: tuple[str, ...]


class JournalPort(Protocol):
    async def prepare(self, plan: Plan, before: bytes, before_hash: str) -> str:
        """Durably record intent + before snapshot + audit. Must raise if not durable."""

    async def record(self, op_id: str, phase: Phase, *, after_hash: str | None = None,
                     error: str | None = None) -> None: ...

    async def finish(self, plan_id: str, state: PlanState, detail: str | None = None) -> None: ...


class FilePort(Protocol):
    async def read(self) -> tuple[bytes, str]: ...

    async def current_hash(self) -> str | None: ...

    async def replace(self, data: bytes, expected_hash: str,
                      fault: FaultHook | None) -> str: ...


class ReloadPort(Protocol):
    def available(self) -> bool: ...

    async def reload(self) -> None: ...

    async def verify(self, expected_ids: tuple[str, ...]) -> bool: ...


@dataclass(slots=True)
class Outcome:
    state: PlanState
    detail: str | None = None
    after_hash: str | None = None


async def run_apply(plan: Plan, *, journal: JournalPort, files: FilePort,
                    reloader: ReloadPort, fault: FaultHook | None = None) -> Outcome:
    hook = fault or (lambda _name: None)
    before, before_hash = await files.read()
    if before_hash != plan.base_hash:
        await journal.finish(plan.id, PlanState.CONFLICT, "changed_since_preview")
        return Outcome(PlanState.CONFLICT, "changed_since_preview")

    op_id = await journal.prepare(plan, before, before_hash)  # durable before side effect
    hook("after_prepared")
    try:
        after_hash = await files.replace(plan.target, plan.base_hash, fault)
    except ContentChangedError:
        await journal.finish(plan.id, PlanState.CONFLICT, "changed_before_replace")
        return Outcome(PlanState.CONFLICT, "changed_before_replace")
    await journal.record(op_id, Phase.FILE_WRITTEN, after_hash=after_hash)
    hook("after_file_written")

    if not reloader.available():
        await journal.finish(plan.id, PlanState.MANUAL_RELOAD_REQUIRED, "reload_unavailable")
        return Outcome(PlanState.MANUAL_RELOAD_REQUIRED, "reload_unavailable", after_hash)

    # Only the reload itself may trigger a rollback; journal errors after a successful
    # reload must never revert a working change.
    reload_error: str | None = None
    try:
        await reloader.reload()
    except Exception as err:  # noqa: BLE001 - any reload failure triggers rollback
        reload_error = f"reload_failed:{type(err).__name__}"
    if reload_error is None:
        await journal.record(op_id, Phase.RELOADED)
        hook("after_reload")
        try:
            verified = await reloader.verify(plan.expected_automation_ids)
        except Exception:  # noqa: BLE001
            verified = False
        if not verified:
            reload_error = "verification_failed"

    if reload_error is None:
        await journal.record(op_id, Phase.VERIFIED)
        hook("before_final_commit")
        await journal.finish(plan.id, PlanState.APPLIED)
        return Outcome(PlanState.APPLIED, after_hash=after_hash)
    return await _rollback(plan, op_id, before, after_hash, reload_error, journal=journal,
                           files=files, reloader=reloader)


async def _rollback(plan: Plan, op_id: str, before: bytes, after_hash: str, reason: str, *,
                    journal: JournalPort, files: FilePort, reloader: ReloadPort) -> Outcome:
    """Revert only if the file still holds exactly what we wrote (spec 10.4)."""
    try:
        reverted = await files.replace(before, after_hash, None)
    except ContentChangedError:
        await journal.record(op_id, Phase.DONE, error=f"{reason};foreign_change")
        await journal.finish(plan.id, PlanState.MANUAL_RECOVERY_REQUIRED,
                             "conflict_manual_recovery")
        return Outcome(PlanState.MANUAL_RECOVERY_REQUIRED, "conflict_manual_recovery")
    await journal.record(op_id, Phase.ROLLBACK_WRITTEN, after_hash=reverted, error=reason)
    try:
        await reloader.reload()
    except Exception:  # noqa: BLE001
        await journal.finish(plan.id, PlanState.MANUAL_RECOVERY_REQUIRED, "rollback_failed")
        return Outcome(PlanState.MANUAL_RECOVERY_REQUIRED, "rollback_failed")
    await journal.finish(plan.id, PlanState.ROLLED_BACK, reason)
    return Outcome(PlanState.ROLLED_BACK, reason, reverted)


def recovery_decision(phase: str, before_hash: str, after_hash: str | None,
                      target_hash: str, actual_hash: str | None) -> tuple[PlanState, str]:
    """Decide the state of an interrupted apply from journal phase and the real file.

    Never re-runs the apply (AC HK 03, T14).
    """
    if phase in (Phase.VERIFIED, Phase.DONE):
        return PlanState.APPLIED, "completed_before_crash"
    if phase == Phase.ROLLBACK_WRITTEN:
        if actual_hash == before_hash:
            return PlanState.MANUAL_RELOAD_REQUIRED, "rolled_back_reload_pending"
        return PlanState.MANUAL_RECOVERY_REQUIRED, "unexpected_content_after_rollback"
    if actual_hash == target_hash:
        # The new content is on disk (replace happened, maybe before the journal update).
        # HA reads it at the next reload or restart; we do not reload or apply again here.
        return PlanState.MANUAL_RELOAD_REQUIRED, "file_written_before_crash"
    if actual_hash == before_hash:
        return PlanState.FAILED, "interrupted_before_write"
    return PlanState.MANUAL_RECOVERY_REQUIRED, "unexpected_content"
