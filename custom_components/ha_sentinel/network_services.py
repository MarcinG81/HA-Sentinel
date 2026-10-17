"""Network Discovery: N0 inventory and opt-in N1 probes (spec 13).

N0 (default) reads only what HA already knows and starts no probe (AC NET 04). N1 probes
only admin-approved targets; periodic and manual scans share one limiter (AC NET 05).
Host reachability and HA entity availability stay separate states (spec 13.2).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later

from .adapters.probe import probe
from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_NETWORK_ACTIVE_PROBES,
    OPT_NETWORK_ALLOWLIST,
    OPT_NETWORK_INTERVAL_S,
)
from .core.errors import ErrorCode, SentinelError
from .core.models import (
    CapabilityStatus,
    CoverageStatus,
    Finding,
    FindingStatus,
    JobState,
    Severity,
    new_uuid,
)
from .modules.correlation.correlator import CandidateEvidence
from .modules.dependency_mapper.graph import Edge, Node
from .modules.network.scheduler import (
    FAILURES_TO_UNREACHABLE,
    Outcome,
    ProbeBook,
    ProbeLimiter,
    Status,
    TargetState,
)
from .modules.network.targets import (
    TargetRejected,
    TargetSpec,
    check_address,
    parse_allowlist,
    validate_spec,
)
from .network_hosts import HostService
from .repositories import jobs
from .repositories import repository as repo
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

_LOGGER = logging.getLogger(__name__)
MAX_TARGETS = 64


class NetworkService:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass: HomeAssistant = runtime.hass
        opts = {**DEFAULT_OPTIONS, **runtime.entry.options}
        self.active = bool(opts[OPT_NETWORK_ACTIVE_PROBES])
        self.interval_s = max(60, int(opts[OPT_NETWORK_INTERVAL_S]))
        self.allowlist = parse_allowlist(str(opts[OPT_NETWORK_ALLOWLIST]))
        self.limiter = ProbeLimiter()
        self.book = ProbeBook()
        self.targets: dict[str, dict[str, Any]] = {}
        self.findings: dict[str, Finding] = {}
        self.probes_started = 0  # observable for AC NET 04
        self._timer: CALLBACK_TYPE | None = None
        self._stopping = False
        self.hosts = HostService(self)

    @property
    def capability(self) -> tuple[CapabilityStatus, str | None]:
        if not self.active:
            return CapabilityStatus.DISABLED, "n0_only"
        return CapabilityStatus.SUPPORTED, None

    async def async_start(self) -> None:
        await self._load()
        await self.hosts.async_load()
        if self.active:
            self._schedule()

    async def async_stop(self) -> None:
        self._stopping = True
        if self._timer is not None:
            self._timer()
            self._timer = None

    async def _load(self) -> None:
        rows = await self.rt.db.run(lambda c: [dict(r) for r in c.execute(
            "SELECT * FROM network_targets ORDER BY label, id")])
        self.targets = {r["id"]: r for r in rows}

    # --- Scheduling -------------------------------------------------------------------

    @callback
    def _schedule(self) -> None:
        if self._stopping:
            return
        delay = self.interval_s * random.uniform(0.9, 1.1)  # noqa: S311 - jitter, not crypto

        @callback
        def fire(_now) -> None:
            self._timer = None
            self.rt.entry.async_create_task(self.hass, self._cycle(), f"{DOMAIN}_probes")

        self._timer = async_call_later(self.hass, delay, fire)

    async def _cycle(self) -> None:
        try:
            await self.run_probes(manual=False)
        finally:
            self._schedule()

    async def run_probes(self, *, manual: bool, target_ids: list[str] | None = None
                         ) -> dict[str, int]:
        if not self.active:
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "active probes are disabled",
                                reason="n0_only")
        now_ns = time.monotonic_ns()
        chosen = [t for tid, t in self.targets.items()
                  if t["enabled"] and (target_ids is None or tid in target_ids)]
        started: list[tuple[dict[str, Any], asyncio.Task]] = []
        skipped = 0
        for target in chosen:
            if not self.book.due(target["id"], now_ns) or not self.limiter.try_acquire(now_ns):
                skipped += 1  # rate limited: shared by manual and periodic scans
                continue
            self.probes_started += 1
            started.append((target, asyncio.ensure_future(self._probe_one(target))))
        for _, task in started:
            await task
        if started:
            await self.rt.async_correlate()
            self.rt.bump()
        return {"probed": len(started), "skipped": skipped, "manual": int(manual)}

    async def _probe_one(self, target: dict[str, Any]) -> None:
        spec = TargetSpec(target["host"], target["port"], target["kind"], target["path"],
                          bool(target["allow_loopback"]))
        try:
            result = await probe(spec, self.allowlist)
        finally:
            self.limiter.release()
        now_us = self.rt.clock.now_us()
        transition = self.book.record(target["id"], result.outcome, time.monotonic_ns(), now_us)
        tid = target["id"]
        await self.rt.db.run(lambda c: _insert_result(c, tid, now_us, result))
        uid = f"endpoint:{tid}"
        if transition.became_unreachable:
            f = Finding(id=new_uuid(), rule_id="NET001", rule_version=1, subject_uid=uid,
                        status=FindingStatus.ACTIVE, severity=Severity.WARNING,
                        first_seen_us=self.book.targets[tid].unreachable_since_us or now_us,
                        last_seen_us=now_us, coverage=CoverageStatus.COMPLETE,
                        details={"outcome": str(result.outcome)})
            self.findings[tid] = f
            await self.rt.db.run(lambda c: repo.upsert_finding(c, f, now_us))
        elif transition.recovered and tid in self.findings:
            f = self.findings.pop(tid)
            f.status = FindingStatus.RESOLVED
            f.resolved_at_us = f.last_seen_us = now_us
            await self.rt.db.run(lambda c: repo.upsert_finding(c, f, now_us))

    def restore(self, findings: list[Finding]) -> list[Finding]:
        """Take over restored NET001 findings of approved targets that are still probed.

        The book starts in the unreachable state so a recovery closes the finding and a
        persisting failure is not announced again as new.
        """
        rest = []
        for f in findings:
            tid = f.subject_uid.removeprefix("endpoint:")
            if f.rule_id == "NET001" and self.active and tid in self.targets:
                self.findings[tid] = f
                self.book.targets[tid] = TargetState(
                    status=Status.UNREACHABLE, failures=FAILURES_TO_UNREACHABLE,
                    unreachable_since_us=f.first_seen_us)
            else:
                rest.append(f)
        return rest

    # --- Read model for correlation and graph ---------------------------------------

    def active_findings(self) -> list[Finding]:
        return list(self.findings.values())

    def evidence(self) -> dict[str, CandidateEvidence]:
        """An unreachable approved endpoint is explicit evidence about it (E=1)."""
        out = {}
        for tid, st in self.book.targets.items():
            if st.status is Status.UNREACHABLE:
                out[f"endpoint:{tid}"] = CandidateEvidence(1.0, st.unreachable_since_us,
                                                           "probe_unreachable")
        return out

    def graph_elements(self) -> tuple[list[Node], list[Edge]]:
        nodes, edges = [], []
        for tid, t in self.targets.items():
            uid = f"endpoint:{tid}"
            nodes.append(Node(uid, "service_endpoint", t["label"],
                              f"{t['kind']}://{t['host']}:{t['port']}"))
            if t["linked_device_id"]:
                # Manual mapping: the device depends on this endpoint (provenance manual).
                edges.append(Edge(f"device:{t['linked_device_id']}", uid, "depends_on",
                                  "manual", 1.0, f"network_target:{tid}"))
        host_nodes, host_edges = self.hosts.graph_elements()
        return nodes + host_nodes, edges + host_edges

    # --- Targets (adding a target is the admin's approval) ---------------------------

    def target_api(self, t: dict[str, Any]) -> dict[str, Any]:
        st = self.book.targets.get(t["id"])
        return {
            "id": t["id"], "label": t["label"], "host": t["host"], "port": t["port"],
            "kind": t["kind"], "path": t["path"], "allow_loopback": bool(t["allow_loopback"]),
            "linked_device_id": t["linked_device_id"], "enabled": bool(t["enabled"]),
            "host_id": t.get("host_id"),
            "status": str(st.status) if st else str(Status.UNKNOWN),
            "last_outcome": str(st.last_outcome) if st and st.last_outcome else None,
            "failures": st.failures if st else 0,
        }

    async def add_target(self, *, label: str, host: str, port: int, kind: str, path: str,
                         allow_loopback: bool, linked_device_id: str | None,
                         actor: str) -> dict[str, Any]:
        if len(self.targets) >= MAX_TARGETS:
            raise SentinelError(ErrorCode.RATE_LIMITED, "too many targets")
        try:
            spec = validate_spec(TargetSpec(host, port, kind, path, allow_loopback))
            # IP literals are checked against the policy now; names are re-checked after
            # every DNS resolution because the answer can change (rebinding).
            try:
                ipaddress.ip_address(spec.host)
            except ValueError:
                pass
            else:
                check_address(spec.host, self.allowlist, spec.allow_loopback)
        except TargetRejected as err:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "target rejected",
                                reason=err.reason) from err
        if linked_device_id and dr.async_get(self.hass).async_get(linked_device_id) is None:
            raise SentinelError(ErrorCode.INVALID_REQUEST, "unknown device")
        tid = new_uuid()
        now = self.rt.clock.now_us()

        def insert(conn):
            with _tx(conn):
                try:
                    conn.execute(
                        "INSERT INTO network_targets(id, label, host, port, kind, path, "
                        "allow_loopback, linked_device_id, enabled, approved_by, approved_us) "
                        "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                        (tid, label, spec.host, spec.port, spec.kind, spec.path,
                         int(spec.allow_loopback), linked_device_id, actor, now))
                except sqlite3.IntegrityError as err:
                    raise SentinelError(ErrorCode.CONFLICT, "target already exists") from err
                insert_audit(conn, actor, tid, "network.target.approve", tid, "ok", now)

        await self.rt.db.run(insert)
        await self._load()
        if self.rt.config is not None:
            self.rt.config.schedule_graph_rebuild()
        return self.target_api(self.targets[tid])

    async def update_target(self, tid: str, *, enabled: bool | None,
                            linked_device_id: str | None, clear_link: bool,
                            actor: str, host_id: str | None = None,
                            clear_host: bool = False) -> dict[str, Any]:
        if tid not in self.targets:
            raise SentinelError(ErrorCode.NOT_FOUND, "target not found")
        if host_id is not None and host_id not in self.hosts.hosts:
            raise SentinelError(ErrorCode.NOT_FOUND, "host not found")
        now = self.rt.clock.now_us()
        link = None if clear_link else (linked_device_id or self.targets[tid]["linked_device_id"])
        host = None if clear_host else (host_id or self.targets[tid].get("host_id"))

        def update(conn):
            with _tx(conn):
                conn.execute("UPDATE network_targets SET enabled=COALESCE(?, enabled), "
                             "linked_device_id=?, host_id=? WHERE id=?",
                             (None if enabled is None else int(enabled), link, host, tid))
                insert_audit(conn, actor, tid, "network.target.update", tid, "ok", now)

        await self.rt.db.run(update)
        await self._load()
        if self.rt.config is not None:
            self.rt.config.schedule_graph_rebuild()
        return self.target_api(self.targets[tid])

    async def delete_target(self, tid: str, actor: str) -> None:
        now = self.rt.clock.now_us()

        def delete(conn):
            with _tx(conn):
                if conn.execute("DELETE FROM network_targets WHERE id=?", (tid,)).rowcount == 0:
                    raise SentinelError(ErrorCode.NOT_FOUND, "target not found")
                insert_audit(conn, actor, tid, "network.target.delete", tid, "ok", now)

        await self.rt.db.run(delete)
        self.book.targets.pop(tid, None)
        if (f := self.findings.pop(tid, None)) is not None:
            f.status = FindingStatus.RESOLVED
            f.resolved_at_us = now
            await self.rt.db.run(lambda c: repo.upsert_finding(c, f, now))
        await self._load()
        if self.rt.config is not None:
            self.rt.config.schedule_graph_rebuild()

    # --- Manual scan job --------------------------------------------------------------

    async def start_scan(self, request_key: str, request_hash: str, actor: str,
                         target_ids: list[str] | None) -> dict[str, Any]:
        self.rt.require_degradation_below(2)
        if not self.active:
            raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "active probes are disabled",
                                reason="n0_only")
        now = self.rt.clock.now_us()
        job_id, created = await self.rt.db.run(lambda c: jobs.create_job(
            c, kind="network_scan", request_key=request_key, request_hash=request_hash,
            actor_id=actor, now_us=now))
        if created:
            self.rt.entry.async_create_task(self.hass, self._scan(job_id, target_ids),
                                            f"{DOMAIN}_scan")
        return {"job_id": job_id, "state": "queued" if created else "existing"}

    async def _scan(self, job_id: str, target_ids: list[str] | None) -> None:
        clock = self.rt.clock
        await self.rt.db.run(lambda c: jobs.update_job(c, job_id, state=JobState.RUNNING,
                                                       now_us=clock.now_us()))
        try:
            result = await self.run_probes(manual=True, target_ids=target_ids)
            await self.rt.db.run(lambda c: jobs.update_job(
                c, job_id, state=JobState.SUCCEEDED, progress=1.0, result=result,
                now_us=clock.now_us()))
        except Exception:
            _LOGGER.exception("Sentinel network scan failed")
            await self.rt.db.run(lambda c: jobs.update_job(
                c, job_id, state=JobState.FAILED, error_code="internal_error",
                now_us=clock.now_us()))

    # --- N0 inventory -----------------------------------------------------------------

    @callback
    def inventory(self) -> list[dict[str, Any]]:
        """Devices HA already knows with network identities. No probing (AC NET 04).

        MAC addresses are shown truncated; IP is never a durable identity (spec 13.2).
        """
        out = []
        for dev in dr.async_get(self.hass).devices.values():
            macs = [v for t, v in dev.connections if t == dr.CONNECTION_NETWORK_MAC]
            url = dev.configuration_url
            if not macs and not url:
                continue
            out.append({
                "device_id": dev.id,
                "name": dev.name_by_user or dev.name,
                "manufacturer": dev.manufacturer,
                "mac_hint": [m[:8] + ":…" for m in macs],
                "has_configuration_url": bool(url),
                "linked_targets": [tid for tid, t in self.targets.items()
                                   if t["linked_device_id"] == dev.id],
            })
        out.sort(key=lambda d: (d["name"] or "").lower())
        return out


def _insert_result(conn, tid: str, now_us: int, result) -> None:
    with _tx(conn):
        conn.execute(
            "INSERT INTO probe_results(target_id, at_us, outcome, latency_ms, detail) "
            "VALUES (?,?,?,?,?)", (tid, now_us, str(result.outcome), result.latency_ms,
                                   result.detail))


__all__ = ["NetworkService", "Outcome"]
