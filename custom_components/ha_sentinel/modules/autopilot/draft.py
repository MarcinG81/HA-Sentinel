"""Automation draft wizard (spec 11.1, AC AUT 04). Pure logic, no HA imports.

The MVP wizard supports exactly: one state or numeric_state trigger, state conditions and
light.turn_on / light.turn_off actions on existing entities, plus mode and cooldown.
The result is YAML text that goes through the same safe parser and lint as an import;
the default outcome is an export. The wizard never installs anything into HA.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import yaml

from ...core.errors import ErrorCode, SentinelError

MODES = ("single", "restart", "queued", "parallel")
SERVICES = ("light.turn_on", "light.turn_off")
MAX_CONDITIONS = 5
MAX_ACTIONS = 5
MAX_TARGETS = 20


@dataclass(slots=True)
class DraftSpec:
    alias: str
    trigger: dict[str, Any]
    actions: list[dict[str, Any]]
    conditions: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "single"
    cooldown_s: int = 0

    def entity_ids(self) -> set[str]:
        out = {self.trigger["entity_id"]}
        out |= {c["entity_id"] for c in self.conditions}
        for a in self.actions:
            out |= set(a["entity_ids"])
        return out


def _bad(param: str, message: str) -> SentinelError:
    return SentinelError(ErrorCode.VALIDATION_FAILED, message, param=param)


def validate(spec: DraftSpec, entity_exists: Any) -> None:
    """Wizard contract checks; `entity_exists(entity_id) -> bool` checks the inventory."""
    trig = spec.trigger
    if trig.get("platform") not in ("state", "numeric_state"):
        raise _bad("trigger.platform", "only state and numeric_state triggers")
    if trig["platform"] == "numeric_state":
        above, below = trig.get("above"), trig.get("below")
        if above is None and below is None:
            raise _bad("trigger.above", "numeric_state needs above or below")
        if above is not None and below is not None and above >= below:
            raise _bad("trigger.below", "below must exceed above")
    elif trig.get("above") is not None or trig.get("below") is not None:
        raise _bad("trigger.above", "thresholds belong to numeric_state")
    if spec.mode not in MODES:
        raise _bad("mode", "unknown mode")
    if spec.cooldown_s and spec.mode != "single":
        raise _bad("cooldown_s", "a cooldown needs mode single")
    if not 1 <= len(spec.actions) <= MAX_ACTIONS:
        raise _bad("actions", "one to five actions")
    if len(spec.conditions) > MAX_CONDITIONS:
        raise _bad("conditions", "at most five conditions")
    for i, a in enumerate(spec.actions):
        if a.get("service") not in SERVICES:
            raise _bad(f"actions.{i}.service", "only light.turn_on and light.turn_off")
        targets = a.get("entity_ids") or []
        if not 1 <= len(targets) <= MAX_TARGETS:
            raise _bad(f"actions.{i}.entity_ids", "one to twenty lights")
        if any(not t.startswith("light.") for t in targets):
            raise _bad(f"actions.{i}.entity_ids", "targets must be lights")
        if a.get("brightness_pct") is not None and a["service"] != "light.turn_on":
            raise _bad(f"actions.{i}.brightness_pct", "brightness only for turn_on")
    for entity_id in sorted(spec.entity_ids()):
        if not entity_exists(entity_id):
            raise SentinelError(ErrorCode.VALIDATION_FAILED, "unknown entity",
                                param="entity_id", entity_id=entity_id)


def build_tree(spec: DraftSpec) -> list[dict[str, Any]]:
    """Plain automation tree in the current HA syntax (triggers/conditions/actions)."""
    trig = spec.trigger
    trigger: dict[str, Any] = {"trigger": trig["platform"], "entity_id": trig["entity_id"]}
    for key in ("from", "to", "above", "below"):
        if trig.get(key) not in (None, ""):
            trigger[key] = trig[key]
    if trig.get("for_s"):
        trigger["for"] = {"seconds": int(trig["for_s"])}
    actions: list[dict[str, Any]] = []
    for a in spec.actions:
        action: dict[str, Any] = {"action": a["service"],
                                  "target": {"entity_id": list(a["entity_ids"])}}
        if a.get("brightness_pct") is not None:
            action["data"] = {"brightness_pct": int(a["brightness_pct"])}
        actions.append(action)
    if spec.cooldown_s:
        # With mode single, further triggers are ignored until the delay ends.
        actions.append({"delay": {"seconds": int(spec.cooldown_s)}})
    digest = hashlib.sha256(json.dumps(
        [spec.alias, trigger, spec.conditions, actions, spec.mode], sort_keys=True,
        default=str).encode()).hexdigest()[:12]
    return [{
        "id": f"sentinel_draft_{digest}",
        "alias": spec.alias,
        "description": "Draft created by HA Sentinel. Review before use.",
        "mode": spec.mode,
        "triggers": [trigger],
        "conditions": [{"condition": "state", "entity_id": c["entity_id"],
                        "state": c["state"]} for c in spec.conditions],
        "actions": actions,
    }]


def to_yaml(tree: list[dict[str, Any]]) -> str:
    return yaml.safe_dump(tree, sort_keys=False, allow_unicode=True, default_flow_style=False)
