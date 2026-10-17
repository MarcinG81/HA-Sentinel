"""Managed file safety: path policy, T12, T13, T14 fault injection, T15 rollback."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from custom_components.ha_sentinel.modules.config_history.apply import (
    Phase,
    Plan,
    PlanState,
    recovery_decision,
    run_apply,
)
from custom_components.ha_sentinel.modules.config_history.managed import (
    ManagedPathError,
    atomic_replace,
    cleanup_temp_files,
    current_hash,
    read_managed,
    resolve_managed_path,
    sha256_bytes,
)

BEFORE = b"- id: a\n  alias: Old\n  triggers: []\n  actions: []\n"
TARGET = b"- id: a\n  alias: New\n  triggers: []\n  actions: []\n"


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    managed = tmp_path / "ha_sentinel_managed"
    managed.mkdir()
    f = managed / "automations.yaml"
    f.write_bytes(BEFORE)
    os.chmod(f, 0o644)
    return tmp_path


# --- Path policy (spec 16.1: traversal, symlinks) ----------------------------------------


def test_valid_path(cfg):
    p = resolve_managed_path(str(cfg), "ha_sentinel_managed",
                             "ha_sentinel_managed/automations.yaml")
    assert p.name == "automations.yaml"


@pytest.mark.parametrize(("rel", "reason"), [
    ("/etc/passwd", "not_relative"),
    ("ha_sentinel_managed/../configuration.yaml", "traversal"),
    ("ha_sentinel_managed/notes.txt", "not_yaml"),
    ("automations.yaml", "missing"),
])
def test_path_rejections(cfg, rel, reason):
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed", rel)
    assert err.value.reason == reason


def test_outside_allowlist_and_symlinks(cfg):
    (cfg / "automations.yaml").write_bytes(BEFORE)
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed", "automations.yaml")
    assert err.value.reason == "outside_allowlist"
    link = cfg / "ha_sentinel_managed" / "link.yaml"
    link.symlink_to(cfg / "automations.yaml")
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed", "ha_sentinel_managed/link.yaml")
    assert err.value.reason == "symlink"
    # A symlinked parent directory is rejected too (symlink swap).
    (cfg / "other").mkdir()
    (cfg / "other" / "x.yaml").write_bytes(BEFORE)
    (cfg / "ha_sentinel_managed" / "sub").symlink_to(cfg / "other")
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed", "ha_sentinel_managed/sub/x.yaml")
    assert err.value.reason == "symlink"


def test_world_writable_rejected(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    os.chmod(f, 0o666)
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed",
                             "ha_sentinel_managed/automations.yaml")
    assert err.value.reason == "group_or_world_writable"


def test_atomic_replace_keeps_mode_and_checks_hash(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    new_hash = atomic_replace(f, TARGET, sha256_bytes(BEFORE))
    assert f.read_bytes() == TARGET and new_hash == sha256_bytes(TARGET)
    assert oct(os.stat(f).st_mode & 0o777) == "0o644"
    from custom_components.ha_sentinel.modules.config_history.managed import ContentChangedError

    with pytest.raises(ContentChangedError):
        atomic_replace(f, BEFORE, sha256_bytes(BEFORE))  # stale expectation: no write
    assert f.read_bytes() == TARGET


# --- Apply engine with fakes ------------------------------------------------------------------


class Crash(Exception):
    pass


class FakeJournal:
    def __init__(self, fail_prepare: bool = False) -> None:
        self.phases: list[str] = []
        self.final: tuple[PlanState, str | None] | None = None
        self.before_hash: str | None = None
        self.after_hash: str | None = None
        self.fail_prepare = fail_prepare

    async def prepare(self, plan, before, before_hash):
        if self.fail_prepare:
            raise OSError("database or disk is full")
        self.phases.append(Phase.PREPARED)
        self.before_hash = before_hash
        return "op1"

    async def record(self, op_id, phase, *, after_hash=None, error=None):
        self.phases.append(phase)
        if after_hash:
            self.after_hash = after_hash

    async def finish(self, plan_id, state, detail=None):
        self.final = (state, detail)

    @property
    def phase(self) -> str:
        return self.phases[-1]


class RealFiles:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def read(self):
        return read_managed(self.path)

    async def current_hash(self):
        return current_hash(self.path)

    async def replace(self, data, expected_hash, fault):
        return atomic_replace(self.path, data, expected_hash, fault)


class FakeReload:
    def __init__(self, ok=True, available=True, fail_second=False, verify=True):
        self.ok, self._available = ok, available
        self.fail_second, self._verify = fail_second, verify
        self.calls = 0

    def available(self):
        return self._available

    async def reload(self):
        self.calls += 1
        if not self.ok and (self.calls == 1 or self.fail_second):
            raise RuntimeError("invalid config")

    async def verify(self, ids):
        return self._verify


def _plan(path: Path) -> Plan:
    return Plan("p1", "r1", sha256_bytes(BEFORE), sha256_bytes(TARGET), TARGET, ("a",))


def _run(coro):
    return asyncio.run(coro)


def test_apply_happy_path(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    j = FakeJournal()
    out = _run(run_apply(_plan(f), journal=j, files=RealFiles(f), reloader=FakeReload()))
    assert out.state is PlanState.APPLIED
    assert j.phases == [Phase.PREPARED, Phase.FILE_WRITTEN, Phase.RELOADED, Phase.VERIFIED]
    assert f.read_bytes() == TARGET


def test_t12_file_changed_after_preview_is_conflict_without_write(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    f.write_bytes(BEFORE + b"# edited elsewhere\n")
    j = FakeJournal()
    out = _run(run_apply(_plan(f), journal=j, files=RealFiles(f), reloader=FakeReload()))
    assert out.state is PlanState.CONFLICT and j.phases == []
    assert f.read_bytes().endswith(b"# edited elsewhere\n")


def test_t12_change_between_prepare_and_replace_is_conflict(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"

    def editor_writes(name):
        if name == "after_fsync":
            f.write_bytes(b"# concurrent editor\n")

    out = _run(run_apply(_plan(f), journal=FakeJournal(), files=RealFiles(f),
                         reloader=FakeReload(), fault=editor_writes))
    assert out.state is PlanState.CONFLICT
    assert f.read_bytes() == b"# concurrent editor\n"  # never overwritten
    assert cleanup_temp_files(f) == 0  # the temp file was removed


def test_t13_no_durable_journal_blocks_apply(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    with pytest.raises(OSError):
        _run(run_apply(_plan(f), journal=FakeJournal(fail_prepare=True), files=RealFiles(f),
                       reloader=FakeReload()))
    assert f.read_bytes() == BEFORE


@pytest.mark.parametrize("crash_at", [
    "after_prepared", "after_temp_write", "after_fsync", "before_replace", "after_replace",
    "after_dir_fsync", "after_file_written", "after_reload", "before_final_commit",
])
def test_t14_crash_at_every_step_recovers_without_double_effect(cfg, crash_at):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    j = FakeJournal()
    reload = FakeReload()

    def crash(name):
        if name == crash_at:
            raise Crash(name)

    with pytest.raises(Crash):
        _run(run_apply(_plan(f), journal=j, files=RealFiles(f), reloader=reload, fault=crash))
    cleanup_temp_files(f)
    actual = current_hash(f)
    # The file is always either fully old or fully new, never partial.
    assert actual in (sha256_bytes(BEFORE), sha256_bytes(TARGET))
    state, reason = recovery_decision(j.phase, sha256_bytes(BEFORE), j.after_hash,
                                      sha256_bytes(TARGET), actual)
    if actual == sha256_bytes(BEFORE):
        assert state is PlanState.FAILED and reason == "interrupted_before_write"
    elif j.phase == Phase.VERIFIED:
        assert state is PlanState.APPLIED
    else:
        assert state is PlanState.MANUAL_RELOAD_REQUIRED
    # Recovery is a decision only: no second write, no extra reload.
    assert current_hash(f) == actual
    assert reload.calls == (1 if crash_at in ("after_reload", "before_final_commit") else 0)


def test_t15_reload_failure_rolls_back(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    j = FakeJournal()
    out = _run(run_apply(_plan(f), journal=j, files=RealFiles(f),
                         reloader=FakeReload(ok=False)))
    assert out.state is PlanState.ROLLED_BACK and out.detail.startswith("reload_failed")
    assert f.read_bytes() == BEFORE


def test_t15_verification_failure_rolls_back(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    out = _run(run_apply(_plan(f), journal=FakeJournal(), files=RealFiles(f),
                         reloader=FakeReload(verify=False)))
    assert out.state is PlanState.ROLLED_BACK and out.detail == "verification_failed"


def test_t15_rollback_never_overwrites_foreign_change(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"

    class EditDuringReload(FakeReload):
        async def reload(self):
            f.write_bytes(b"# admin fixed it by hand\n")
            raise RuntimeError("invalid config")

    out = _run(run_apply(_plan(f), journal=FakeJournal(), files=RealFiles(f),
                         reloader=EditDuringReload()))
    assert out.state is PlanState.MANUAL_RECOVERY_REQUIRED
    assert out.detail == "conflict_manual_recovery"
    assert f.read_bytes() == b"# admin fixed it by hand\n"  # both versions kept: snapshot + file


def test_t15_rollback_reload_failure(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    out = _run(run_apply(_plan(f), journal=FakeJournal(), files=RealFiles(f),
                         reloader=FakeReload(ok=False, fail_second=True)))
    assert out.state is PlanState.MANUAL_RECOVERY_REQUIRED and out.detail == "rollback_failed"
    assert f.read_bytes() == BEFORE


def test_no_reload_service_means_manual_reload(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    out = _run(run_apply(_plan(f), journal=FakeJournal(), files=RealFiles(f),
                         reloader=FakeReload(available=False)))
    assert out.state is PlanState.MANUAL_RELOAD_REQUIRED
    assert f.read_bytes() == TARGET


def test_more_path_rejections(cfg):
    managed = cfg / "ha_sentinel_managed"
    (managed / "dir.yaml").mkdir()
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed", "ha_sentinel_managed/dir.yaml")
    assert err.value.reason == "not_regular_file"
    big = managed / "big.yaml"
    big.write_bytes(b"#" * (1024 * 1024 + 1))
    os.chmod(big, 0o644)
    with pytest.raises(ManagedPathError) as err:
        resolve_managed_path(str(cfg), "ha_sentinel_managed", "ha_sentinel_managed/big.yaml")
    assert err.value.reason == "too_large"
    for rel, reason in (("", "not_relative"), ("ha_sentinel_managed/a\x00.yaml", "not_relative"),
                        ("C:/x.yaml", "traversal")):
        with pytest.raises(ManagedPathError) as err:
            resolve_managed_path(str(cfg), "ha_sentinel_managed", rel)
        assert err.value.reason == reason


def test_cleanup_removes_crash_leftovers(cfg):
    f = cfg / "ha_sentinel_managed" / "automations.yaml"
    (f.parent / ".automations.yaml.sentinel-abc").write_bytes(b"partial")
    assert cleanup_temp_files(f) == 1
    assert current_hash(cfg / "nope.yaml") is None
