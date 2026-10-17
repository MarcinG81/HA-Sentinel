"""Static analysis AUT001-AUT010 (spec 11.2-11.3). Pure logic: never executes Jinja and never
calls HA services. No findings means only that the defined rules passed, not that HA
accepts the configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...core.yaml_ast import ParsedYaml
from .model import OPPOSITE, ResourceModel, Runnable

RULESET_VERSION = 1
HIGH_RISK_DOMAINS = frozenset({"lock", "alarm_control_panel", "valve", "shell_command"})
MAX_RUNNABLES = 1000

EntityStatus = Callable[[str], tuple[str, str | None]]
"""entity_id -> (status, detail): active | renamed(new alias) | deleted | unknown."""
ServiceExists = Callable[[str, str], bool]


@dataclass(slots=True)
class LintFinding:
    rule_id: str
    level: str  # error | warning | info | risk | unsupported
    message_key: str
    pointer: str
    runnable: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    heuristic: bool = False

    def as_dict(self, parsed: ParsedYaml | None = None) -> dict[str, Any]:
        span = parsed.span(self.pointer) if parsed else None
        return {
            "rule_id": self.rule_id,
            "level": self.level,
            "message_key": self.message_key,
            "pointer": self.pointer,
            "span": span.as_dict() if span else None,
            "runnable": self.runnable,
            "params": self.params,
            "heuristic": self.heuristic,
        }


@dataclass(slots=True)
class LintReport:
    findings: list[LintFinding]
    completeness: float
    runnables: int
    templates: int
    ruleset_version: int = RULESET_VERSION
    truncated: bool = False


def lint(
    model: ResourceModel,
    *,
    entity_status: EntityStatus,
    service_exists: ServiceExists,
    inventory_complete: bool,
) -> LintReport:
    runnables = model.runnables[:MAX_RUNNABLES]
    out: list[LintFinding] = []
    for u in model.unsupported:
        out.append(LintFinding("AUT010", "unsupported", f"unsupported.{u.reason}", u.pointer))

    for r in runnables:
        out += _per_runnable(r, entity_status, service_exists, inventory_complete)
    out += _aut003_conflicts(runnables)
    out += _aut004_loops(runnables)
    out += _aut006_hysteresis(runnables)
    out += _aut009_duplicate_ids(runnables)
    out.sort(key=lambda f: (f.pointer, f.rule_id))
    return LintReport(
        findings=out,
        completeness=model.completeness,
        runnables=len(runnables),
        templates=sum(len(r.templates) for r in runnables),
        truncated=len(model.runnables) > MAX_RUNNABLES,
    )


def _per_runnable(r: Runnable, entity_status: EntityStatus, service_exists: ServiceExists,
                  inventory_complete: bool) -> list[LintFinding]:
    out: list[LintFinding] = []
    seen: set[tuple[str, str]] = set()
    for ref in r.refs:
        if ref.kind in ("entity", "scene", "script") and (ref.value, ref.pointer) not in seen:
            seen.add((ref.value, ref.pointer))
            status, detail = entity_status(ref.value)
            if status == "active":
                continue
            if status == "renamed":
                out.append(LintFinding("AUT001", "warning", "aut001.renamed", ref.pointer,
                                       r.label, {"entity_id": ref.value, "new": detail}))
            elif status == "deleted":
                out.append(LintFinding("AUT001", "warning", "aut001.deleted", ref.pointer,
                                       r.label, {"entity_id": ref.value}))
            else:
                # Error only when the inventory is complete (spec 11.2).
                level = "error" if inventory_complete else "warning"
                out.append(LintFinding("AUT001", level, "aut001.missing", ref.pointer,
                                       r.label, {"entity_id": ref.value}))
    for call in r.calls:
        if not service_exists(call.domain, call.name):
            out.append(LintFinding("AUT002", "warning", "aut002.unknown_service", call.pointer,
                                   r.label, {"service": call.service}))
        if call.domain in HIGH_RISK_DOMAINS:
            out.append(LintFinding("AUT008", "risk", "aut008.high_risk", call.pointer, r.label,
                                   {"service": call.service}))
    frequent = [t for t in r.triggers
                if t.platform == "time_pattern" or (t.platform == "state" and not t.has_for
                                                    and t.to is None)]
    if r.mode == "single" and r.has_delay_or_wait and frequent:
        out.append(LintFinding("AUT005", "warning", "aut005.single_with_delay",
                               frequent[0].pointer, r.label, {"mode": r.mode}, heuristic=True))
    for pointer in r.templates:
        out.append(LintFinding("AUT007", "info", "aut007.template", pointer, r.label))
    for u in r.unsupported:
        out.append(LintFinding("AUT010", "unsupported", f"unsupported.{u.reason}", u.pointer,
                               r.label))
    return out


def _aut003_conflicts(runnables: list[Runnable]) -> list[LintFinding]:
    """Same trigger and opposite actions on the same target: a possible conflict only."""
    out: list[LintFinding] = []
    by_sig: dict[tuple, list[tuple[Runnable, Any]]] = {}
    for r in runnables:
        for t in r.triggers:
            by_sig.setdefault(t.signature(), []).append((r, t))
    reported: set[tuple[str, str, str]] = set()
    for entries in by_sig.values():
        for i, (a, _) in enumerate(entries):
            for b, _ in entries[i + 1:]:
                if a is b:
                    continue
                for ca in a.calls:
                    for cb in b.calls:
                        if ca.domain != cb.domain or OPPOSITE.get(ca.name) != cb.name:
                            continue
                        common = set(ca.entity_ids) & set(cb.entity_ids)
                        for target in sorted(common):
                            key = tuple(sorted((a.label, b.label))) + (target,)
                            if key in reported:
                                continue
                            reported.add(key)
                            out.append(LintFinding(
                                "AUT003", "warning", "aut003.possible_conflict", ca.pointer,
                                a.label,
                                {"other": b.label, "target": target,
                                 "conditions_unknown": a.has_conditions or b.has_conditions},
                                heuristic=True,
                            ))
    return out


def _aut004_loops(runnables: list[Runnable]) -> list[LintFinding]:
    """Influence graph automation -> controlled state -> triggered automation; Tarjan SCC."""
    autos = [r for r in runnables if r.kind == "automation"]
    triggered_by: dict[str, list[int]] = {}
    for idx, r in enumerate(autos):
        for e in r.trigger_entities:
            triggered_by.setdefault(e, []).append(idx)
    edges: list[set[int]] = [set() for _ in autos]
    for idx, r in enumerate(autos):
        for e in r.controlled_entities:
            edges[idx].update(triggered_by.get(e, []))

    out: list[LintFinding] = []
    for scc in _tarjan(edges):
        if len(scc) == 1 and scc[0] not in edges[scc[0]]:
            continue
        members = [autos[i] for i in sorted(scc)]
        breakers = sorted({
            b for r in members for b in (
                ("condition" if r.has_conditions else None),
                ("mode_" + r.mode if r.mode != "single" else None),
                ("for_delay" if any(t.has_for for t in r.triggers) else None),
            ) if b
        })
        first = members[0]
        out.append(LintFinding(
            "AUT004", "warning", "aut004.potential_loop", first.pointer, first.label,
            {"cycle": [m.label for m in members], "possible_breakers": breakers},
            heuristic=True,
        ))
    return out


def _tarjan(edges: list[set[int]]) -> list[list[int]]:
    """Iterative Tarjan, O(V+E); no recursion limit issues on large imports."""
    index_of: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0
    for root in range(len(edges)):
        if root in index_of:
            continue
        work: list[tuple[int, list[int]]] = [(root, sorted(edges[root]))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                nxt = pending.pop()
                if nxt not in index_of:
                    index_of[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(edges[nxt])))
                elif nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                result.append(comp)
    return result


def _aut006_hysteresis(runnables: list[Runnable]) -> list[LintFinding]:
    """numeric_state above X -> turn_on and below X -> turn_off on the same target."""
    ons: list[tuple[Runnable, str, Any, str]] = []
    offs: list[tuple[Runnable, str, Any, str]] = []
    for r in runnables:
        for t in r.triggers:
            if t.platform != "numeric_state":
                continue
            for c in r.calls:
                for target in c.entity_ids:
                    for sensor in t.entity_ids:
                        if t.above is not None and c.name == "turn_on":
                            ons.append((r, sensor, t.above, target))
                        if t.below is not None and c.name == "turn_off":
                            offs.append((r, sensor, t.below, target))
    out: list[LintFinding] = []
    for r_on, sensor, above, target in ons:
        for r_off, sensor2, below, target2 in offs:
            if sensor == sensor2 and target == target2 and above == below:
                out.append(LintFinding(
                    "AUT006", "info", "aut006.no_hysteresis", r_on.pointer, r_on.label,
                    {"sensor": sensor, "target": target, "threshold": above,
                     "other": r_off.label},
                    heuristic=True,
                ))
    return out


def _aut009_duplicate_ids(runnables: list[Runnable]) -> list[LintFinding]:
    seen: dict[str, Runnable] = {}
    out: list[LintFinding] = []
    for r in runnables:
        if r.kind != "automation" or not r.id:
            continue
        if r.id in seen:
            out.append(LintFinding("AUT009", "error", "aut009.duplicate_id", r.pointer, r.label,
                                   {"id": r.id}))
        else:
            seen[r.id] = r
    return out
