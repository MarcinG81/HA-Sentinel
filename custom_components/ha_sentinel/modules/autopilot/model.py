"""Structural model of automations and scripts extracted from the safe AST (spec 11.1-11.2).

Only literal references are resolved; templates are never executed or guessed (AUT007).
Every fragment the extractor does not understand is reported as unsupported with its
pointer, and completeness = supported nodes / all nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.yaml_ast import escape_pointer, is_ref

KNOWN_TRIGGERS = frozenset({
    "state", "numeric_state", "time", "time_pattern", "homeassistant", "event", "sun",
    "template", "device", "zone", "geo_location", "mqtt", "webhook", "tag", "calendar",
    "conversation", "persistent_notification", "sentence",
})
KNOWN_CONDITIONS = frozenset({
    "state", "numeric_state", "and", "or", "not", "template", "time", "sun", "zone",
    "device", "trigger",
})
OPPOSITE = {
    "turn_on": "turn_off", "turn_off": "turn_on", "open_cover": "close_cover",
    "close_cover": "open_cover", "lock": "unlock", "unlock": "lock",
    "open_valve": "close_valve", "close_valve": "open_valve",
}


def is_template(value: Any) -> bool:
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass(slots=True)
class Ref:
    kind: str  # entity | device | area | script | scene | service | blueprint_input
    value: str
    pointer: str
    role: str  # trigger | condition | target | call


@dataclass(slots=True)
class Trigger:
    platform: str
    pointer: str
    entity_ids: list[str] = field(default_factory=list)
    to: Any = None
    from_: Any = None
    above: Any = None
    below: Any = None
    has_for: bool = False

    def signature(self) -> tuple:
        return (self.platform, tuple(sorted(self.entity_ids)), repr(self.to), repr(self.from_),
                repr(self.above), repr(self.below), self.has_for)


@dataclass(slots=True)
class ActionCall:
    service: str  # "domain.service"
    pointer: str
    entity_ids: list[str] = field(default_factory=list)
    dynamic_target: bool = False

    @property
    def domain(self) -> str:
        return self.service.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.service.split(".", 1)[-1]


@dataclass(slots=True)
class Unsupported:
    pointer: str
    reason: str


@dataclass(slots=True)
class Runnable:
    """An automation or a script."""

    kind: str  # automation | script
    key: str
    pointer: str
    id: str | None = None
    alias: str | None = None
    mode: str = "single"
    triggers: list[Trigger] = field(default_factory=list)
    calls: list[ActionCall] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    unsupported: list[Unsupported] = field(default_factory=list)
    has_conditions: bool = False
    has_delay_or_wait: bool = False
    blueprint: str | None = None
    total_nodes: int = 0
    supported_nodes: int = 0

    @property
    def label(self) -> str:
        return self.alias or self.id or self.key

    @property
    def controlled_entities(self) -> set[str]:
        return {e for c in self.calls for e in c.entity_ids}

    @property
    def trigger_entities(self) -> set[str]:
        return {e for t in self.triggers for e in t.entity_ids}


@dataclass(slots=True)
class ResourceModel:
    kind: str
    runnables: list[Runnable] = field(default_factory=list)
    unsupported: list[Unsupported] = field(default_factory=list)

    @property
    def completeness(self) -> float:
        total = sum(r.total_nodes for r in self.runnables)
        supported = sum(r.supported_nodes for r in self.runnables)
        return 1.0 if total == 0 else supported / total


def _p(pointer: str, key: str | int) -> str:
    return f"{pointer}/{escape_pointer(str(key))}"


def extract(tree: Any, kind: str) -> ResourceModel:
    """`kind` is automation, script or blueprint (spec 10.1 type I)."""
    model = ResourceModel(kind=kind)
    if kind == "blueprint":
        if not isinstance(tree, dict) or not isinstance(tree.get("blueprint"), dict):
            model.unsupported.append(Unsupported("", "not_a_blueprint"))
            return model
        domain = tree["blueprint"].get("domain")
        body = {k: v for k, v in tree.items() if k != "blueprint"}
        if domain == "automation":
            model.runnables.append(_automation(body, "", "blueprint"))
        elif domain == "script":
            model.runnables.append(_script(body, "", "blueprint"))
        else:
            model.unsupported.append(Unsupported("/blueprint/domain", "unknown_blueprint_domain"))
        return model

    if kind == "automation":
        if isinstance(tree, dict) and isinstance(tree.get("automation"), list):
            items = [(i, item, _p("/automation", i)) for i, item in enumerate(tree["automation"])]
        elif isinstance(tree, list):
            items = [(i, item, _p("", i)) for i, item in enumerate(tree)]
        elif isinstance(tree, dict):
            items = [(0, tree, "")]
        else:
            model.unsupported.append(Unsupported("", "not_an_automation_list"))
            return model
        for i, item, pointer in items:
            if not isinstance(item, dict):
                model.unsupported.append(Unsupported(pointer, "not_a_mapping"))
                continue
            model.runnables.append(_automation(item, pointer, str(i)))
        return model

    if kind == "script":
        if not isinstance(tree, dict):
            model.unsupported.append(Unsupported("", "not_a_script_mapping"))
            return model
        if "sequence" in tree:
            model.runnables.append(_script(tree, "", "script"))
        else:
            for name, cfg in tree.items():
                pointer = _p("", name)
                if isinstance(cfg, dict):
                    model.runnables.append(_script(cfg, pointer, name))
                else:
                    model.unsupported.append(Unsupported(pointer, "not_a_mapping"))
        return model

    model.unsupported.append(Unsupported("", "unknown_resource_kind"))
    return model


def _automation(cfg: dict, pointer: str, key: str) -> Runnable:
    r = Runnable(kind="automation", key=key, pointer=pointer,
                 id=_str(cfg.get("id")), alias=_str(cfg.get("alias")),
                 mode=_str(cfg.get("mode")) or "single")
    if "use_blueprint" in cfg:
        bp = cfg["use_blueprint"]
        r.blueprint = _str(bp.get("path")) if isinstance(bp, dict) else None
        r.total_nodes += 1
        r.unsupported.append(Unsupported(_p(pointer, "use_blueprint"), "blueprint_not_resolved"))
        return r
    triggers_key = "triggers" if "triggers" in cfg else "trigger"
    for i, trig in enumerate(as_list(cfg.get(triggers_key))):
        _trigger(r, trig, _p(_p(pointer, triggers_key), i))
    cond_key = "conditions" if "conditions" in cfg else "condition"
    conds = as_list(cfg.get(cond_key))
    r.has_conditions = bool(conds)
    for i, cond in enumerate(conds):
        _condition(r, cond, _p(_p(pointer, cond_key), i))
    actions_key = "actions" if "actions" in cfg else "action"
    _sequence(r, cfg.get(actions_key), _p(pointer, actions_key))
    return r


def _script(cfg: dict, pointer: str, key: str) -> Runnable:
    r = Runnable(kind="script", key=key, pointer=pointer, alias=_str(cfg.get("alias")),
                 mode=_str(cfg.get("mode")) or "single")
    _sequence(r, cfg.get("sequence"), _p(pointer, "sequence"))
    return r


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else (str(value) if value is not None and
                                                  not isinstance(value, (dict, list)) else None)


def _literal_ids(r: Runnable, value: Any, pointer: str, kind: str, role: str) -> list[str]:
    out: list[str] = []
    if is_ref(value):
        r.refs.append(Ref("blueprint_input", value["arg"], pointer, role))
        return out
    if is_template(value):
        r.templates.append(pointer)
        return out
    if isinstance(value, str):
        ids = [v.strip() for v in value.split(",")] if kind == "entity" else [value]
        for v in ids:
            if v:
                out.append(v)
                r.refs.append(Ref(kind, v, pointer, role))
        return out
    if isinstance(value, list):
        for i, item in enumerate(value):
            out += _literal_ids(r, item, _p(pointer, i), kind, role)
    return out


def _trigger(r: Runnable, trig: Any, pointer: str) -> None:
    r.total_nodes += 1
    if not isinstance(trig, dict):
        r.unsupported.append(Unsupported(pointer, "trigger_not_mapping"))
        return
    platform = _str(trig.get("trigger", trig.get("platform"))) or ""
    if platform not in KNOWN_TRIGGERS:
        r.unsupported.append(Unsupported(pointer, "unknown_trigger"))
        return
    t = Trigger(platform=platform, pointer=pointer, to=trig.get("to"),
                from_=trig.get("from"), above=trig.get("above"), below=trig.get("below"),
                has_for="for" in trig)
    if "entity_id" in trig:
        t.entity_ids = _literal_ids(r, trig["entity_id"], _p(pointer, "entity_id"),
                                    "entity", "trigger")
    if "device_id" in trig:
        _literal_ids(r, trig["device_id"], _p(pointer, "device_id"), "device", "trigger")
    templated = False
    for key in ("value_template", "template"):
        if is_template(trig.get(key)):
            r.templates.append(_p(pointer, key))
            templated = True
    r.triggers.append(t)
    if not templated and platform != "template":
        r.supported_nodes += 1  # a template trigger is only partially understood


def _condition(r: Runnable, cond: Any, pointer: str) -> None:
    r.total_nodes += 1
    if isinstance(cond, str):
        if is_template(cond):
            r.templates.append(pointer)  # shorthand template: not evaluated, not counted
        else:
            r.unsupported.append(Unsupported(pointer, "unknown_condition"))
        return
    if not isinstance(cond, dict):
        r.unsupported.append(Unsupported(pointer, "condition_not_mapping"))
        return
    ctype = _str(cond.get("condition")) or ""
    if ctype not in KNOWN_CONDITIONS:
        r.unsupported.append(Unsupported(pointer, "unknown_condition"))
        return
    if ctype != "template":
        r.supported_nodes += 1
    if "entity_id" in cond:
        _literal_ids(r, cond["entity_id"], _p(pointer, "entity_id"), "entity", "condition")
    if "device_id" in cond:
        _literal_ids(r, cond["device_id"], _p(pointer, "device_id"), "device", "condition")
    if is_template(cond.get("value_template")):
        r.templates.append(_p(pointer, "value_template"))
    for i, sub in enumerate(as_list(cond.get("conditions"))):
        _condition(r, sub, _p(_p(pointer, "conditions"), i))


def _sequence(r: Runnable, actions: Any, pointer: str) -> None:
    for i, action in enumerate(as_list(actions)):
        _action(r, action, _p(pointer, i))


def _action(r: Runnable, action: Any, pointer: str) -> None:
    r.total_nodes += 1
    if not isinstance(action, dict):
        r.unsupported.append(Unsupported(pointer, "action_not_mapping"))
        return
    service = action.get("action", action.get("service"))
    if service is not None:
        key = "action" if "action" in action else "service"
        if is_template(service) or not isinstance(service, str) or "." not in service:
            r.templates.append(_p(pointer, key))
            r.unsupported.append(Unsupported(pointer, "dynamic_service"))
            return
        call = ActionCall(service=service, pointer=pointer)
        r.refs.append(Ref("service", service, _p(pointer, key), "call"))
        if call.domain == "script" and call.name not in ("turn_on", "turn_off", "toggle",
                                                          "reload"):
            r.refs.append(Ref("script", f"script.{call.name}", _p(pointer, key), "call"))
        for container_key in ("target", "data"):
            container = action.get(container_key)
            if isinstance(container, dict):
                cp = _p(pointer, container_key)
                if "entity_id" in container:
                    ids = _literal_ids(r, container["entity_id"], _p(cp, "entity_id"),
                                       "entity", "target")
                    call.entity_ids += ids
                    call.dynamic_target |= is_template(container["entity_id"])
                for k, kind in (("device_id", "device"), ("area_id", "area")):
                    if k in container:
                        _literal_ids(r, container[k], _p(cp, k), kind, "target")
                        call.dynamic_target |= is_template(container[k])
            elif is_template(container):
                r.templates.append(_p(pointer, container_key))
                call.dynamic_target = True
        if "entity_id" in action:  # legacy form
            call.entity_ids += _literal_ids(r, action["entity_id"], _p(pointer, "entity_id"),
                                            "entity", "target")
        r.calls.append(call)
        if not call.dynamic_target:
            r.supported_nodes += 1  # a templated target limits completeness (spec 11.2)
        return
    if "scene" in action:
        ids = _literal_ids(r, action["scene"], _p(pointer, "scene"), "entity", "target")
        r.refs += [Ref("scene", s, _p(pointer, "scene"), "call") for s in ids]
        r.calls.append(ActionCall("scene.turn_on", pointer, ids))
        r.supported_nodes += 1
        return
    if any(k in action for k in ("delay", "wait_template", "wait_for_trigger")):
        r.has_delay_or_wait = True
        r.supported_nodes += 1
        return
    if "choose" in action:
        r.supported_nodes += 1
        for i, option in enumerate(as_list(action["choose"])):
            op = _p(_p(pointer, "choose"), i)
            if isinstance(option, dict):
                for j, cond in enumerate(as_list(option.get("conditions"))):
                    _condition(r, cond, _p(_p(op, "conditions"), j))
                _sequence(r, option.get("sequence"), _p(op, "sequence"))
        _sequence(r, action.get("default"), _p(pointer, "default"))
        return
    if "if" in action:
        r.supported_nodes += 1
        for j, cond in enumerate(as_list(action["if"])):
            _condition(r, cond, _p(_p(pointer, "if"), j))
        _sequence(r, action.get("then"), _p(pointer, "then"))
        _sequence(r, action.get("else"), _p(pointer, "else"))
        return
    if "repeat" in action and isinstance(action["repeat"], dict):
        r.supported_nodes += 1
        _sequence(r, action["repeat"].get("sequence"), _p(_p(pointer, "repeat"), "sequence"))
        return
    if "parallel" in action:
        r.supported_nodes += 1
        for i, branch in enumerate(as_list(action["parallel"])):
            bp = _p(_p(pointer, "parallel"), i)
            if isinstance(branch, dict) and "sequence" in branch and len(branch) == 1:
                _sequence(r, branch["sequence"], _p(bp, "sequence"))
            else:
                _action(r, branch, bp)
        return
    if "sequence" in action:
        r.supported_nodes += 1
        _sequence(r, action["sequence"], _p(pointer, "sequence"))
        return
    if "condition" in action:
        r.total_nodes -= 1  # counted by _condition
        _condition(r, action, pointer)
        return
    if any(k in action for k in ("event", "stop", "variables", "set_conversation_response")):
        r.supported_nodes += 1
        return
    if "device_id" in action and "domain" in action:
        _literal_ids(r, action["device_id"], _p(pointer, "device_id"), "device", "target")
        r.supported_nodes += 1
        return
    r.unsupported.append(Unsupported(pointer, "unknown_action"))
