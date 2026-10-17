"""Glue for E3 modules: config history, autopilot lint and dependency graph.

Registry data is collected on the HA loop; parsing, linting and graph building run in an
executor, and SQLite/blob I/O runs on the DB worker (spec 2.2).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .adapters.registry import RegistrySnapshot, async_snapshot
from .const import DATA_DIR, DOMAIN
from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.yaml_ast import parse
from .modules.autopilot import draft as wizard
from .modules.autopilot.lint import lint
from .modules.autopilot.model import extract
from .modules.config_history.canonical import canonical_hash, semantic_diff, textual_diff
from .modules.config_history.importer import PreparedImport, prepare_import
from .modules.config_history.render import to_yaml
from .modules.dependency_mapper.graph import DependencyGraph, ImportedResource, build_graph
from .repositories import history
from .repositories.blobs import BlobStore

if TYPE_CHECKING:
    from .runtime import SentinelRuntime

_LOGGER = logging.getLogger(__name__)
GRAPH_DEBOUNCE_S = 2.0
LINT_TIMEOUT_S = 5.0


class ConfigServices:
    def __init__(self, runtime: SentinelRuntime) -> None:
        self.rt = runtime
        self.hass: HomeAssistant = runtime.hass
        self.blobs = BlobStore(Path(self.hass.config.path(DATA_DIR)) / "blobs")
        self.graph = DependencyGraph(revision=0)
        self.graph_built_us: int | None = None
        self._graph_timer: CALLBACK_TYPE | None = None
        self._graph_lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        for event in (er.EVENT_ENTITY_REGISTRY_UPDATED, dr.EVENT_DEVICE_REGISTRY_UPDATED,
                      ar.EVENT_AREA_REGISTRY_UPDATED):
            self._unsubs.append(self.hass.bus.async_listen(event, self._registry_changed))
        if self.rt.storage_writable:
            await self._snapshot_sentinel_options()
        await self.async_rebuild_graph()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._graph_timer is not None:
            self._graph_timer()
            self._graph_timer = None

    # --- Graph -------------------------------------------------------------------------

    @callback
    def _registry_changed(self, _event) -> None:
        self.schedule_graph_rebuild()

    @callback
    def schedule_graph_rebuild(self) -> None:
        """Debounced incremental trigger (spec 12.2: 2 s)."""
        if self._graph_timer is not None:
            self._graph_timer()

        @callback
        def fire(_now) -> None:
            self._graph_timer = None
            self.rt.entry.async_create_background_task(
                self.hass, self.async_rebuild_graph(), f"{DOMAIN}_graph"
            )

        self._graph_timer = async_call_later(self.hass, GRAPH_DEBOUNCE_S, fire)

    async def async_rebuild_graph(self) -> None:
        async with self._graph_lock:
            snap = async_snapshot(self.hass, self.rt.inventory)
            rows = await self.rt.db.run(lambda c: history.latest_imports(c, self.blobs))
            revision = self.graph.revision + 1
            net_nodes, net_edges = (self.rt.network.graph_elements()
                                    if self.rt.network is not None else ([], []))

            def build() -> DependencyGraph:
                imports = [
                    ImportedResource(r["resource_id"], r["label"], extract(r["tree"], r["kind"]),
                                     us_to_rfc3339(r["created_us"]))
                    for r in rows
                ]
                return build_graph(entities=snap.entities, devices=snap.devices,
                                   entries=snap.entries, areas=snap.areas, imports=imports,
                                   revision=revision, extra_nodes=net_nodes,
                                   extra_edges=net_edges)

            self.graph = await self.hass.async_add_executor_job(build)
            self.graph_built_us = self.rt.clock.now_us()
        self.rt.bump()

    # --- Config history ------------------------------------------------------------------

    async def _snapshot_sentinel_options(self) -> None:
        """Type S resource: Sentinel's own options, snapshotted on every change (spec 10.1)."""
        tree = {k: self.rt.entry.options[k] for k in sorted(self.rt.entry.options)}
        now = self.rt.clock.now_us()

        def save(conn):
            rid = history.find_resource(conn, "sentinel", "sentinel_options")
            return history.save_snapshot_tx(
                conn, self.blobs, resource_id=rid, provider="sentinel", kind="sentinel_options",
                label="HA Sentinel options", canonical_tree=tree,
                canonical_hash=canonical_hash(tree), raw=None, raw_hash=None,
                redacted_paths=[], validation={"parser": "n/a"}, actor_id=None,
                request_id="startup", now_us=now,
            )

        await self.rt.db.run(save)

    async def prepare(self, content: str, content_hash: str, kind: str,
                      redact_secrets: bool) -> PreparedImport:
        self.rt.require_degradation_below(2)
        return await self.hass.async_add_executor_job(
            prepare_import, content, content_hash, kind, redact_secrets
        )

    def save_import_op(self, prepared: PreparedImport, *, resource_id: str | None, kind: str,
                       label: str):
        """Returns an op for repository.run_idempotent (runs on the DB worker)."""

        def op(conn, actor: str, request_id: str, now: int) -> dict[str, Any]:
            result = history.save_snapshot(
                conn, self.blobs, resource_id=resource_id, provider="import", kind=kind,
                label=label, canonical_tree=prepared.tree,
                canonical_hash=prepared.canonical_hash, raw=prepared.raw,
                raw_hash=prepared.raw_hash, redacted_paths=prepared.redacted_paths,
                validation=prepared.validation, actor_id=actor, request_id=request_id,
                now_us=now,
            )
            return {**result, "canonical_hash": prepared.canonical_hash,
                    "redacted_paths": prepared.redacted_paths,
                    "raw_kept": prepared.raw is not None, "restorable": False,
                    "validation": prepared.validation}

        return op

    async def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snap = await self.rt.db.run(lambda c: history.get_snapshot(c, self.blobs, snapshot_id))
        if snap is None:
            raise SentinelError(ErrorCode.NOT_FOUND, "snapshot not found")
        return snap

    async def export_text(self, snapshot_id: str) -> dict[str, Any]:
        snap = await self.get_snapshot(snapshot_id)
        if snap["raw"] is not None:
            text, source = snap["raw"], "raw"
        else:
            text = await self.hass.async_add_executor_job(to_yaml, snap["tree"])
            source = "rendered"
        return {"snapshot_id": snapshot_id, "label": snap["label"], "kind": snap["kind"],
                "source": source, "redacted_paths": snap["redacted_paths"],
                "restorable": snap["restorable"], "content": text}

    async def diff(self, from_id: str, to_id: str) -> dict[str, Any]:
        a = await self.get_snapshot(from_id)
        b = await self.get_snapshot(to_id)

        def compute() -> dict[str, Any]:
            changes = semantic_diff(a["tree"], b["tree"])
            text = textual_diff(a["raw"], b["raw"]) if a["raw"] and b["raw"] else None
            return {
                "semantic": [c.as_dict() for c in changes],
                "semantic_changed": a["canonical_hash"] != b["canonical_hash"],
                "textual_changed": (a["raw_hash"] != b["raw_hash"]
                                    if a["raw_hash"] and b["raw_hash"] else None),
                "textual": text,
            }

        return await self.hass.async_add_executor_job(compute)

    # --- Replay (spec 11.4) -------------------------------------------------------------

    async def replay(self, snapshot_id: str, hours: int) -> dict[str, Any]:
        from datetime import timedelta

        from homeassistant.util import dt as dt_util

        from .adapters import history as ha_history
        from .modules.autopilot.replay import replay as run_replay

        self.rt.require_degradation_below(1)  # level 1 pauses costly replay (spec 21.2)
        snap = await self.get_snapshot(snapshot_id)
        if snap["kind"] != "automation":
            raise SentinelError(ErrorCode.INVALID_REQUEST, "only automations can be replayed")
        model = await self.hass.async_add_executor_job(extract, snap["tree"], "automation")
        entities = sorted({r.value for run in model.runnables for r in run.refs
                           if r.kind == "entity" and r.role in ("trigger", "condition")})
        end = dt_util.utcnow()
        start = end - timedelta(hours=hours)
        initial, events, truncated = await ha_history.fetch(self.hass, entities, start, end)
        tree = snap["tree"]
        try:
            report = await asyncio.wait_for(
                self.hass.async_add_executor_job(lambda: run_replay(tree, initial, events)),
                LINT_TIMEOUT_S)
        except TimeoutError as err:
            raise SentinelError(ErrorCode.CANCELLED, "replay exceeded 5 s") from err
        out = report.as_dict()
        for occ in out["occurrences"]:
            occ["time"] = us_to_rfc3339(occ.pop("time_us"))
        out.update({
            "snapshot_id": snapshot_id,
            "window": {"start": us_to_rfc3339(int(start.timestamp() * 1_000_000)),
                       "end": us_to_rfc3339(int(end.timestamp() * 1_000_000))},
            "entities": entities,
            "events": len(events),
            "events_truncated": truncated,
            "source": "recorder",
        })
        return out

    # --- Autopilot lint ------------------------------------------------------------------

    async def draft(self, spec: wizard.DraftSpec) -> dict[str, Any]:
        """Wizard draft through the same parser and lint as an import (AC AUT 04)."""
        reg: RegistrySnapshot = async_snapshot(self.hass, self.rt.inventory)
        wizard.validate(spec, lambda eid: eid in reg.active_entity_ids)
        complete = self.rt.state.value == "ready" and not self.rt.queues.degraded
        text = wizard.to_yaml(wizard.build_tree(spec))

        def run() -> dict[str, Any]:
            parsed = parse(text)  # the draft is treated exactly like untrusted input
            model = extract(parsed.tree, "automation")
            report = lint(model, entity_status=reg.entity_status,
                          service_exists=reg.service_exists, inventory_complete=complete)
            findings = [f.as_dict(parsed) for f in report.findings]
            return {
                "yaml": text,
                "completeness": round(report.completeness, 4),
                "findings": findings,
                "valid": not any(f["level"] == "error" for f in findings),
                "inventory_complete": complete,
            }

        try:
            return await asyncio.wait_for(self.hass.async_add_executor_job(run), LINT_TIMEOUT_S)
        except TimeoutError as err:
            raise SentinelError(ErrorCode.CANCELLED, "analysis exceeded 5 s") from err

    async def analyze(self, snapshot_id: str) -> dict[str, Any]:
        snap = await self.get_snapshot(snapshot_id)
        if snap["kind"] not in ("automation", "script", "blueprint"):
            raise SentinelError(ErrorCode.INVALID_REQUEST, "resource kind cannot be analysed")
        reg: RegistrySnapshot = async_snapshot(self.hass, self.rt.inventory)
        complete = self.rt.state.value == "ready" and not self.rt.queues.degraded
        raw = snap["raw"]

        def run() -> dict[str, Any]:
            parsed = parse(raw) if raw is not None else None
            model = extract(snap["tree"], snap["kind"])
            report = lint(model, entity_status=reg.entity_status,
                          service_exists=reg.service_exists, inventory_complete=complete)
            return {
                "snapshot_id": snapshot_id,
                "ruleset_version": report.ruleset_version,
                "completeness": round(report.completeness, 4),
                "runnables": [
                    {"key": r.key, "label": r.label, "kind": r.kind, "mode": r.mode,
                     "pointer": r.pointer, "triggers": len(r.triggers), "calls": len(r.calls),
                     "templates": len(r.templates), "unsupported": len(r.unsupported)}
                    for r in model.runnables
                ],
                "findings": [f.as_dict(parsed) for f in report.findings],
                "templates": report.templates,
                "truncated": report.truncated,
                "inventory_complete": complete,
                "spans_available": parsed is not None,
            }

        try:
            return await asyncio.wait_for(self.hass.async_add_executor_job(run), LINT_TIMEOUT_S)
        except TimeoutError as err:
            raise SentinelError(ErrorCode.CANCELLED, "analysis exceeded 5 s") from err
