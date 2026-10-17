"""change_related hypotheses (spec 9.2). Pure logic, no HA imports.

A configuration change in the 30 minutes before an incident is a hypothesis only when
the changed resource is on the dependency path of an affected object: one of its
automations reads, controls or calls the object or one of the object's ancestors.
Time order alone is not evidence, so these hypotheses never raise the ranking score;
they are listed separately with what links them to the incident.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..dependency_mapper.graph import DependencyGraph, ancestors

WINDOW_S = 30 * 60
CLOCK_TOLERANCE_S = 5
LINK_RELATIONS = ("controls", "reads", "calls")
MAX_CHANGES = 3


@dataclass(frozen=True, slots=True)
class ConfigChange:
    resource_id: str
    label: str
    changed_at_us: int


def change_hypotheses(graph: DependencyGraph, subjects: list[str], started_us: int,
                      changes: list[ConfigChange]) -> list[dict]:
    window_us = WINDOW_S * 1_000_000
    tolerance_us = CLOCK_TOLERANCE_S * 1_000_000
    recent = [c for c in changes
              if started_us - window_us <= c.changed_at_us <= started_us + tolerance_us]
    if not recent:
        return []
    affected: dict[str, str] = {}  # node uid -> affected subject it stands for
    for subject in subjects:
        affected.setdefault(subject, subject)
        for anc in ancestors(graph, subject):
            affected.setdefault(anc, subject)
    by_resource: dict[str, list] = {}
    for node in graph.nodes.values():
        rid = node.metadata.get("resource_id") if node.metadata else None
        if rid is not None:
            by_resource.setdefault(rid, []).append(node)
    out = []
    for change in sorted(recent, key=lambda c: -c.changed_at_us):
        link = None
        for runnable in by_resource.get(change.resource_id, []):
            for edge in graph._out.get(runnable.uid, []):  # noqa: SLF001 - same package
                if edge.relation in LINK_RELATIONS and edge.to_uid in affected:
                    link = (runnable.label, edge.relation, affected[edge.to_uid])
                    break
            if link:
                break
        if link is None:
            continue  # changed, but not on any dependency path of this incident
        out.append({
            "kind": "change_related",
            "candidate_uid": f"resource:{change.resource_id}",
            "label": change.label,
            "minutes_before": max(0, round((started_us - change.changed_at_us) / 60e6)),
            "runnable": link[0], "relation": link[1], "subject_uid": link[2],
        })
        if len(out) >= MAX_CHANGES:
            break
    return out
