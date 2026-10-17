"""Versioned graph and paged neighbourhood queries (spec 12.2, T11, AC MAP 05)."""

from __future__ import annotations

from custom_components.ha_sentinel.modules.dependency_mapper import versions
from custom_components.ha_sentinel.modules.dependency_mapper.graph import (
    MAX_EDGES,
    DependencyGraph,
    Edge,
    Node,
)


def graph(labels: dict[str, str], edges: list[tuple[str, str]]) -> DependencyGraph:
    g = DependencyGraph(1)
    for uid, label in labels.items():
        g.add_node(Node(uid, "entity", label))
    for a, b in edges:
        g.add_edge(Edge(a, b, "provided_by", "registry", 1.0))
    return g


def test_diff_apply_and_rebuild_roundtrip():
    g1 = graph({"a": "light.old", "d": "Dev"}, [("a", "d")])
    g2 = graph({"a": "light.new", "d": "Dev", "b": "sensor.x"}, [("a", "d"), ("b", "d")])
    g3 = graph({"a": "light.new", "d": "Dev"}, [("a", "d")])
    d1, d2, d3 = versions.to_doc(g1), versions.to_doc(g2), versions.to_doc(g3)
    assert versions.diff(d1, d1) == {}
    c12 = versions.diff(d1, d2)
    assert c12["nodes_removed"] == ["a"] and {n["uid"] for n in c12["nodes_added"]} == {"a", "b"}
    chain = [{"kind": "full", **d1}, {"kind": "diff", "diff": c12},
             {"kind": "diff", "diff": versions.diff(d2, d3)}]
    assert versions.rebuild(chain[:1]) == d1
    assert versions.rebuild(chain[:2]) == d2
    assert versions.rebuild(chain) == d3
    old = versions.from_doc(versions.rebuild(chain[:1]), 7)
    assert old.nodes["a"].label == "light.old" and old.revision == 7  # AC MAP 05


def test_paged_query_on_a_large_cyclic_graph():
    n = 6000
    labels = {f"n{i}": f"n{i}" for i in range(n)}
    edges = [(f"n{i}", "hub") for i in range(n)] + [("hub", "n0")]  # cycle through hub
    g = graph({**labels, "hub": "hub"}, edges)
    seen, offset, pages = set(), 0, 0
    while True:
        page = g.query("hub", "dependents", 5, offset=offset)
        pages += 1
        assert len(page["edges"]) <= MAX_EDGES and len(page["nodes"]) <= 1000
        seen |= {(e["from"], e["to"]) for e in page["edges"]}
        if page["next_offset"] is None:
            assert page["truncated"] is False
            break
        assert page["truncated"] is True
        offset = page["next_offset"]
    assert len(seen) == n + 1 and pages > 1  # every edge exactly once, finite despite cycle
