"""Sentinel WebSocket commands (spec 15). Every handler requires an admin (ADR 05, T08).

Handlers are registered once per domain and answer `not_ready` while no entry is loaded.
Mutations need a `request_key` UUID and an `expected_revision`; the same key with the same
content returns the stored result.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import API_VERSION, DOMAIN
from .core.clock import datetime_to_us, us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.models import CapabilityStatus, ModuleState, is_uuid4, new_uuid
from .core.ratelimit import RequestClass
from .modules.doctor.overrides import PARAM_RANGES
from .repositories import history, jobs
from .repositories import repository as repo

_LOGGER = logging.getLogger(__name__)
_REGISTERED = f"{DOMAIN}_ws_registered"
MAX_SUBSCRIPTIONS_PER_USER = 5
MAX_SUBSCRIPTIONS_TOTAL = 10


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or not is_uuid4(value):
        raise vol.Invalid("expected UUID v4")
    return value


def _schema(command: str, **fields: Any) -> dict:
    return {
        vol.Required("type"): f"{DOMAIN}/{command}",
        vol.Optional("api_version", default=API_VERSION): vol.All(int, vol.In([API_VERSION])),
        **fields,
    }


@callback
def async_register_api(hass: HomeAssistant) -> None:
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    for handler in (
        ws_capabilities,
        ws_overview,
        ws_diagnostics,
        ws_incidents_list,
        ws_incidents_get,
        ws_incidents_ack,
        ws_incidents_suppress,
        ws_incidents_confirm_cause,
        ws_subscribe,
        ws_import_create,
        ws_resources_list,
        ws_snapshots_list,
        ws_snapshots_get,
        ws_snapshots_diff,
        ws_autopilot_analyze,
        ws_graph_query,
        ws_graph_search,
        ws_export_create,
        ws_jobs_get,
        ws_jobs_cancel,
        ws_data_status,
        ws_data_delete,
        ws_managed_register,
        ws_managed_sync,
        ws_changes_preview,
        ws_changes_apply,
        ws_changes_get,
        ws_network_inventory,
        ws_network_targets_list,
        ws_network_targets_add,
        ws_network_targets_update,
        ws_network_targets_delete,
        ws_network_scan,
        ws_autopilot_replay,
        ws_maintenance_list,
        ws_maintenance_create,
        ws_maintenance_cancel,
        ws_rules_list,
        ws_rules_set,
        ws_rules_delete,
        ws_modules_resume,
    ):
        websocket_api.async_register_command(hass, handler)


def _require_admin(func: Callable) -> Callable:
    """Reject non-admins with the Sentinel code `forbidden` (spec 15.2, 16.2).

    Runs before the handler body, so nothing about the requested object is revealed.
    """

    @callback
    @wraps(func)
    def wrapper(hass: HomeAssistant, connection, msg: dict) -> None:
        user = connection.user
        if user is None or not user.is_active or not user.is_admin:
            connection.send_error(msg["id"], str(ErrorCode.FORBIDDEN), "Admin access required")
            return
        func(hass, connection, msg)

    return wrapper


def _runtime(hass: HomeAssistant):
    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime is None:
        raise SentinelError(ErrorCode.NOT_READY, "HA Sentinel is not loaded")
    return runtime


def _guarded(cls: RequestClass) -> Callable:
    """Resolve the runtime, apply the rate limit and translate domain errors."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(hass: HomeAssistant, connection, msg: dict) -> None:
            started = time.perf_counter()
            runtime = None
            try:
                runtime = _runtime(hass)
                if not runtime.rate_limiter.allow(connection.user.id, cls):
                    raise SentinelError(ErrorCode.RATE_LIMITED, "too many requests")
                await func(hass, connection, msg, runtime)
            except SentinelError as err:
                error: dict[str, Any] = {"code": str(err.code), "message": str(err)}
                if err.details:
                    error["details"] = err.details  # only safe, allowlisted fields
                connection.send_message(
                    {"id": msg["id"], "type": "result", "success": False, "error": error})
            except Exception:
                request_id = new_uuid()
                _LOGGER.exception("Sentinel API failure request_id=%s", request_id)
                connection.send_error(msg["id"], str(ErrorCode.INTERNAL_ERROR),
                                      f"internal error, request_id={request_id}")
            finally:
                if runtime is not None:
                    runtime.metrics.observe("api_latency_ms",
                                            (time.perf_counter() - started) * 1000)

        return wrapper

    return decorator


# --- Queries ------------------------------------------------------------------------


@websocket_api.websocket_command(_schema("capabilities"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_capabilities(hass, connection, msg, runtime) -> None:
    connection.send_result(
        msg["id"],
        {"api_version": API_VERSION,
         "capabilities": [c.as_dict() for c in runtime.capabilities.values()]},
    )


@websocket_api.websocket_command(_schema("overview"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_overview(hass, connection, msg, runtime) -> None:
    connection.send_result(msg["id"], runtime.overview())


@websocket_api.websocket_command(_schema("diagnostics"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_diagnostics(hass, connection, msg, runtime) -> None:
    diag = await runtime.diagnostics()
    diag["started_at"] = us_to_rfc3339(diag.pop("started_at_us"))
    deadline = diag["doctor"].pop("next_deadline_us")
    diag["doctor"]["next_deadline"] = us_to_rfc3339(deadline) if deadline else None
    diag["coverage_gaps"] = [
        {**g, "from": us_to_rfc3339(g.pop("from_us")),
         "to": us_to_rfc3339(t) if (t := g.pop("to_us")) else None}
        for g in diag["coverage_gaps"]
    ]
    connection.send_result(msg["id"], diag)


def _encode_cursor(user_id: str, active_only: bool, row: dict) -> str:
    raw = json.dumps({"u": user_id, "a": active_only, "t": row["updated_at_us"], "i": row["id"]})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str, user_id: str, active_only: bool) -> tuple[int, str]:
    """Opaque cursor bound to the user and filters; never raw SQL (spec 15.3)."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if data["u"] != user_id or data["a"] != active_only:
            raise ValueError
        return int(data["t"]), str(data["i"])
    except (ValueError, KeyError, TypeError) as err:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "invalid cursor") from err


def _incident_api(runtime, row: dict) -> dict:
    summary = row["summary"]
    return {
        "id": row["id"],
        "status": row["status"],
        "severity": row["severity"],
        "title_key": summary.get("title_key"),
        "kind": "infrastructure" if summary.get("root_uid") else "rule",
        "rule_id": summary.get("title_key", "").removeprefix("rule."),
        "member_count": summary.get("member_count", len(summary.get("finding_ids", []))),
        "root": (runtime.node_label(summary["root_uid"]) if summary.get("root_uid") else None),
        "merged_into": summary.get("merged_into"),
        "top_hypothesis": (
            {k: h[k] for k in ("candidate_label", "label", "score")}
            if (h := next(iter(summary.get("hypotheses") or []), None)) else None
        ),
        "subject": runtime.subject_label(summary.get("subject_uid")),
        "opened_at": us_to_rfc3339(row["opened_at_us"]),
        "updated_at": us_to_rfc3339(row["updated_at_us"]),
        "resolved_at": us_to_rfc3339(r) if (r := row["resolved_at_us"]) else None,
        "revision": row["revision"],
        "suppressed_until": us_to_rfc3339(s) if (s := row["suppressed_until_us"]) else None,
        "suppress_reason": row["suppress_reason"],
        "acknowledged_at": us_to_rfc3339(a) if (a := row["acknowledged_at_us"]) else None,
    }


@websocket_api.websocket_command(
    _schema("incidents/list")
    | {
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=100)),
        vol.Optional("active_only", default=True): bool,
        vol.Optional("cursor"): str,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_incidents_list(hass, connection, msg, runtime) -> None:
    user_id = connection.user.id
    active_only = msg["active_only"]
    after = _decode_cursor(msg["cursor"], user_id, active_only) if "cursor" in msg else None
    limit = msg["limit"]
    rows = await runtime.db.run(lambda c: repo.list_incidents(c, limit + 1, active_only, after))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_cursor(user_id, active_only, rows[-1])
    connection.send_result(
        msg["id"],
        {"items": [_incident_api(runtime, r) for r in rows],
         "next_cursor": next_cursor, "revision": runtime.revision},
    )


@websocket_api.websocket_command(
    _schema("incidents/get") | {vol.Required("incident_id"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_incidents_get(hass, connection, msg, runtime) -> None:
    row = await runtime.db.run(lambda c: repo.get_incident(c, msg["incident_id"]))
    if row is None:
        raise SentinelError(ErrorCode.NOT_FOUND, "incident not found")
    out = _incident_api(runtime, row)
    out["findings"] = [
        {
            "id": f["id"],
            "rule_id": f["rule_id"],
            "rule_version": f["rule_version"],
            "status": f["status"],
            "severity": f["severity"],
            "confidence": f["confidence"],
            "coverage": f["coverage"],
            "subject": runtime.subject_label(f["subject_uid"]),
            "first_seen": us_to_rfc3339(f["first_us"]),
            "last_seen": us_to_rfc3339(f["last_us"]),
            "resolved_at": us_to_rfc3339(r) if (r := f["resolved_us"]) else None,
            "evidence": [
                {
                    "kind": e["kind"],
                    "received_at": us_to_rfc3339(t) if (t := e["received_at_us"]) else None,
                    "occurred_at": us_to_rfc3339(o) if (o := e["occurred_at_us"]) else None,
                    "payload": e["payload"],
                    "evidence_expired": e["evidence_expired"],
                }
                for e in f["evidence"]
            ],
        }
        for f in row["findings"]
    ]
    summary = row["summary"]
    # Ranking is a heuristic, not a probability; confirmation is a separate admin field.
    out["hypotheses"] = summary.get("hypotheses", [])
    out["counter_evidence"] = [
        {**c, "candidate_uid": h["candidate_uid"]}
        for h in out["hypotheses"] for c in h.get("counter_evidence", [])
    ]
    cause = summary.get("confirmed_cause")
    out["confirmed_cause"] = (
        {**{k: v for k, v in cause.items() if k != "at_us"}, "at": us_to_rfc3339(cause["at_us"])}
        if cause else None
    )
    connection.send_result(msg["id"], out)


# --- Mutations ------------------------------------------------------------------------


def _request_hash(msg: dict) -> str:
    body = {k: v for k, v in msg.items() if k not in ("id", "request_key")}
    return "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _mutate(runtime, connection, msg: dict, kind: str, op: Callable) -> dict:
    if not runtime.storage_writable:
        raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE, "storage is read-only")
    now = runtime.clock.now_us()
    request_hash = _request_hash(msg)
    actor = connection.user.id
    request_id = new_uuid()
    result = await runtime.db.run(
        lambda c: repo.run_idempotent(
            c, request_key=msg["request_key"], request_hash=request_hash, actor_id=actor,
            kind=kind, now_us=now, op=lambda conn: op(conn, actor, request_id, now),
        )
    )
    runtime.bump()
    return result


_MUTATION_FIELDS = {
    vol.Required("incident_id"): _uuid,
    vol.Required("expected_revision"): vol.All(int, vol.Range(min=1)),
    vol.Required("request_key"): _uuid,
}


@websocket_api.websocket_command(_schema("incidents/ack") | _MUTATION_FIELDS)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_incidents_ack(hass, connection, msg, runtime) -> None:
    result = await _mutate(
        runtime, connection, msg, "incident.ack",
        lambda c, actor, rid, now: repo.acknowledge_incident(
            c, msg["incident_id"], msg["expected_revision"], actor, rid, now),
    )
    connection.send_result(msg["id"], result)


def _rfc3339(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as err:
        raise vol.Invalid("expected RFC3339 date-time") from err
    if dt.tzinfo is None:
        raise vol.Invalid("timezone offset required")
    return dt


@websocket_api.websocket_command(
    _schema("incidents/suppress")
    | _MUTATION_FIELDS
    | {
        vol.Required("until"): _rfc3339,
        vol.Required("reason"): vol.All(str, vol.Length(min=1, max=200)),
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_incidents_suppress(hass, connection, msg, runtime) -> None:
    until_us = datetime_to_us(msg["until"])
    msg = {**msg, "until": us_to_rfc3339(until_us)}  # canonical form for the request hash
    result = await _mutate(
        runtime, connection, msg, "incident.suppress",
        lambda c, actor, rid, now: repo.suppress_incident(
            c, msg["incident_id"], msg["expected_revision"], until_us, msg["reason"],
            actor, rid, now),
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("incidents/confirm_cause")
    | _MUTATION_FIELDS
    | {
        vol.Required("candidate_uid"): vol.Any(None, vol.All(str, vol.Length(min=1, max=300))),
        vol.Required("verdict"): vol.In(["confirmed", "rejected"]),
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_incidents_confirm_cause(hass, connection, msg, runtime) -> None:
    """Manual admin assessment of a hypothesis; never changes the ranking (spec 9.2)."""
    result = await _mutate(
        runtime, connection, msg, "incident.cause",
        lambda c, actor, rid, now: repo.confirm_cause(
            c, msg["incident_id"], msg["expected_revision"], msg["candidate_uid"],
            msg["verdict"], actor, rid, now),
    )
    connection.send_result(msg["id"], result)


# --- Subscription ------------------------------------------------------------------------


@websocket_api.websocket_command(_schema("subscribe"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_subscribe(hass, connection, msg, runtime) -> None:
    """Change notifications; MVP sends revisions and clients refetch (resync semantics)."""
    user_id = connection.user.id
    if (runtime.subscriptions.get(user_id, 0) >= MAX_SUBSCRIPTIONS_PER_USER
            or sum(runtime.subscriptions.values()) >= MAX_SUBSCRIPTIONS_TOTAL):
        raise SentinelError(ErrorCode.RATE_LIMITED, "too many subscriptions")
    runtime.subscriptions[user_id] = runtime.subscriptions.get(user_id, 0) + 1
    last_sent = runtime.revision

    @callback
    def forward() -> None:
        nonlocal last_sent
        if runtime.revision == last_sent:
            return
        connection.send_message(websocket_api.event_message(msg["id"], {
            "stream_epoch": runtime.boot_id,
            "from_revision": last_sent,
            "to_revision": runtime.revision,
            "resync_required": True,
        }))
        last_sent = runtime.revision

    unsub_signal = async_dispatcher_connect(hass, f"{DOMAIN}_updated", forward)

    @callback
    def unsubscribe() -> None:
        unsub_signal()
        runtime.subscriptions[user_id] = max(0, runtime.subscriptions.get(user_id, 1) - 1)

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"], {"stream_epoch": runtime.boot_id,
                                       "revision": runtime.revision})


# --- Configuration history, autopilot, graph (E3) ---------------------------------------


def _config(runtime):
    cfg = runtime.config
    if cfg is None or runtime.module_states.get("config_history") is not ModuleState.READY:
        raise SentinelError(ErrorCode.NOT_READY, "configuration modules are not ready")
    return cfg


_KINDS = vol.In(["automation", "script", "blueprint"])


@websocket_api.websocket_command(
    _schema("import/create")
    | {
        vol.Required("content_utf8"): vol.All(str, vol.Length(min=1, max=1024 * 1024)),
        vol.Required("content_hash"): vol.Match(r"^sha256:[a-f0-9]{64}$"),
        vol.Required("resource_kind"): _KINDS,
        vol.Required("label"): vol.All(str, vol.Length(min=1, max=120)),
        vol.Optional("resource_id"): _uuid,
        vol.Optional("redact_secrets", default=False): bool,
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_import_create(hass, connection, msg, runtime) -> None:
    """Import YAML as an export-only snapshot (type I). Never writes to HA configuration."""
    if runtime.capabilities["automation.import"].status is not CapabilityStatus.SUPPORTED:
        raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "import is unavailable")
    cfg = _config(runtime)
    prepared = await cfg.prepare(msg["content_utf8"], msg["content_hash"],
                                 msg["resource_kind"], msg["redact_secrets"])
    # The request hash covers the content hash, not the 1 MiB body itself.
    hashed = {k: v for k, v in msg.items() if k != "content_utf8"}
    result = await _mutate(
        runtime, connection, hashed, "import.create",
        lambda c, actor, rid, now: cfg.save_import_op(
            prepared, resource_id=msg.get("resource_id"), kind=msg["resource_kind"],
            label=msg["label"])(c, actor, rid, now),
    )
    cfg.schedule_graph_rebuild()
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(_schema("resources/list"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_resources_list(hass, connection, msg, runtime) -> None:
    rows = await runtime.db.run(history.list_resources)
    connection.send_result(msg["id"], {"items": [
        {"id": r["id"], "provider": r["provider"], "kind": r["kind"], "label": r["label"],
         "write_policy": r["write_policy"], "snapshots": r["snapshots"],
         "updated_at": us_to_rfc3339(u) if (u := r["updated_us"]) else None}
        for r in rows
    ]})


def _snapshot_api(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k not in ("created_us", "tree", "raw")}
    out["created_at"] = us_to_rfc3339(s["created_us"])
    return out


@websocket_api.websocket_command(
    _schema("snapshots/list")
    | {vol.Required("resource_id"): _uuid,
       vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=100))}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_snapshots_list(hass, connection, msg, runtime) -> None:
    rows = await runtime.db.run(
        lambda c: history.list_snapshots(c, msg["resource_id"], msg["limit"]))
    connection.send_result(msg["id"], {"items": [_snapshot_api(r) for r in rows]})


@websocket_api.websocket_command(_schema("snapshots/get") | {vol.Required("snapshot_id"): _uuid})
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_snapshots_get(hass, connection, msg, runtime) -> None:
    """Metadata and YAML text for preview and download (spec 10.5 AC HK 05)."""
    connection.send_result(msg["id"], await _config(runtime).export_text(msg["snapshot_id"]))


@websocket_api.websocket_command(
    _schema("snapshots/diff")
    | {vol.Required("from_snapshot_id"): _uuid, vol.Required("to_snapshot_id"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_snapshots_diff(hass, connection, msg, runtime) -> None:
    connection.send_result(
        msg["id"],
        await _config(runtime).diff(msg["from_snapshot_id"], msg["to_snapshot_id"]),
    )


@websocket_api.websocket_command(
    _schema("autopilot/analyze")
    | {vol.Required("snapshot_id"): _uuid,
       vol.Optional("ruleset_version", default=1): vol.All(int, vol.In([1]))}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_autopilot_analyze(hass, connection, msg, runtime) -> None:
    """Static lint only; never calls HA services and never executes templates (AC AUT 03)."""
    connection.send_result(msg["id"], await _config(runtime).analyze(msg["snapshot_id"]))


@websocket_api.websocket_command(
    _schema("graph/query")
    | {
        vol.Required("uid"): vol.All(str, vol.Length(min=1, max=300)),
        vol.Optional("direction", default="dependents"): vol.In(["dependents",
                                                                  "dependencies"]),
        vol.Optional("depth", default=2): vol.All(int, vol.Range(min=1, max=5)),
        vol.Optional("include_grouping", default=False): bool,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_graph_query(hass, connection, msg, runtime) -> None:
    cfg = _config(runtime)
    graph = cfg.graph
    result = await hass.async_add_executor_job(
        graph.query, msg["uid"], msg["direction"], msg["depth"], msg["include_grouping"])
    if not result["found"]:
        raise SentinelError(ErrorCode.NOT_FOUND, "node not found")
    result["built_at"] = us_to_rfc3339(cfg.graph_built_us) if cfg.graph_built_us else None
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("graph/search") | {vol.Required("q"): vol.All(str, vol.Length(min=1, max=100))}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_graph_search(hass, connection, msg, runtime) -> None:
    graph = _config(runtime).graph
    nodes = await hass.async_add_executor_job(graph.search, msg["q"], 20)
    connection.send_result(msg["id"], {
        "items": [n.as_dict() for n in nodes],
        "graph_revision": graph.revision,
        "nodes": len(graph.nodes),
        "edges": graph.edge_count,
        "unresolved": graph.unresolved_count,
    })


# --- Export, jobs, data management (B23) -----------------------------------------------


def _data(runtime):
    if runtime.data is None:
        raise SentinelError(ErrorCode.NOT_READY, "data services are not ready")
    return runtime.data


@websocket_api.websocket_command(
    _schema("export/create")
    | {
        vol.Required("scopes"): vol.All([vol.In(["diagnostics", "incidents",
                                                 "config_history"])], vol.Length(min=1),
                                        lambda v: sorted(set(v))),
        vol.Optional("redacted", default=True): bool,
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_export_create(hass, connection, msg, runtime) -> None:
    """Start an export job; the archive is downloaded via the authenticated HTTP view."""
    result = await _data(runtime).start_export(
        scopes=msg["scopes"], redacted=msg["redacted"], request_key=msg["request_key"],
        request_hash=_request_hash(msg), actor_id=connection.user.id)
    connection.send_result(msg["id"], result)


def _job_api(job: dict) -> dict:
    result = job["result"]
    if result is not None:
        result = {k: v for k, v in result.items() if k != "file"}  # no server paths
        if "expires_us" in result:
            result["expires_at"] = us_to_rfc3339(result.pop("expires_us"))
    return {
        "job_id": job["id"],
        "kind": job["kind"],
        "state": job["state"],
        "progress": job["checkpoint"].get("progress"),
        "cancel_requested": job["checkpoint"].get("cancel_requested", False),
        "error_code": job["checkpoint"].get("error_code"),
        "updated_at": us_to_rfc3339(job["updated_at_us"]),
        "result": result,
    }


@websocket_api.websocket_command(_schema("jobs/get") | {vol.Required("job_id"): _uuid})
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_jobs_get(hass, connection, msg, runtime) -> None:
    job = await runtime.db.run(lambda c: jobs.get_job(c, msg["job_id"]))
    if job is None or job["kind"].startswith(("incident.", "import.")):
        raise SentinelError(ErrorCode.NOT_FOUND, "job not found")
    connection.send_result(msg["id"], _job_api(job))


@websocket_api.websocket_command(
    _schema("jobs/cancel") | {vol.Required("job_id"): _uuid, vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_jobs_cancel(hass, connection, msg, runtime) -> None:
    now = runtime.clock.now_us()
    state = await runtime.db.run(lambda c: jobs.request_cancel(c, msg["job_id"], now))
    connection.send_result(msg["id"], {"cancel_requested": True, "state": state})


@websocket_api.websocket_command(_schema("data/status"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_data_status(hass, connection, msg, runtime) -> None:
    from .const import DEFAULT_OPTIONS

    data = _data(runtime)
    stats = await runtime.db.run(repo.storage_stats)
    opts = {**DEFAULT_OPTIONS, **runtime.entry.options}
    last = data.last_purge
    connection.send_result(msg["id"], {
        "storage": stats,
        "retention": {k: v for k, v in opts.items() if k.startswith(("retention_", "storage_"))},
        "last_purge": ({**{k: v for k, v in last.items() if k != "at_us"},
                        "at": us_to_rfc3339(last["at_us"])} if last else None),
    })


@websocket_api.websocket_command(
    _schema("data/delete")
    | {
        vol.Required("scope"): vol.In(["observations", "incidents", "config_history", "all"]),
        vol.Required("confirm"): str,
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_data_delete(hass, connection, msg, runtime) -> None:
    """Delete Sentinel's own data only; Recorder and HA backups are never touched."""
    if msg["confirm"] != msg["scope"]:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "confirmation does not match scope")
    if not runtime.storage_writable:
        raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE, "storage is read-only")
    result = await _data(runtime).delete_scope(msg["scope"], connection.user.id)
    connection.send_result(msg["id"], result)


# --- Managed files: register, preview, apply (B20) ------------------------------------------


def _changes(runtime):
    if runtime.changes is None:
        raise SentinelError(ErrorCode.NOT_READY, "change service is not ready")
    return runtime.changes


@websocket_api.websocket_command(
    _schema("managed/register")
    | {
        vol.Required("relative_path"): vol.All(str, vol.Length(min=1, max=255)),
        vol.Required("label"): vol.All(str, vol.Length(min=1, max=120)),
        vol.Required("exclusive_owner_confirmed"): bool,
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_managed_register(hass, connection, msg, runtime) -> None:
    """Register a file Sentinel exclusively owns. It never edits configuration.yaml."""
    result = await _changes(runtime).register(
        msg["relative_path"], msg["label"], msg["exclusive_owner_confirmed"],
        connection.user.id, msg["request_key"])
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("managed/sync") | {vol.Required("resource_id"): _uuid,
                               vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_managed_sync(hass, connection, msg, runtime) -> None:
    result = await _changes(runtime).sync(msg["resource_id"], connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("changes/preview")
    | {vol.Required("resource_id"): _uuid, vol.Required("snapshot_id"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_changes_preview(hass, connection, msg, runtime) -> None:
    """Create a plan with diff, validation, impact and a 10 minute TTL (spec 10.4)."""
    result = await _changes(runtime).preview(msg["resource_id"], msg["snapshot_id"],
                                             connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("changes/apply")
    | {
        vol.Required("plan_id"): _uuid,
        vol.Required("plan_hash"): vol.Match(r"^sha256:[a-f0-9]{64}$"),
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_changes_apply(hass, connection, msg, runtime) -> None:
    result = await _changes(runtime).start_apply(
        msg["plan_id"], msg["plan_hash"], msg["request_key"], _request_hash(msg),
        connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(_schema("changes/get") | {vol.Required("plan_id"): _uuid})
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_changes_get(hass, connection, msg, runtime) -> None:
    plan = await _changes(runtime)._load_plan(msg["plan_id"])
    report = plan["report"]
    connection.send_result(msg["id"], {
        "plan_id": plan["id"], "resource_id": plan["resource_id"], "state": plan["state"],
        "plan_hash": plan["plan_hash"], "base_hash": plan["base_hash"],
        "target_hash": plan["target_hash"], "expires_at": us_to_rfc3339(plan["expires_us"]),
        "detail": report.get("result_detail"), "validation": report.get("validation"),
        "impact": report.get("impact"),
    })


# --- Network discovery (B21) --------------------------------------------------------------


def _network(runtime):
    if runtime.network is None:
        raise SentinelError(ErrorCode.NOT_READY, "network module is not ready")
    return runtime.network


@websocket_api.websocket_command(_schema("network/inventory"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_network_inventory(hass, connection, msg, runtime) -> None:
    """Level N0: devices HA already knows. Never probes anything."""
    net = _network(runtime)
    status, reason = net.capability
    connection.send_result(msg["id"], {
        "level": "N1" if status is CapabilityStatus.SUPPORTED else "N0",
        "probe_capability": {"status": str(status), "reason_code": reason},
        "devices": net.inventory(),
    })


@websocket_api.websocket_command(_schema("network/targets/list"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_network_targets_list(hass, connection, msg, runtime) -> None:
    net = _network(runtime)
    connection.send_result(msg["id"], {
        "items": [net.target_api(t) for t in net.targets.values()],
        "allowlist": [str(n) for n in net.allowlist],
        "interval_s": net.interval_s,
        "active": net.active,
    })


@websocket_api.websocket_command(
    _schema("network/targets/add")
    | {
        vol.Required("label"): vol.All(str, vol.Length(min=1, max=80)),
        vol.Required("host"): vol.All(str, vol.Length(min=1, max=253)),
        vol.Required("port"): vol.All(int, vol.Range(min=1, max=65535)),
        vol.Required("kind"): vol.In(["tcp", "http", "https"]),
        vol.Optional("path", default="/"): vol.All(str, vol.Length(max=200)),
        vol.Optional("allow_loopback", default=False): bool,
        vol.Optional("linked_device_id"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_network_targets_add(hass, connection, msg, runtime) -> None:
    """Adding a target is the admin's explicit approval to probe it (spec 13.1)."""
    result = await _network(runtime).add_target(
        label=msg["label"], host=msg["host"], port=msg["port"], kind=msg["kind"],
        path=msg["path"], allow_loopback=msg["allow_loopback"],
        linked_device_id=msg.get("linked_device_id"), actor=connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("network/targets/update")
    | {
        vol.Required("target_id"): _uuid,
        vol.Optional("enabled"): bool,
        vol.Optional("linked_device_id"): vol.Any(None, vol.All(str, vol.Length(max=64))),
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_network_targets_update(hass, connection, msg, runtime) -> None:
    clear = "linked_device_id" in msg and msg["linked_device_id"] is None
    result = await _network(runtime).update_target(
        msg["target_id"], enabled=msg.get("enabled"),
        linked_device_id=msg.get("linked_device_id"), clear_link=clear,
        actor=connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("network/targets/delete")
    | {vol.Required("target_id"): _uuid, vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_network_targets_delete(hass, connection, msg, runtime) -> None:
    await _network(runtime).delete_target(msg["target_id"], connection.user.id)
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.websocket_command(
    _schema("network/scan")
    | {vol.Optional("target_ids"): [_uuid], vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_network_scan(hass, connection, msg, runtime) -> None:
    """Manual scan of approved targets only; shares limits with periodic probes."""
    result = await _network(runtime).start_scan(
        msg["request_key"], _request_hash(msg), connection.user.id, msg.get("target_ids"))
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("autopilot/replay")
    | {vol.Required("snapshot_id"): _uuid,
       vol.Optional("hours", default=6): vol.All(int, vol.Range(min=1, max=24))}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_autopilot_replay(hass, connection, msg, runtime) -> None:
    """Replay recorded state changes through the stored automation AST (spec 11.4).

    Never calls automation.trigger or any other service (AC AUT 03, T10)."""
    connection.send_result(msg["id"], await _config(runtime).replay(msg["snapshot_id"],
                                                                    msg["hours"]))


# --- Maintenance windows (spec 8.2) ----------------------------------------------------


@websocket_api.websocket_command(_schema("maintenance/list"))
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_maintenance_list(hass, connection, msg, runtime) -> None:
    await runtime.maintenance.async_load()
    connection.send_result(msg["id"], {
        "items": [runtime.maintenance.window_api(w) for w in runtime.maintenance.windows]})


@websocket_api.websocket_command(
    _schema("maintenance/create")
    | {
        vol.Required("scope_uid"): vol.All(str, vol.Length(min=1, max=300)),
        vol.Optional("label", default=""): vol.All(str, vol.Length(max=120)),
        vol.Required("minutes"): vol.All(int, vol.Range(min=1, max=7 * 24 * 60)),
        vol.Required("reason"): vol.All(str, vol.Length(min=1, max=200)),
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_maintenance_create(hass, connection, msg, runtime) -> None:
    """Planned maintenance: no new failures for the scope, excluded from the score."""
    if not runtime.storage_writable:
        raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE, "storage is read-only")
    result = await runtime.maintenance.create(
        scope_uid=msg["scope_uid"], label=msg["label"], minutes=msg["minutes"],
        reason=msg["reason"], actor=connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("maintenance/cancel") | {vol.Required("window_id"): _uuid,
                                     vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_maintenance_cancel(hass, connection, msg, runtime) -> None:
    await runtime.maintenance.cancel(msg["window_id"], connection.user.id)
    connection.send_result(msg["id"], {"cancelled": True})


@websocket_api.websocket_command(
    _schema("rules/list") | {vol.Optional("subject_uid"): vol.All(str, vol.Length(max=300))}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.QUERY)
async def ws_rules_list(hass, connection, msg, runtime) -> None:
    """Per-object rule overrides; DOC005 entries are the heartbeat contracts."""
    await runtime.rules.async_load()
    items = [o for o in runtime.rules.overrides
             if "subject_uid" not in msg or o.subject_uid == msg["subject_uid"]]
    connection.send_result(msg["id"], {
        "items": [runtime.rules.override_api(o) for o in items],
        "ranges": {rule: {k: list(v) for k, v in params.items()}
                   for rule, params in PARAM_RANGES.items()},
    })


@websocket_api.websocket_command(
    _schema("rules/set")
    | {
        vol.Required("rule_id"): vol.In(list(PARAM_RANGES)),
        vol.Required("subject_uid"): vol.All(str, vol.Length(min=1, max=300)),
        vol.Required("enabled"): bool,
        vol.Optional("params", default={}): vol.Schema({vol.All(str, vol.Length(max=40)): int}),
        vol.Required("request_key"): _uuid,
    }
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_rules_set(hass, connection, msg, runtime) -> None:
    if not runtime.storage_writable:
        raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE, "storage is read-only")
    result = await runtime.rules.set(rule_id=msg["rule_id"], subject_uid=msg["subject_uid"],
                                     enabled=msg["enabled"], params=msg["params"],
                                     actor=connection.user.id)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    _schema("rules/delete") | {vol.Required("override_id"): _uuid,
                               vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_rules_delete(hass, connection, msg, runtime) -> None:
    if not runtime.storage_writable:
        raise SentinelError(ErrorCode.STORAGE_UNAVAILABLE, "storage is read-only")
    await runtime.rules.delete(msg["override_id"], connection.user.id)
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.websocket_command(
    _schema("modules/resume") | {vol.Required("module"): vol.In(["writer"]),
                                 vol.Required("request_key"): _uuid}
)
@_require_admin
@websocket_api.async_response
@_guarded(RequestClass.MUTATION)
async def ws_modules_resume(hass, connection, msg, runtime) -> None:
    """Restart a module paused after repeated failures (spec 4.3)."""
    if not runtime.resume_writer():
        raise SentinelError(ErrorCode.CONFLICT, "module is not paused")
    connection.send_result(msg["id"], {"resumed": True})
