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
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_report_event,
    async_track_time_interval,
)

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
    HEARTBEAT_MIN_INTERVAL_S,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
    OPT_INGESTION_ALLOWED_DOMAINS,
    OPT_INGESTION_NUMERIC_SAMPLING,
    OPT_MODULES_AUTOPILOT,
    OPT_MODULES_CONFIG_HISTORY,
    OPT_MODULES_DEPENDENCY_MAPPER,
    OPT_MODULES_DOCTOR,
    OPT_MODULES_NETWORK,
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
    FindingStatus,
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
from .modules.doctor.engine import DOCTOR_RULES, DoctorEngine, DoctorParams
from .modules.doctor.health import HealthResult, HealthUnit, compute_health
from .modules.doctor.numeric import NumericSource
from .network_services import NetworkService
from .notification_services import NotificationService
from .numeric_services import NumericSourceService
from .repairs import async_update_storage_issue
from .repositories import repository as repo
from .repositories.db import DatabaseWorker, DbMode, UnsupportedSchemaError
from .rules_services import RuleService

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
        # Module switches (spec 27.1); Core, ingestion and inventory always run.
        self.modules_enabled = {
            "doctor": bool(options[OPT_MODULES_DOCTOR]),
            "config_history": bool(options[OPT_MODULES_CONFIG_HISTORY]),
            "autopilot": bool(options[OPT_MODULES_AUTOPILOT]),
            "dependency_mapper": bool(options[OPT_MODULES_DEPENDENCY_MAPPER]),
            "network": bool(options[OPT_MODULES_NETWORK]),
        }
        # Empty = all domains; privacy exclusions win either way.
        self.allowed_domains = frozenset(options[OPT_INGESTION_ALLOWED_DOMAINS])
        self.numeric_sampling = bool(options[OPT_INGESTION_NUMERIC_SAMPLING])
        self.db = DatabaseWorker(Path(hass.config.path(DATA_DIR)) / DB_FILENAME)
        self.queues = IngestionQueues(self.clock, source_id=SOURCE_ID)
        self.inventory = Inventory(self.boot_id)
        self.coverage = CoverageTracker(monitoring_since_us=self.started_us)
        self.maintenance = MaintenanceService(self)
        self.rules = RuleService(self)
        self.numeric = NumericSourceService(self)
        self.unit_weights: dict[str, int] = {}  # health unit uid -> admin weight 2..5
        # entity_id -> explicit numeric sources (manual battery mapping, signal scales).
        self._numeric_aliases: dict[str, list[NumericSource]] = {}
        self.doctor = DoctorEngine(
            boot_started_us=self.started_us,
            params=self.doctor_params,
            coverage_complete=self.coverage.is_complete,
            in_maintenance=lambda uid: self.maintenance.covers(uid),
            domain_of=lambda uid: (e.domain if (e := self.inventory.get(uid)) else None),
            tz_name=str(hass.config.time_zone),
            automation_enabled=self._automation_enabled,
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
        # One batch at a time: a flush must not return while the writer still holds a
        # drained batch that Doctor has not seen yet.
        self._flush_lock = asyncio.Lock()
        self._writer_resume = asyncio.Event()
        self._supervisor = Supervisor()
        self._stopping = False
        # DOC005 contracts: entity_id -> uid, last emitted heartbeat, state_reported tracker.
        self._heartbeat_aliases: dict[str, str] = {}
        self._heartbeat_last_us: dict[str, int] = {}
        self._heartbeat_unsub: CALLBACK_TYPE | None = None
        self._expect_aliases: dict[str, str] = {}  # automation entity_id -> uid (DOC007)

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
        self._pending_inventory.extend(
            self.inventory.load(entities, devices, self.clock.now_us()))
        await self.maintenance.async_load()
        restored = await self.db.run(repo.load_active_findings)
        self.doctor.restore(restored)  # takes only the rules it evaluates
        other_findings = [f for f in restored if f.rule_id not in DOCTOR_RULES]
        if not self.modules_enabled["doctor"]:
            # A switched-off Doctor must not leave findings that look current.
            closed_by_module = self.doctor.close_all(self.clock.now_us(), "module_disabled")
        else:
            closed_by_module = []
        await self.rules.async_load()
        await self.numeric.async_load()
        await self.async_load_weights()
        self.refresh_numeric_tracking()  # before the baseline, so it includes mapped samples
        closed_by_overrides = self.doctor.set_overrides(self.rules.overrides,
                                                        self.clock.now_us())
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
        self.refresh_heartbeat_tracking()
        if stale := [*self.doctor.retire_stale(snapshot_done_us), *closed_by_overrides,
                     *closed_by_module]:
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
        if self.modules_enabled["network"]:
            self.network = NetworkService(self)
            try:
                await self.network.async_start()
                status, reason = self.network.capability
                self.module_states["network"] = ModuleState.READY
            except Exception:
                # Network discovery must never take down Core or Doctor (AC NET 02).
                _LOGGER.exception("Sentinel network module failed to start")
                status, reason = CapabilityStatus.UNAVAILABLE, "network_failed"
                self.module_states["network"] = ModuleState.FAILED
        else:
            status, reason = CapabilityStatus.DISABLED, "module_disabled"
            self.module_states["network"] = ModuleState.DISABLED
        self.capabilities["network.probe"] = Capability("network.probe", status, reason)
        if self.config is not None:
            await self.config.async_rebuild_graph()  # all graph sources are up now
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
        await self._adopt_findings(other_findings)
        self._update_degradation(0)
        if self.state is ModuleState.STARTING:
            self.state = ModuleState.READY
        if self.config is not None:
            # Reference checks (DOC006) need a ready inventory; same graph, no new revision.
            self.config.schedule_graph_rebuild()

    async def async_stop(self) -> None:
        self.state = ModuleState.STOPPING
        self._stopping = True
        if self._heartbeat_unsub is not None:
            self._heartbeat_unsub()
            self._heartbeat_unsub = None
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
            self.module_states[name] = (ModuleState.READY if self.modules_enabled[name]
                                        else ModuleState.DISABLED)

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
        if self.allowed_domains and entity_id.split(".", 1)[0] not in self.allowed_domains:
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
            for sample in self._numeric_samples(state):
                baseline.append(self._make_obs(ObservationKind.NUMERIC_SAMPLE, uid, sample,
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
        if entity_id in self._heartbeat_aliases:
            self._heartbeat(entity_id, new_state)
        if entity_id in self._expect_aliases:
            self._automation_run(entity_id, event.data["old_state"], new_state)
        avail = availability_of(new_state)
        prev = self._availability.get(entity_id)
        samples = self._numeric_samples(new_state)
        upd = update_payload(new_state)
        old_upd = update_payload(event.data["old_state"])
        if upd is not None and upd != old_upd:
            self._offer(self._make_obs(ObservationKind.UPDATE_STATE,
                                       self._resolve_uid(entity_id), upd,
                                       _dt_us(new_state.last_changed)))
        if avail == prev and not samples:
            return
        uid = self._resolve_uid(entity_id)
        occurred = _dt_us(new_state.last_changed)
        if avail != prev:
            self._availability[entity_id] = avail
            self._offer(self._make_obs(ObservationKind.AVAILABILITY_CHANGED, uid,
                                       {"from": str(prev) if prev else None, "to": str(avail)},
                                       occurred))
        for sample in samples:
            self._offer(self._make_obs(ObservationKind.NUMERIC_SAMPLE, uid, sample,
                                       _dt_us(new_state.last_updated)))

    def _numeric_samples(self, state: State) -> list[dict[str, Any]]:
        """Battery by device_class and unit, or an explicit mapping; never by name."""
        sources = self._numeric_aliases.get(state.entity_id)
        if not sources:
            bat = battery_payload(state)
            return [bat] if bat is not None else []
        if availability_of(state) is not Availability.AVAILABLE:
            return []
        out = [p for src in sources if (p := src.payload(state.state)) is not None]
        if not any(src.purpose == "battery" for src in sources):
            # A signal mapping does not hide a battery reported by device_class.
            if (bat := battery_payload(state)) is not None:
                out.append(bat)
        return out

    @callback
    def refresh_numeric_tracking(self) -> None:
        aliases: dict[str, list[NumericSource]] = {}
        for src in self.numeric.active(self.numeric_sampling):
            ent = self.inventory.get(src.subject_uid)
            if ent is not None and ent.deleted_at_us is None:
                aliases.setdefault(ent.current_alias, []).append(src)
        self._numeric_aliases = aliases

    @callback
    def refresh_heartbeat_tracking(self) -> None:
        """(Re)subscribe to state reports of subjects with a heartbeat contract.

        A sensor that reports the same value again fires state_reported, not
        state_changed; both count as a report. Only contracted entities are tracked.
        """
        expect = {}
        for uid in self.doctor.expectation_subjects if self.modules_enabled["doctor"] else ():
            ent = self.inventory.get(uid)
            if ent is not None and ent.deleted_at_us is None:
                expect[ent.current_alias] = uid
        self._expect_aliases = expect
        aliases = {}
        subjects = self.doctor.heartbeat_subjects if self.modules_enabled["doctor"] else ()
        for uid in subjects:
            ent = self.inventory.get(uid)
            if ent is not None and ent.deleted_at_us is None:
                aliases[ent.current_alias] = uid
        if aliases == self._heartbeat_aliases and (self._heartbeat_unsub or not aliases):
            return
        if self._heartbeat_unsub is not None:
            self._heartbeat_unsub()
            self._heartbeat_unsub = None
        self._heartbeat_aliases = aliases
        if aliases:
            self._heartbeat_unsub = async_track_state_report_event(
                self.hass, list(aliases), self._on_state_reported)

    @callback
    def _on_state_reported(self, event: Event[EventStateReportedData]) -> None:
        entity_id = event.data["entity_id"]
        if entity_id in self._heartbeat_aliases:
            self._heartbeat(entity_id, event.data["new_state"])

    def _automation_enabled(self, uid: str) -> bool | None:
        """True when the automation is on now; None when unknown (DOC007 stays unknown)."""
        ent = self.inventory.get(uid)
        state = self.hass.states.get(ent.current_alias) if ent else None
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def _automation_run(self, entity_id: str, old: State | None, new: State) -> None:
        """last_triggered changed: an observed start, not proof of a successful run."""
        ran = new.attributes.get("last_triggered")
        if ran is None or (old is not None and old.attributes.get("last_triggered") == ran):
            return
        occurred = _dt_us(ran) if isinstance(ran, datetime) else None
        self._offer(self._make_obs(ObservationKind.AUTOMATION_RUN,
                                   self._expect_aliases[entity_id], {}, occurred))

    def _heartbeat(self, entity_id: str, state: State) -> None:
        if availability_of(state) is not Availability.AVAILABLE:
            return  # an unavailable entity has not reported
        uid = self._heartbeat_aliases[entity_id]
        now = self.clock.now_us()
        # At most one stored heartbeat per HEARTBEAT_MIN_INTERVAL_S: the contract
        # deadline is at least 3 periods away, so nothing is lost by throttling.
        if now - self._heartbeat_last_us.get(uid, 0) < HEARTBEAT_MIN_INTERVAL_S * 1_000_000:
            return
        self._heartbeat_last_us[uid] = now
        self._offer(self._make_obs(ObservationKind.HEARTBEAT, uid, {},
                                   _dt_us(state.last_reported)))

    async def _adopt_findings(self, findings: list[Finding]) -> None:
        """Hand restored findings to their owner modules; close those without an owner.

        Without this, DOC010 / NET001 / DOC006 from the last boot would stay active forever
        next to the new findings of the same problem.
        """
        for owner in (self.data, self.network, self.config):
            if owner is not None and findings:
                findings = owner.restore(findings)
        now = self.clock.now_us()
        for f in findings:
            f.status = FindingStatus.RESOLVED
            f.resolved_at_us = f.last_seen_us = now
            f.details = {**f.details, "resolution": "owner_unavailable"}
        await self._commit_findings(findings)

    @callback
    def reschedule_doctor(self) -> None:
        self._reschedule_doctor()

    async def commit_findings(self, findings: list[Finding]) -> None:
        await self._commit_findings(findings)

    async def config_changed(self, actor: str) -> None:
        """Rule settings changed: new type S snapshot (spec 10.1)."""
        if self.config is not None:
            await self.config.snapshot_sentinel_config(actor, request_id="rules")

    @callback
    def _on_registry(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "remove":
            if event.data["action"] == "update" and "old_entity_id" in event.data:
                self.refresh_heartbeat_tracking()  # renamed: follow the new alias
                self.refresh_numeric_tracking()
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
        async with self._flush_lock:
            return await self._flush_batch()

    async def _flush_batch(self) -> bool:
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
            if self.modules_enabled["doctor"]:
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
            network_findings += list(self.config.ref_findings.values()) if self.config else []
            findings = [
                ActiveFinding(f.id, f.rule_id, f.subject_uid, f.severity, f.first_seen_us)
                for f in [*self.doctor.active_findings(), *network_findings]
            ]
            existing = [
                ExistingIncident(r["id"], r["fingerprint"], frozenset(r["finding_ids"]),
                                 r["opened_at_us"], r["root_uid"])
                for r in await self.db.run(repo.load_active_incidents)
            ]
            # Without the dependency map only singleton incidents remain (no shared parents).
            graph = (self.config.graph if self.config is not None
                     and self.modules_enabled["dependency_mapper"] else DependencyGraph())
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
        if not self.modules_enabled["doctor"]:
            return
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
        if uid and uid.startswith("resource:") and self.config is not None:
            return {"uid": uid, "entity_id": None, "name": self.config.resource_label(uid),
                    "deleted": False}
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
        if not self.modules_enabled["doctor"]:
            return HealthResult(None, None, None, 0)  # no rules ran: never "healthy"
        now = self.clock.now_us()
        covered = self.coverage.is_complete(now - COVERAGE_WINDOW_S * S, now)
        groups: dict[str, set[str]] = {}
        for ent in self.inventory.active_entities():
            if ent.current_alias not in self._availability:
                continue  # no valid baseline for this entity in the current boot
            groups.setdefault(ent.device_uid or ent.uid, set()).add(ent.uid)
        units = [HealthUnit(k, frozenset(v), weight=self.unit_weights.get(k, 1), covered=covered)
                 for k, v in groups.items()]
        # Planned maintenance leaves the denominator; muted failures do not (spec 8.2).
        excluded = {u.uid: "maintenance" for u in units
                    if u.entity_uids and all(self.maintenance.covers(e, now)
                                             for e in u.entity_uids)}
        excluded.update({e: "maintenance" for u in units for e in u.entity_uids
                         if self.maintenance.covers(e, now)})
        result = compute_health(units, self.doctor.active_findings(), excluded)
        result.maintenance_units = sum(1 for u in units if u.uid in excluded)
        return result

    async def async_load_weights(self) -> None:
        self.unit_weights = await self.db.run(lambda c: dict(c.execute(
            "SELECT unit_uid, weight FROM unit_weights")))

    async def set_unit_weight(self, unit_uid: str, weight: int, actor: str) -> None:
        """Weight 1 is the default and removes the row (spec 8.3: admin may set 1..5)."""
        if not 1 <= weight <= 5:
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "weight out of range",
                                param="weight", min=1, max=5)
        if unit_uid not in {c["unit"] for c in self.health().components}:
            raise SentinelError(ErrorCode.NOT_FOUND, "unknown health unit")
        now = self.clock.now_us()

        def write(conn):
            with repo._tx(conn):
                if weight == 1:
                    conn.execute("DELETE FROM unit_weights WHERE unit_uid=?", (unit_uid,))
                else:
                    conn.execute("INSERT INTO unit_weights(unit_uid, weight, actor_id, "
                                 "updated_us) VALUES (?,?,?,?) ON CONFLICT(unit_uid) DO UPDATE "
                                 "SET weight=excluded.weight, actor_id=excluded.actor_id, "
                                 "updated_us=excluded.updated_us",
                                 (unit_uid, weight, actor, now))
                repo.insert_audit(conn, actor, unit_uid, "health.weight", unit_uid,
                                  f"weight={weight}", now)

        await self.db.run(write)
        await self.async_load_weights()
        await self.config_changed(actor)
        self.bump()

    def health_components(self) -> dict[str, Any]:
        """Every part of the score and every exclusion, with readable labels (spec 8.3)."""
        h = self.health()
        reg = dr.async_get(self.hass)
        ha_device = {d.uid: d.ha_device_id for d in self.inventory.devices()}
        active: dict[str, list[str]] = {}
        for f in self.doctor.active_findings():
            active.setdefault(f.subject_uid, []).append(f.rule_id)
        by_device: dict[str, list[Any]] = {}
        for e in self.inventory.active_entities():
            if e.device_uid:
                by_device.setdefault(e.device_uid, []).append(e)
        out = []
        for c in h.components:
            uid = c["unit"]
            if uid in ha_device:
                dev = reg.async_get(ha_device[uid])
                label = (dev.name_by_user or dev.name) if dev else None
                members = by_device.get(uid, [])
                kind = "device"
            else:
                ent = self.inventory.get(uid)
                label = ent.current_alias if ent else None
                members = [ent] if ent else []
                kind = "entity"
            out.append({
                **c, "kind": kind, "label": label or uid,
                "entities": len(members),
                "rules": sorted({r for m in members for r in active.get(m.uid, [])}),
            })
        return {"score": h.score, "score_partial": float(h.score_exact)
                if h.score_exact is not None else None, "units": out}

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
            "modules": {"doctor": str(self.state if self.modules_enabled["doctor"]
                                      else ModuleState.DISABLED),
                        **{k: str(v) for k, v in self.module_states.items()}},
        }


def _dt_us(value: datetime) -> int:
    return int(value.timestamp() * S)
