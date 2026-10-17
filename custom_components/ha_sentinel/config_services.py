"""Glue for E3 modules: config history, autopilot lint and dependency graph.

Registry data is collected on the HA loop; parsing, linting and graph building run in an
executor, and SQLite/blob I/O runs on the DB worker (spec 2.2).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .adapters.registry import RegistrySnapshot, async_snapshot
from .const import DATA_DIR, DOMAIN
from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.models import CoverageStatus, Finding, FindingStatus, Severity, new_uuid
from .core.yaml_ast import parse
from .modules.autopilot import draft as wizard
from .modules.autopilot.lint import lint
from .modules.autopilot.model import extract
from .modules.config_history.canonical import canonical_hash, semantic_diff, textual_diff
from .modules.config_history.importer import PreparedImport, prepare_import
from .modules.config_history.render import to_yaml
from .modules.dependency_mapper import versions
from .modules.dependency_mapper.graph import DependencyGraph, ImportedResource, build_graph
from .modules.doctor.references import ImportRefs, evaluate
from .repositories import history
from .repositories.blobs import BlobCorruptedError, BlobStore
from .repositories.repository import _tx

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
        self.ref_findings: dict[str, Finding] = {}  # "resource:<id>" -> DOC006
        self._graph_doc: dict[str, Any] | None = None  # document of the latest revision
        self._old_graphs: dict[int, DependencyGraph] = {}  # small cache of old revisions

    async def async_start(self) -> None:
        for event in (er.EVENT_ENTITY_REGISTRY_UPDATED, dr.EVENT_DEVICE_REGISTRY_UPDATED,
                      ar.EVENT_AREA_REGISTRY_UPDATED):
            self._unsubs.append(self.hass.bus.async_listen(event, self._registry_changed))
        await self.snapshot_sentinel_config()
        await self._load_graph_head()
        # The first build waits for the network module (runtime calls it once every
        # module is up); building now would record a revision without network nodes.
        # Full rebuild every 24 h even without registry events (spec 12.2).
        self._unsubs.append(async_track_time_interval(
            self.hass, self._daily_rebuild, timedelta(hours=24)))

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
            revision = self.graph.revision + 1  # provisional; kept only if it changed
            net_nodes, net_edges = (self.rt.network.graph_elements()
                                    if self.rt.network is not None else ([], []))

            def build() -> tuple[DependencyGraph, list[ImportRefs]]:
                imports = [
                    ImportedResource(r["resource_id"], r["label"], extract(r["tree"], r["kind"]),
                                     us_to_rfc3339(r["created_us"]))
                    for r in rows
                ]
                refs = [
                    ImportRefs(i.resource_id, i.label, i.imported_at,
                               complete=i.model.completeness == 1.0 and not i.model.unsupported,
                               refs=tuple((ref.value, ref.pointer) for r in i.model.runnables
                                          for ref in r.refs if ref.kind == "entity"))
                    for i in imports
                ]
                graph = build_graph(entities=snap.entities, devices=snap.devices,
                                    entries=snap.entries, areas=snap.areas, imports=imports,
                                    revision=revision, extra_nodes=net_nodes,
                                    extra_edges=net_edges)
                return graph, refs

            graph, refs = await self.hass.async_add_executor_job(build)
            graph.revision = await self._record_revision(graph)
            self.graph = graph
            self.graph_built_us = self.rt.clock.now_us()
            await self._check_references(refs, snap)
        self.rt.bump()

    @callback
    def _daily_rebuild(self, _now: Any) -> None:
        self.schedule_graph_rebuild()

    # --- Graph revisions (spec 7.2, 12.2, AC MAP 05) -------------------------------------

    async def _load_graph_head(self) -> None:
        """Continue the persisted revision sequence after a restart."""
        head = await self.rt.db.run(lambda c: c.execute(
            "SELECT MAX(revision) FROM graph_revisions").fetchone()[0])
        if head is None:
            return
        try:
            self._graph_doc = await self._doc_at(head)
            self.graph.revision = head
        except (ValueError, OSError, BlobCorruptedError):
            _LOGGER.warning("Graph history unreadable; starting a new checkpoint")
            self._graph_doc = None
            self.graph.revision = head

    async def _record_revision(self, graph: DependencyGraph) -> int:
        """Persist a diff (or a checkpoint) and return the revision number to use."""
        current = self.graph.revision
        doc = await self.hass.async_add_executor_job(versions.to_doc, graph)
        change = versions.diff(self._graph_doc, doc) if self._graph_doc is not None else None
        if change == {}:
            return current  # identical graph keeps its revision (stable ranking, AC COR 03)
        if not self.rt.storage_writable:
            self._graph_doc = doc
            return current + 1
        revision = current + 1
        full = change is None or revision % versions.CHECKPOINT_EVERY == 1
        payload = {"kind": "full", **doc} if full else {"kind": "diff", "diff": change}
        now = self.rt.clock.now_us()
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

        def save(conn):
            ref = self.blobs.put(data)
            with _tx(conn):
                conn.execute("INSERT OR REPLACE INTO graph_revisions(revision, created_at_us, "
                             "diff_blob_ref) VALUES (?,?,?)", (revision, now, ref))
                # Keep KEEP_REVISIONS, but never cut a chain: start at a checkpoint.
                floor = revision - versions.KEEP_REVISIONS
                first = floor - (floor - 1) % versions.CHECKPOINT_EVERY
                conn.execute("DELETE FROM graph_revisions WHERE revision < ?", (first,))

        await self.rt.db.run(save)
        self._graph_doc = doc
        self._old_graphs.clear()
        return revision

    async def _doc_at(self, revision: int) -> dict[str, Any]:
        def load(conn):
            rows = conn.execute(
                "SELECT revision, diff_blob_ref FROM graph_revisions WHERE revision <= ? AND "
                "revision >= COALESCE((SELECT MAX(revision) FROM graph_revisions WHERE "
                "revision <= ? AND (revision - 1) % ? = 0), 1) ORDER BY revision",
                (revision, revision, versions.CHECKPOINT_EVERY)).fetchall()
            if not rows or rows[-1][0] != revision:
                raise SentinelError(ErrorCode.NOT_FOUND, "graph revision not found")
            return [json.loads(self.blobs.get(r[1])) for r in rows]

        chain = await self.rt.db.run(load)
        return await self.hass.async_add_executor_job(versions.rebuild, chain)

    async def graph_at(self, revision: int) -> DependencyGraph:
        if revision == self.graph.revision:
            return self.graph
        if (cached := self._old_graphs.get(revision)) is not None:
            return cached
        doc = await self._doc_at(revision)
        graph = await self.hass.async_add_executor_job(versions.from_doc, doc, revision)
        if len(self._old_graphs) >= 2:
            self._old_graphs.pop(next(iter(self._old_graphs)))
        self._old_graphs[revision] = graph
        return graph

    async def list_revisions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.rt.db.run(lambda c: [tuple(r) for r in c.execute(
            "SELECT revision, created_at_us FROM graph_revisions ORDER BY revision DESC "
            "LIMIT ?", (limit,))])
        return [{"revision": r, "created_at": us_to_rfc3339(t)} for r, t in rows]

    # --- DOC006 missing / stale references (spec 8.1, 22.1) ------------------------------

    def restore(self, findings: list[Finding]) -> list[Finding]:
        """Take over restored DOC006 findings; returns the ones this module does not own."""
        rest = []
        for f in findings:
            if f.rule_id == "DOC006" and f.subject_uid.startswith("resource:"):
                self.ref_findings[f.subject_uid] = f
            else:
                rest.append(f)
        return rest

    async def _check_references(self, refs: list[ImportRefs], snap: RegistrySnapshot) -> None:
        if not self.rt.modules_enabled["doctor"] or not self.rt.storage_writable:
            return
        complete = self.rt.state.value == "ready" and not self.rt.queues.degraded
        if not complete:
            return  # never close or raise on a partial inventory
        now = self.rt.clock.now_us()
        deleted = {e.current_alias: e.deleted_at_us for e in self.rt.inventory.all_entities()
                   if e.deleted_at_us is not None}
        issues = evaluate(refs, snap.entity_status, deleted, now, inventory_complete=True)
        changed: list[Finding] = []
        for rid, issue in issues.items():
            uid = f"resource:{rid}"
            severity = Severity.WARNING if issue.missing else Severity.INFO
            current = self.ref_findings.get(uid)
            if current is not None and current.severity is severity \
                    and current.details == issue.details:
                continue
            f = Finding(id=current.id if current else new_uuid(), rule_id="DOC006",
                        rule_version=1, subject_uid=uid, status=FindingStatus.ACTIVE,
                        severity=severity,
                        first_seen_us=current.first_seen_us if current else now,
                        last_seen_us=now, coverage=CoverageStatus.COMPLETE,
                        details=issue.details)
            self.ref_findings[uid] = f
            changed.append(f)
        for uid in [u for u in self.ref_findings if u.removeprefix("resource:") not in issues]:
            f = self.ref_findings.pop(uid)
            f.status = FindingStatus.RESOLVED
            f.resolved_at_us = f.last_seen_us = now
            changed.append(f)
        await self.rt.commit_findings(changed)

    def resource_label(self, uid: str) -> str | None:
        f = self.ref_findings.get(uid)
        return f.details.get("resource_label") if f else None

    # --- Config history ------------------------------------------------------------------

    def sentinel_config_tree(self) -> dict[str, Any]:
        """Type S content: options plus per-object rule settings (spec 10.1, 27.1).

        Subjects are keyed by UID; the alias at snapshot time is kept only as a label so
        a diff stays readable after a rename.
        """
        def label(uid: str) -> str | None:
            ent = self.rt.inventory.get(uid)
            return ent.current_alias if ent else None

        return {
            "options": {k: self.rt.entry.options[k] for k in sorted(self.rt.entry.options)},
            "rule_overrides": [
                {"rule_id": o.rule_id, "subject_uid": o.subject_uid,
                 "subject_label": label(o.subject_uid), "enabled": o.enabled,
                 "params": dict(sorted(o.params.items()))}
                for o in sorted(self.rt.rules.overrides, key=lambda o: o.key)],
            "numeric_sources": [
                {"purpose": n.purpose, "subject_uid": n.subject_uid,
                 "subject_label": label(n.subject_uid), "scale_min": n.scale_min,
                 "scale_max": n.scale_max, "threshold": n.threshold}
                for n in sorted(self.rt.numeric.sources,
                                key=lambda n: (n.purpose, n.subject_uid))],
            "unit_weights": dict(sorted(self.rt.unit_weights.items())),
        }

    async def snapshot_sentinel_config(self, actor: str | None = None,
                                       request_id: str = "startup") -> None:
        """Snapshot after every change of options or rule settings; identical content is
        deduplicated by the history repository."""
        if not self.rt.storage_writable or not self.rt.modules_enabled["config_history"]:
            return
        tree = self.sentinel_config_tree()
        now = self.rt.clock.now_us()

        def save(conn):
            rid = history.find_resource(conn, "sentinel", "sentinel_options")
            return history.save_snapshot_tx(
                conn, self.blobs, resource_id=rid, provider="sentinel", kind="sentinel_options",
                label="HA Sentinel configuration", canonical_tree=tree,
                canonical_hash=canonical_hash(tree), raw=None, raw_hash=None,
                redacted_paths=[], validation={"parser": "n/a"}, actor_id=actor,
                request_id=request_id, now_us=now,
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
