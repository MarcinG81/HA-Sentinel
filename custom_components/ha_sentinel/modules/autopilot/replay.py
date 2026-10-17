"""Limited replay of automations against recorded state changes (spec 11.4, T10). Pure.

Works only on the stored AST and a given ordered event list. It never calls HA services
(not even automation.trigger) and never models device responses: planned actions do not
change later states. Supported: state / numeric_state triggers without templates,
state / numeric_state conditions, and/or/not, simple if and choose. delay, wait, repeat,
parallel, `for:` and templates are reported as unsupported. Missing data is UNKNOWN,
never false.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ...core.yaml_ast import escape_pointer
from .model import as_list, is_template

INTERPRETER_VERSION = 1
MAX_OCCURRENCES = 1000


class Tri(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def _and(values: list[Tri]) -> Tri:
    if any(v is Tri.FALSE for v in values):
        return Tri.FALSE
    return Tri.UNKNOWN if any(v is Tri.UNKNOWN for v in values) else Tri.TRUE


def _or(values: list[Tri]) -> Tri:
    if any(v is Tri.TRUE for v in values):
        return Tri.TRUE
    return Tri.UNKNOWN if any(v is Tri.UNKNOWN for v in values) else Tri.FALSE


def _not(value: Tri) -> Tri:
    return {Tri.TRUE: Tri.FALSE, Tri.FALSE: Tri.TRUE}.get(value, Tri.UNKNOWN)


@dataclass(frozen=True, slots=True)
class StateEvent:
    time_us: int
    entity_id: str
    new_state: str | None


@dataclass(slots=True)
class ReplayReport:
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    unsupported: list[dict[str, str]] = field(default_factory=list)
    supported_nodes: int = 0
    total_nodes: int = 0
    missing_initial: list[str] = field(default_factory=list)
    truncated: bool = False
    interpreter_version: int = INTERPRETER_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "occurrences": self.occurrences,
            "unsupported": self.unsupported,
            "supported_nodes": self.supported_nodes,
            "total_nodes": self.total_nodes,
            "missing_initial": self.missing_initial,
            "truncated": self.truncated,
            "interpreter_version": self.interpreter_version,
        }


def _p(pointer: str, key: Any) -> str:
    return f"{pointer}/{escape_pointer(str(key))}"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _in_range(value: float | None, above: float | None, below: float | None) -> Tri:
    if value is None:
        return Tri.UNKNOWN
    if above is not None and not value > above:
        return Tri.FALSE
    if below is not None and not value < below:
        return Tri.FALSE
    return Tri.TRUE


def _matches(value: str | None, expected: Any) -> bool:
    if expected is None:
        return True
    return value in [str(x) for x in as_list(expected)]


class _Automation:
    def __init__(self, cfg: dict[str, Any], pointer: str, report: ReplayReport) -> None:
        self.cfg = cfg
        self.pointer = pointer
        self.label = str(cfg.get("alias") or cfg.get("id") or pointer)
        self.report = report
        tkey = "triggers" if "triggers" in cfg else "trigger"
        self.triggers: list[tuple[int, dict[str, Any], str]] = []
        for i, trig in enumerate(as_list(cfg.get(tkey))):
            ptr = _p(_p(pointer, tkey), i)
            report.total_nodes += 1
            reason = self._trigger_unsupported(trig)
            if reason:
                self._unsupported(ptr, reason)
            else:
                report.supported_nodes += 1
                self.triggers.append((i, trig, ptr))
        self.ckey = "conditions" if "conditions" in cfg else "condition"
        self.akey = "actions" if "actions" in cfg else "action"

    def _unsupported(self, pointer: str, reason: str) -> None:
        self.report.unsupported.append({"automation": self.label, "pointer": pointer,
                                        "reason": reason})

    @staticmethod
    def _trigger_unsupported(trig: Any) -> str | None:
        if not isinstance(trig, dict):
            return "trigger_not_mapping"
        platform = trig.get("trigger", trig.get("platform"))
        if platform not in ("state", "numeric_state"):
            return f"trigger_{platform}_needs_time_or_event_model"
        if "for" in trig:
            return "for_needs_time_model"
        if "attribute" in trig or "value_template" in trig:
            return "attribute_or_template"
        if any(is_template(v) for v in as_list(trig.get("entity_id"))):
            return "template"
        if platform == "numeric_state" and any(
                trig.get(k) is not None and _num(trig.get(k)) is None for k in ("above", "below")):
            return "non_numeric_threshold"
        return None

    def fired(self, event: StateEvent, old: str | None) -> list[tuple[int, str]]:
        out = []
        for idx, trig, ptr in self.triggers:
            if event.entity_id not in [str(e) for e in as_list(trig.get("entity_id"))]:
                continue
            platform = trig.get("trigger", trig.get("platform"))
            if platform == "state":
                if old == event.new_state:
                    continue
                if _matches(old, trig.get("from")) and _matches(event.new_state, trig.get("to")):
                    out.append((idx, ptr))
            else:
                above, below = _num(trig.get("above")), _num(trig.get("below"))
                now_in = _in_range(_num(event.new_state), above, below)
                was_in = _in_range(_num(old), above, below)
                if now_in is Tri.TRUE and was_in is not Tri.TRUE:
                    out.append((idx, ptr))
        return out

    def condition(self, cond: Any, states: dict[str, str | None], pointer: str) -> Tri:
        if isinstance(cond, str) or not isinstance(cond, dict):
            self._unsupported(pointer, "template_condition")
            return Tri.UNKNOWN
        kind = cond.get("condition")
        if kind in ("and", "or", "not"):
            parts = [self.condition(c, states, _p(_p(pointer, "conditions"), i))
                     for i, c in enumerate(as_list(cond.get("conditions")))]
            if kind == "and":
                return _and(parts)
            if kind == "or":
                return _or(parts)
            return _not(_or(parts))
        if kind not in ("state", "numeric_state") or "attribute" in cond \
                or "value_template" in cond or "for" in cond:
            self._unsupported(pointer, f"condition_{kind}")
            return Tri.UNKNOWN
        results = []
        for entity in as_list(cond.get("entity_id")):
            if is_template(entity):
                self._unsupported(pointer, "template")
                return Tri.UNKNOWN
            value = states.get(str(entity))
            if value is None:
                results.append(Tri.UNKNOWN)  # no history is not "false" (spec 11.4)
            elif kind == "state":
                results.append(Tri.TRUE if _matches(value, cond.get("state")) else Tri.FALSE)
            else:
                results.append(_in_range(_num(value), _num(cond.get("above")),
                                         _num(cond.get("below"))))
        return _and(results) if results else Tri.UNKNOWN

    def plan(self, actions: Any, states: dict[str, str | None], pointer: str
             ) -> tuple[list[dict[str, Any]], bool]:
        """Planned calls; second value False when the plan stops at an unsupported step."""
        planned: list[dict[str, Any]] = []
        for i, action in enumerate(as_list(actions)):
            ptr = _p(pointer, i)
            if not isinstance(action, dict):
                self._unsupported(ptr, "action_not_mapping")
                return planned, False
            service = action.get("action", action.get("service"))
            if service is not None:
                target = action.get("target") or {}
                planned.append({"pointer": ptr, "service": str(service),
                                "target": target if isinstance(target, dict) else str(target)})
                continue
            if "choose" in action:
                branch = None
                for j, option in enumerate(as_list(action["choose"])):
                    optr = _p(_p(ptr, "choose"), j)
                    conds = [self.condition(c, states, _p(_p(optr, "conditions"), k))
                             for k, c in enumerate(as_list((option or {}).get("conditions")))]
                    result = _and(conds)
                    if result is Tri.UNKNOWN:
                        planned.append({"pointer": optr, "unknown_branch": True})
                        return planned, False
                    if result is Tri.TRUE:
                        branch = (option.get("sequence"), _p(optr, "sequence"))
                        break
                if branch is None:
                    branch = (action.get("default"), _p(ptr, "default"))
                sub, ok = self.plan(branch[0], states, branch[1])
                planned += sub
                if not ok:
                    return planned, False
                continue
            if "if" in action:
                result = _and([self.condition(c, states, _p(_p(ptr, "if"), k))
                               for k, c in enumerate(as_list(action["if"]))])
                if result is Tri.UNKNOWN:
                    planned.append({"pointer": ptr, "unknown_branch": True})
                    return planned, False
                key = "then" if result is Tri.TRUE else "else"
                sub, ok = self.plan(action.get(key), states, _p(ptr, key))
                planned += sub
                if not ok:
                    return planned, False
                continue
            if "condition" in action:
                result = self.condition(action, states, ptr)
                if result is Tri.FALSE:
                    return planned, True  # the run stops here, as in HA
                if result is Tri.UNKNOWN:
                    planned.append({"pointer": ptr, "unknown_branch": True})
                    return planned, False
                continue
            if any(k in action for k in ("delay", "wait_template", "wait_for_trigger",
                                         "repeat", "parallel")):
                self._unsupported(ptr, "needs_time_model")
                planned.append({"pointer": ptr, "stopped": "unsupported_step"})
                return planned, False
            self._unsupported(ptr, "unknown_action")
            return planned, False
        return planned, True


def replay(tree: Any, initial: dict[str, str | None], events: list[StateEvent]
           ) -> ReplayReport:
    report = ReplayReport()
    items = tree if isinstance(tree, list) else [tree] if isinstance(tree, dict) else []
    autos = [_Automation(cfg, _p("", i), report) for i, cfg in enumerate(items)
             if isinstance(cfg, dict) and "use_blueprint" not in cfg]
    referenced = {str(e) for a in autos for _, t, _ in a.triggers
                  for e in as_list(t.get("entity_id"))}
    report.missing_initial = sorted(e for e in referenced if initial.get(e) is None)
    states: dict[str, str | None] = dict(initial)
    seen_unsupported: set[tuple[str, str]] = set()
    for event in sorted(events, key=lambda e: (e.time_us, e.entity_id)):
        old = states.get(event.entity_id)
        states[event.entity_id] = event.new_state
        for auto in autos:
            for idx, ptr in auto.fired(event, old):
                if len(report.occurrences) >= MAX_OCCURRENCES:
                    report.truncated = True
                    break
                conds = [auto.condition(c, states, _p(_p(auto.pointer, auto.ckey), k))
                         for k, c in enumerate(as_list(auto.cfg.get(auto.ckey)))]
                verdict = _and(conds) if conds else Tri.TRUE
                planned, complete = ([], True)
                if verdict is not Tri.FALSE:
                    planned, complete = auto.plan(auto.cfg.get(auto.akey), states,
                                                  _p(auto.pointer, auto.akey))
                report.occurrences.append({
                    "time_us": event.time_us,
                    "automation": auto.label,
                    "trigger_pointer": ptr,
                    "trigger_index": idx,
                    "entity_id": event.entity_id,
                    "from": old,
                    "to": event.new_state,
                    "conditions": str(verdict),
                    "would_run": str(verdict),
                    "planned_actions": planned,
                    "plan_complete": complete,
                })
    # Deduplicate unsupported entries collected while re-evaluating conditions.
    unique = []
    for u in report.unsupported:
        key = (u["pointer"], u["reason"])
        if key not in seen_unsupported:
            seen_unsupported.add(key)
            unique.append(u)
    report.unsupported = unique
    return report
