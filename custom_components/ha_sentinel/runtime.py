"""Sentinel runtime: lifecycle, ingestion wiring, writer and Doctor timers (spec 4.1).

Start sequence: open DB and migrate -> load inventory and active findings -> subscribe
and take the state snapshot in one synchronous block of the HA loop -> start writer.
Callbacks only build small DTOs and enqueue them; all SQLite work runs on the DB worker.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval

from .adapters.evidence import EvidenceCollector
from .adapters.ha_states import (
    SOURCE_ID,
    availability_of,
    battery_payload,
    is_observed,
    update_payload,
)
from .change_services import ChangeService
from .config_services import ConfigServices
from .const import (
    COVERAGE_WINDOW_S,
    DATA_DIR,
    DB_FILENAME,
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
    SENSOR_THROTTLE_S,
    UNLOAD_FLUSH_TIMEOUT_S,
    WRITER_BATCH_SIZE,
    WRITER_FLUSH_INTERVAL_S,
)
from .core.clock import Clock, SystemClock, us_to_rfc3339
from .core.coverage import CoverageTracker
from .core.errors import ErrorCode, SentinelError
from .core.ingestion import IngestionQueues
from .core.inventory import Inventory, InventoryChange
from .core.metrics import Metrics
from .core.models import (
    Availability,
    CapabilityStatus,
    CoverageSegment,
    CoverageStatus,
    Finding,
    ModuleState,
    Observation,
    ObservationKind,
    new_uuid,
)
from .core.ratelimit import RateLimiter
from .core.supervisor import Supervisor
from .data_services import DataServices
from .maintenance_services import MaintenanceService
from .modules.correlation.correlator import ActiveFinding, ExistingIncident, correlate
from .modules.dependency_mapper.graph import DependencyGraph
from .modules.doctor.engine import DoctorEngine, DoctorParams
from .modules.doctor.health import HealthResult, HealthUnit, compute_health
from .network_services import NetworkService
from .notification_services import NotificationService
from .repairs import async_update_storage_issue
from .repositories import repository as repo
from .repositories.db import DatabaseWorker, DbMode, UnsupportedSchemaError

_LOGGER = logging.getLogger(__name__)
SIGNAL_UPDATED = f"{DOMAIN}_updated"
PRESSURE_EVERY_S = 300
S = 1_000_000


@dataclass(slots=True)
class Capability:
    capability_id: str
    status: CapabilityStatus
    reason_code: str | None = None
    contract_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "status": str(self.status),
            "reason_code": self.reason_code,
            "contract_version": self.contract_version,
        }


class SentinelRuntime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, clock: Clock | None = None):
        self.hass = hass
        self.entry = entry
        self.clock = clock or SystemClock()
        self.boot_id = new_uuid()
        self.started_us = self.clock.now_us()
        self.state = ModuleState.STARTING
        self.reason_code: str | None = None
        options = {**DEFAULT_OPTIONS, **entry.options}
        self.doctor_params = DoctorParams(
            unavailable_s=int(options[OPT_DOCTOR_UNAVAILABLE_S]),
            startup_grace_s=int(options[OPT_DOCTOR_STARTUP_GRACE_S]),
        )
        self.db = DatabaseWorker(Path(hass.config.path(DATA_DIR)) / DB_FILENAME)
        self.queues = IngestionQueues(self.clock, source_id=SOURCE_ID)
        self.inventory = Inventory(self.boot_id)
        self.coverage = CoverageTracker(monitoring_since_us=self.started_us)
        self.maintenance = MaintenanceService(self)
        self.doctor = DoctorEngine(
            boot_started_us=self.started_us,
            params=self.doctor_params,
            coverage_complete=self.coverage.is_complete,
            in_maintenance=lambda uid: self.maintenance.covers(uid),
            domain_of=lambda uid: (e.domain if (e := self.inventory.get(uid)) else None),
        )
        self.capabilities: dict[str, Capability] = {}
        self.config: ConfigServices | None = None
        self.data: DataServices | None = None
        self.changes: ChangeService | None = None
        self.network: NetworkService | None = None
        self.notifications = NotificationService(self)
        self.version = "unknown"
        self.module_states: dict[str, ModuleState] = {}
        self._correlate_lock = asyncio.Lock()
        self._queue_high_since_us: int | None = None
        self._calm_since_us: int | None = None
        self.degradation_level = 0
        self._pressure_checked_us = 0
        self._evidence = EvidenceCollector()
        self.rate_limiter = RateLimiter(self.clock)
        self.metrics = Metrics()
        self.subscriptions: dict[str, int] = {}  # user_id -> open subscriptions
        self.active_incidents = 0
        self.revision = 0
        self._seq = itertools.count(1)
        self._pending_inventory: list[InventoryChange] = []
        self._availability: dict[str, Any] = {}  # entity_id -> last emitted availability
        self._unsubs: list[CALLBACK_TYPE] = []
        self._doctor_timer: CALLBACK_TYPE | None = None
        self._doctor_deadline: int | None = None
        self._writer_task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._writer_resume = asyncio.Event()
        self._supervisor = Supervisor()
        self._stopping = False

    # --- Lifecycle --------------------------------------------------------------

    async def async_start(self) -> None:
        try:
            mode = await self.db.async_open(self.started_us)
        except UnsupportedSchemaError:
            mode = DbMode.READ_ONLY
            self.reason_code = "unsupported_schema"
        if mode is DbMode.READ_ONLY:
            self.state = ModuleState.DEGRADED
            self.reason_code = self.db.reason or self.reason_code or "storage_read_only"
        async_update_storage_issue(self.hass, self.db.reason, self.db.path_hint)

        entities, devices = await self.db.run(repo.load_inventory)
        self.inventory.load(entities, devices)
        await self.maintenance.async_load()
        self.doctor.restore(await self.db.run(repo.load_active_findings))
        last_us = await self.db.run(repo.last_observation_us)
        self.active_incidents = await self.db.run(repo.count_active_incidents)

        # Subscription and snapshot in one synchronous block: no event can slip between.
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state))
        self._unsubs.append(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry)
        )
        self._snapshot_states()
        snapshot_done_us = self.clock.now_us()
        if last_us is not None:
            # New boot: coverage gap from the last durable observation to end of resync.
            self.coverage.add_gap(CoverageSegment(SOURCE_ID, "control", last_us,
                                                  snapshot_done_us, CoverageStatus.MISSING,
                                                  "restart"))
            await self.db.run(lambda c: repo.insert_coverage(c, self.coverage.gaps))

        self._build_capabilities(mode)
        # Process the baseline now so stale findings (subjects gone since the last boot,
        # revised rules) are closed explicitly before anything else is correlated.
        await self.async_flush()
        if stale := self.doctor.retire_stale(snapshot_done_us):
            await self._commit_findings(stale)
        self._writer_task = self.entry.async_create_background_task(
            self.hass, self._writer_loop(), f"{DOMAIN}_writer"
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._publish_tick,
                                      timedelta(seconds=SENSOR_THROTTLE_S))
        )
        await self._start_config_services()
        self.data = DataServices(self)
        await self.data.async_start()
        self.network = NetworkService(self)
        try:
            await self.network.async_start()
            status, reason = self.network.capability
        except Exception:
            # Network discovery must never take down Core or Doctor (AC NET 02).
            _LOGGER.exception("Sentinel network module failed to start")
            status, reason = CapabilityStatus.UNAVAILABLE, "network_failed"
        self.capabilities["network.probe"] = Capability("network.probe", status, reason)
        if self.config is not None:
            self.config.schedule_graph_rebuild()
        config_ready = self.module_states.get("config_history") is ModuleState.READY
        if self.config is not None and config_ready:
            self.changes = ChangeService(self)
            status, reason = self.changes.capability
            self.capabilities["managed_yaml.restore"] = Capability(
                "managed_yaml.restore", status, reason)
            try:
                await self.changes.async_recover()
            except Exception:
                _LOGGER.exception("Recovery of interrupted applies failed")
                self.capabilities["managed_yaml.restore"] = Capability(
                    "managed_yaml.restore", CapabilityStatus.UNAVAILABLE, "recovery_failed")
        self._update_degradation(0)
        if self.state is ModuleState.STARTING:
            self.state = ModuleState.READY

    async def async_stop(self) -> None:
        self.state = ModuleState.STOPPING
        self._stopping = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._doctor_timer is not None:
            self._doctor_timer()
            self._doctor_timer = None
        self._wake.set()
        if self._writer_task is not None:
            try:
                await asyncio.wait_for(self._writer_task, UNLOAD_FLUSH_TIMEOUT_S)
            except TimeoutError:
                _LOGGER.warning("Writer did not flush within %ss", UNLOAD_FLUSH_TIMEOUT_S)
                self._writer_task.cancel()
        if self.network is not None:
            await self.network.async_stop()
        if self.data is not None:
            await self.data.async_stop()
        if self.config is not None:
            await self.config.async_stop()
        await self.db.async_close()

    async def _start_config_services(self) -> None:
        """A failing E3 module degrades only itself; Doctor keeps running (spec 4.3)."""
        self.config = ConfigServices(self)
        names = ("config_history", "autopilot", "dependency_mapper")
        try:
            await self.config.async_start()
        except Exception:
            _LOGGER.exception("Sentinel configuration modules failed to start")
            for name in names:
                self.module_states[name] = ModuleState.FAILED
            return
        for name in names:
            self.module_states[name] = ModuleState.READY

    def _build_capabilities(self, mode: DbMode) -> None:
        caps = [
            Capability("inventory.read", CapabilityStatus.SUPPORTED),
            Capability("states.live", CapabilityStatus.SUPPORTED),
            Capability("history.read", CapabilityStatus.SUPPORTED
                       if "recorder" in self.hass.config.components
                       else CapabilityStatus.UNAVAILABLE,
                       None if "recorder" in self.hass.config.components
                       else "recorder_unavailable"),
            Capability("automation.import", CapabilityStatus.SUPPORTED
                       if mode is DbMode.READ_WRITE else CapabilityStatus.UNAVAILABLE,
                       None if mode is DbMode.READ_WRITE else "storage_read_only"),
            Capability("automation.lint", CapabilityStatus.SUPPORTED),
            Capability("graph.read", CapabilityStatus.SUPPORTED),
            Capability("automation.trace_import", CapabilityStatus.UNSUPPORTED,
                       "not_implemented"),
            Capability("automation.replay", CapabilityStatus.SUPPORTED
                       if "recorder" in self.hass.config.components
                       else CapabilityStatus.UNAVAILABLE,
                       None if "recorder" in self.hass.config.components
                       else "recorder_unavailable"),
            Capability("managed_yaml.restore", CapabilityStatus.DISABLED, "release_r1"),
            Capability("network.probe", CapabilityStatus.DISABLED, "n0_only"),
            Capability("ai.explain", CapabilityStatus.DISABLED, "ai_disabled"),
        ]
        if mode is DbMode.READ_ONLY:
            caps.append(Capability("storage.write", CapabilityStatus.UNAVAILABLE,
                                   self.reason_code))
        else:
            caps.append(Capability("storage.write", CapabilityStatus.SUPPORTED))
        self.capabilities = {c.capability_id: c for c in caps}

    # --- Ingestion (callbacks are O(1) and never await) ------------------------

    def _make_obs(self, kind: ObservationKind, uid: str, payload: dict[str, Any],
                  occurred_us: int | None) -> Observation:
        seq = next(self._seq)
        return Observation(
            id=new_uuid(), ingest_seq=seq, boot_id=self.boot_id, source_id=SOURCE_ID,
            source_event_id=str(seq), kind=kind, received_at_us=self.clock.now_us(),
            occurred_at_us=occurred_us, subject_uid=uid, payload=payload,
        )

    def _resolve_uid(self, entity_id: str) -> str:
        reg = er.async_get(self.hass).async_get(entity_id)
        uid, changes = self.inventory.observe(
            entity_id=entity_id,
            registry_id=reg.id if reg else None,
            device_id=reg.device_id if reg else None,
            now_us=self.clock.now_us(),
        )
        if changes:
            self._pending_inventory.extend(changes)
        return uid

    def _observed(self, entity_id: str) -> bool:
        """Privacy-excluded domains and Sentinel's own entities are never observed."""
        if not is_observed(entity_id):
            return False
        reg = er.async_get(self.hass).async_get(entity_id)
        return reg is None or reg.platform != DOMAIN

    def _snapshot_states(self) -> None:
        """Baseline of all current states in one synchronous pass (spec 4.1)."""
        baseline: list[Observation] = []
        for state in self.hass.states.async_all():
            if not self._observed(state.entity_id):
                continue
            uid = self._resolve_uid(state.entity_id)
            avail = availability_of(state)
            self._availability[state.entity_id] = avail
            baseline.append(self._make_obs(ObservationKind.STATE_SNAPSHOT, uid,
                                           {"availability": str(avail)}, None))
            if (bat := battery_payload(state)) is not None:
                baseline.append(self._make_obs(ObservationKind.NUMERIC_SAMPLE, uid, bat,
                                               _dt_us(state.last_updated)))
            if (upd := update_payload(state)) is not None:
                baseline.append(self._make_obs(ObservationKind.UPDATE_STATE, uid, upd,
                                               _dt_us(state.last_changed)))
        self.queues.replace_baseline(baseline)
        self._wake.set()

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        start = time.perf_counter_ns()
        try:
            self._handle_state(event)
        finally:
            # Budget: p99 <= 1 ms in the standard profile (spec 21.1).
            self.metrics.observe("ingestion_callback_us",
                                 (time.perf_counter_ns() - start) / 1000)

    def _handle_state(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        if new_state is None or not self._observed(entity_id):
            return
        avail = availability_of(new_state)
        prev = self._availability.get(entity_id)
        bat = battery_payload(new_state)
        upd = update_payload(new_state)
        old_upd = update_payload(event.data["old_state"])
        if upd is not None and upd != old_upd:
            self._offer(self._make_obs(ObservationKind.UPDATE_STATE,
                                       self._resolve_uid(entity_id), upd,
                                       _dt_us(new_state.last_changed)))
        if avail == prev and bat is None:
            return
        uid = self._resolve_uid(entity_id)
        occurred = _dt_us(new_state.last_changed)
        if avail != prev:
            self._availability[entity_id] = avail
            self._offer(self._make_obs(ObservationKind.AVAILABILITY_CHANGED, uid,
                                       {"from": str(prev) if prev else None, "to": str(avail)},
                                       occurred))
        if bat is not None:
            self._offer(self._make_obs(ObservationKind.NUMERIC_SAMPLE, uid, bat,
                                       _dt_us(new_state.last_updated)))

    @callback
    def _on_registry(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "remove":
            return  # renames are resolved through the stable registry id
        uid = self.inventory.uid_for_alias(event.data["entity_id"])
        if uid and (change := self.inventory.remove(uid, self.clock.now_us())):
            self._pending_inventory.append(change)
            self._availability.pop(event.data["entity_id"], None)

    def _offer(self, obs: Observation) -> None:
        self.metrics.queue_depth_max = max(self.metrics.queue_depth_max, self.queues.depth + 1)
        if self.queues.offer(obs) and self.queues.depth >= WRITER_BATCH_SIZE:
            self._wake.set()

    # --- Writer ---------------------------------------------------------------------

    async def _writer_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), WRITER_FLUSH_INTERVAL_S)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                while await self._flush_once():
                    pass
                if self.reason_code == "writer_failed":
                    self.state, self.reason_code = ModuleState.READY, None  # recovered
            except Exception:
                _LOGGER.exception("Sentinel writer failed")
                self.metrics.inc("module_failures_total", label="writer")
                delay = self._supervisor.failed(self.clock.monotonic_ns())
                if delay is None:
                    # Five failures in ten minutes: stop retrying until an admin decides.
                    self.state, self.reason_code = ModuleState.FAILED, "writer_paused"
                    self.bump()
                    await self._writer_resume.wait()
                    self._writer_resume.clear()
                    self.state, self.reason_code = ModuleState.READY, None
                    continue
                self.state, self.reason_code = ModuleState.DEGRADED, "writer_failed"
                self.bump()
                await asyncio.sleep(delay)
            if self._stopping:
                return

    def resume_writer(self) -> bool:
        """Admin decision after the writer was paused (spec 4.3)."""
        if self.reason_code != "writer_paused":
            return False
        self._supervisor.reset()
        self._writer_resume.set()
        return True

    async def async_flush(self) -> None:
        """Drain the queues now (unload paths and deterministic tests)."""
        while await self._flush_once():
            pass

    async def _flush_once(self) -> bool:
        drained = self.queues.drain()
        inventory, self._pending_inventory = self._pending_inventory, []
        if not drained.observations and not drained.gaps and not inventory:
            return False
        for gap in drained.gaps:
            self.coverage.add_gap(gap)
        if self.storage_writable:
            batch = drained.observations

            def persist(conn):
                repo.apply_inventory_changes(conn, inventory)
                repo.insert_observations(conn, batch)
                if drained.gaps:
                    repo.insert_coverage(conn, drained.gaps)

            t0 = time.perf_counter()
            await self.db.run(persist)
            self.metrics.observe("writer_commit_ms", (time.perf_counter() - t0) * 1000)
        changed: list[Finding] = []
        t0 = time.perf_counter()
        now_us = self.clock.now_us()
        for obs in drained.observations:
            self.metrics.observe("ingestion_lag_ms", (now_us - obs.received_at_us) / 1000)
            changed += self.doctor.process(obs)
        if drained.observations:
            self.metrics.observe("rules_duration_ms", (time.perf_counter() - t0) * 1000)
        await self._commit_findings(changed)
        if drained.resync_required and not self.queues.baseline_pending:
            # Resync only once the previous baseline is processed (no resync storms).
            self._availability.clear()
            self._snapshot_states()
        self._reschedule_doctor()
        return len(drained.observations) >= WRITER_BATCH_SIZE

    async def _commit_findings(self, findings: list[Finding]) -> None:
        if not findings:
            return
        now = self.clock.now_us()
        if self.storage_writable:
            def persist(conn):
                for f in findings:
                    repo.upsert_finding(conn, f, now)

            await self.db.run(persist)
            await self.async_correlate()
        self.bump()

    async def async_correlate(self) -> None:
        """Derive incidents and hypotheses from all active findings (spec 9)."""
        if not self.storage_writable:
            return
        async with self._correlate_lock:
            t_start = time.perf_counter()
            now = self.clock.now_us()
            network_findings = self.network.active_findings() if self.network else []
            network_findings += list(self.data.findings.values()) if self.data else []
            findings = [
                ActiveFinding(f.id, f.rule_id, f.subject_uid, f.severity, f.first_seen_us)
                for f in [*self.doctor.active_findings(), *network_findings]
            ]
            existing = [
                ExistingIncident(r["id"], r["fingerprint"], frozenset(r["finding_ids"]),
                                 r["opened_at_us"], r["root_uid"])
                for r in await self.db.run(repo.load_active_incidents)
            ]
            graph = self.config.graph if self.config is not None else DependencyGraph()
            evidence = self._evidence.collect(self.hass, now)
            if self.network is not None:
                evidence.update(self.network.evidence())
            availability = {
                uid: (a is Availability.AVAILABLE)
                for uid, a in self.doctor.availability_snapshot().items()
            }
            plans = await self.hass.async_add_executor_job(
                lambda: correlate(
                    instance_id=self.entry.data.get("instance_id", self.entry.entry_id),
                    findings=findings, existing=existing, graph=graph, evidence=evidence,
                    entity_available=availability.get,
                    coverage_complete=self.coverage.is_complete, now_us=now,
                )
            )

            def apply(conn):
                changed = repo.sync_incidents(conn, plans, now)
                return changed, repo.count_active_incidents(conn)

            changed, self.active_incidents = await self.db.run(apply)
            self.metrics.observe("correlation_ms", (time.perf_counter() - t_start) * 1000)
        if changed:
            await self.notifications.process()
            self.bump()

    # --- Doctor timers -------------------------------------------------------------

    def _reschedule_doctor(self) -> None:
        deadline = self.doctor.next_deadline_us()
        if deadline == self._doctor_deadline or self._stopping:
            return
        if self._doctor_timer is not None:
            self._doctor_timer()
            self._doctor_timer = None
        self._doctor_deadline = deadline
        if deadline is not None:
            when = datetime.fromtimestamp(deadline / S, tz=UTC)
            self._doctor_timer = async_track_point_in_utc_time(self.hass, self._doctor_fire, when)

    @callback
    def _doctor_fire(self, _now: datetime) -> None:
        self._doctor_timer = None
        self._doctor_deadline = None
        findings = self.doctor.tick(self.clock.now_us())
        self._reschedule_doctor()
        if findings:
            # Tracked (not background): findings and incidents must be committed before
            # the entry unloads, and HA waits for tracked tasks.
            self.entry.async_create_task(
                self.hass, self._commit_findings(findings), f"{DOMAIN}_findings"
            )

    # --- Read model -----------------------------------------------------------------

    @callback
    def _publish_tick(self, _now: datetime) -> None:
        self.entry.async_create_task(self.hass, self._periodic(), f"{DOMAIN}_periodic")

    def _update_degradation(self, storage_level: int) -> None:
        """Levels from spec 21.2 with hysteresis: return only after 5 min below 50 %."""
        now = self.clock.now_us()
        ratio = self.queues.depth / self.queues.capacity if self.queues.capacity else 0.0
        if ratio > 0.8:
            self._queue_high_since_us = self._queue_high_since_us or now
        else:
            self._queue_high_since_us = None
        queue_level = 2 if ratio > 0.95 else 1 if (
            self._queue_high_since_us and now - self._queue_high_since_us >= 60 * S) else 0
        target = max(queue_level, storage_level, 0 if self.storage_writable else 3)
        if target >= self.degradation_level:
            self.degradation_level = target
            self._calm_since_us = None
            return
        if ratio < 0.5 and storage_level < self.degradation_level:
            self._calm_since_us = self._calm_since_us or now
            if now - self._calm_since_us >= 5 * 60 * S:
                self.degradation_level = target
                self._calm_since_us = None
        else:
            self._calm_since_us = None

    def require_degradation_below(self, level: int) -> None:
        if self.degradation_level >= level:
            raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE,
                                "Sentinel is degraded; heavy work is paused",
                                reason=f"degraded_level_{self.degradation_level}")

    async def _periodic(self) -> None:
        self.maintenance.refresh_parents()
        now = self.clock.now_us()
        if self.data is not None and now - self._pressure_checked_us >= PRESSURE_EVERY_S * S:
            self._pressure_checked_us = now  # walking blobs/ is not free: every 5 min
            await self.data.check_pressure()
        self._update_degradation(self.data.storage_level if self.data is not None else 0)
        if self.storage_writable:
            now = self.clock.now_us()
            if await self.db.run(lambda c: repo.expire_suppressions(c, now)):
                self.revision += 1
            # Re-rank periodically: evidence and dependent states change without findings.
            await self.async_correlate()
            await self.notifications.process()  # retries and dismissals
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    def bump(self) -> None:
        """Signal a change of the read model to sensors and subscribers."""
        self.revision += 1
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    @property
    def storage_writable(self) -> bool:
        cap = self.capabilities.get("storage.write")
        return cap is not None and cap.status is CapabilityStatus.SUPPORTED

    def node_label(self, uid: str) -> dict[str, Any]:
        node = self.config.graph.nodes.get(uid) if self.config is not None else None
        if node is None:
            return {"uid": uid, "label": uid, "type": None}
        return {"uid": uid, "label": node.label, "type": node.type}

    def subject_label(self, uid: str | None) -> dict[str, Any]:
        """Display data for a subject; rendered as text only by the panel."""
        ent = self.inventory.get(uid) if uid else None
        if ent is None:
            return {"uid": uid, "entity_id": None, "name": None, "deleted": False}
        state = self.hass.states.get(ent.current_alias)
        return {
            "uid": uid,
            "entity_id": ent.current_alias,
            "name": state.name if state else None,
            "deleted": ent.deleted_at_us is not None,
        }

    async def diagnostics(self) -> dict[str, Any]:
        stats = await self.db.run(repo.storage_stats)
        gaps = await self.db.run(lambda c: repo.list_coverage_gaps(c, 20))
        q = self.queues
        return {
            "boot_id": self.boot_id,
            "started_at_us": self.started_us,
            "state": str(self.state),
            "reason_code": self.reason_code,
            "queues": {
                "depth": q.depth,
                "capacity": q.capacity,
                "received_total": q.stats.received_total,
                "dropped_total": q.stats.dropped_total,
                "rejected_oversize_total": q.stats.rejected_oversize_total,
                "telemetry_coalesced_total": q.stats.telemetry_coalesced_total,
                "degraded": q.degraded,
            },
            "storage": {**stats, "mode": str(self.db.mode)},
            "coverage_gaps": gaps,
            "metrics": self.metrics.snapshot(),
            "doctor": {
                "active_findings": len(self.doctor.active_findings()),
                "flapping_partial": len(self.doctor.flap_partial),
                "next_deadline_us": self.doctor.next_deadline_us(),
            },
        }

    def health(self) -> HealthResult:
        now = self.clock.now_us()
        covered = self.coverage.is_complete(now - COVERAGE_WINDOW_S * S, now)
        groups: dict[str, set[str]] = {}
        for ent in self.inventory.active_entities():
            if ent.current_alias not in self._availability:
                continue  # no valid baseline for this entity in the current boot
            groups.setdefault(ent.device_uid or ent.uid, set()).add(ent.uid)
        units = [HealthUnit(k, frozenset(v), covered=covered) for k, v in groups.items()]
        # Planned maintenance leaves the denominator; muted failures do not (spec 8.2).
        excluded = {u.uid: "maintenance" for u in units
                    if u.entity_uids and all(self.maintenance.covers(e, now)
                                             for e in u.entity_uids)}
        excluded.update({e: "maintenance" for u in units for e in u.entity_uids
                         if self.maintenance.covers(e, now)})
        result = compute_health(units, self.doctor.active_findings(), excluded)
        result.maintenance_units = sum(1 for u in units if u.uid in excluded)
        return result

    @property
    def degraded(self) -> bool:
        return self.state in (ModuleState.DEGRADED, ModuleState.FAILED) or self.queues.degraded

    def overview(self) -> dict[str, Any]:
        h = self.health()
        return {
            "api_version": 1,
            "revision": self.revision,
            "state": str(self.state),
            "reason_code": self.reason_code,
            "degradation_level": self.degradation_level,
            "health": {
                "score": h.score,
                "score_partial": float(h.score_exact) if h.score_exact is not None else None,
                "coverage_pct": float(h.coverage_pct) if h.coverage_pct is not None else None,
                # Warm-up after start or a gap: the 15 min window is not complete yet.
                "coverage_complete_at": (
                    us_to_rfc3339(ready) if (ready := self.coverage.complete_at_us(
                        COVERAGE_WINDOW_S * S, self.clock.now_us())) else None
                ),
                "units": h.units_total,
                "maintenance_units": h.maintenance_units,
            },
            "counters": {
                "active_incidents": self.active_incidents,
                "active_findings": len(self.doctor.active_findings()),
                "entities": len(self.inventory),
                "queue_depth": self.queues.depth,
                "dropped_observations_total": self.queues.stats.dropped_total,
            },
            "modules": {"doctor": str(self.state),
                        **{k: str(v) for k, v in self.module_states.items()}},
        }


def _dt_us(value: datetime) -> int:
    return int(value.timestamp() * S)
