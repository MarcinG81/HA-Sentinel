"""change_related hypotheses: only on the dependency path, only within 30 min (9.2)."""

from __future__ import annotations

from custom_components.ha_sentinel.modules.correlation.changes import (
    ConfigChange,
    change_hypotheses,
)
from custom_components.ha_sentinel.modules.dependency_mapper.graph import (
    DependencyGraph,
    Edge,
    Node,
)

S = 1_000_000
T = 1_800_000_000 * S


def _graph() -> DependencyGraph:
    g = DependencyGraph(1)
    for uid, typ in (("lamp", "entity"), ("plug", "entity"), ("dev", "device")):
        g.add_node(Node(uid, typ, uid))
    g.add_node(Node("automation:r1:a", "automation", "Evening", None, {"resource_id": "r1"}))
    g.add_node(Node("automation:r2:b", "automation", "Other", None, {"resource_id": "r2"}))
    g.add_edge(Edge("lamp", "dev", "provided_by", "registry", 1.0))
    g.add_edge(Edge("automation:r1:a", "dev", "controls", "static_parse", 1.0))
    g.add_edge(Edge("automation:r2:b", "plug", "controls", "static_parse", 1.0))
    return g


def test_only_changes_on_the_path_and_inside_the_window():
    changes = [ConfigChange("r1", "evening.yaml", T - 12 * 60 * S),
               ConfigChange("r2", "other.yaml", T - 5 * 60 * S),  # unrelated object
               ConfigChange("r1", "evening.yaml", T - 45 * 60 * S)]  # too old
    out = change_hypotheses(_graph(), ["lamp"], T, changes)
    assert out == [{"kind": "change_related", "candidate_uid": "resource:r1",
                    "label": "evening.yaml", "minutes_before": 12, "runnable": "Evening",
                    "relation": "controls", "subject_uid": "lamp"}]
    assert change_hypotheses(_graph(), ["lamp"], T, changes[2:]) == []
    assert change_hypotheses(_graph(), ["lamp"], T, [ConfigChange("r1", "x", T + 60 * S)]) == []
