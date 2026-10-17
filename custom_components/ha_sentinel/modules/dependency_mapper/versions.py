"""Versioned dependency graph (spec 6.3, 7.2, 12.2, AC MAP 05). Pure logic, no HA imports.

A revision stores the difference to the previous one; every CHECKPOINT_EVERY revisions a
full copy is stored so an old graph is rebuilt from at most CHECKPOINT_EVERY - 1 diffs.
The current view alone cannot answer "what did the graph look like then": old aliases and
references stay available through these revisions.
"""

from __future__ import annotations

from typing import Any

from .graph import DependencyGraph, Edge, Node

CHECKPOINT_EVERY = 20
KEEP_REVISIONS = 200


def _edge_key(e: dict[str, Any]) -> str:
    return f"{e['from']}|{e['to']}|{e['relation']}"


def to_doc(graph: DependencyGraph) -> dict[str, Any]:
    nodes = {n.uid: n.as_dict() for n in graph.nodes.values()}
    edges: dict[str, dict[str, Any]] = {}
    for out in graph._out.values():  # noqa: SLF001 - same package
        for e in out:
            d = e.as_dict()
            edges[_edge_key(d)] = d
    return {"nodes": nodes, "edges": edges}


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Changed nodes and edges count as removed + added; empty dict = no change."""
    out: dict[str, Any] = {}
    for kind in ("nodes", "edges"):
        a, b = old[kind], new[kind]
        removed = sorted(k for k in a if k not in b or a[k] != b[k])
        added = [b[k] for k in sorted(b) if k not in a or a[k] != b[k]]
        if removed:
            out[f"{kind}_removed"] = removed
        if added:
            out[f"{kind}_added"] = added
    return out


def apply(doc: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    nodes, edges = dict(doc["nodes"]), dict(doc["edges"])
    for uid in change.get("nodes_removed", []):
        nodes.pop(uid, None)
    for n in change.get("nodes_added", []):
        nodes[n["uid"]] = n
    for key in change.get("edges_removed", []):
        edges.pop(key, None)
    for e in change.get("edges_added", []):
        edges[_edge_key(e)] = e
    return {"nodes": nodes, "edges": edges}


def from_doc(doc: dict[str, Any], revision: int) -> DependencyGraph:
    g = DependencyGraph(revision)
    for n in doc["nodes"].values():
        g.add_node(Node(n["uid"], n["type"], n["label"], n.get("external_ref"),
                        n.get("metadata") or {}))
    for e in doc["edges"].values():
        g.add_edge(Edge(e["from"], e["to"], e["relation"], e["provenance"], e["confidence"],
                        e.get("evidence_ref")))
    return g


def rebuild(chain: list[dict[str, Any]]) -> dict[str, Any]:
    """chain: blobs from a checkpoint up to the wanted revision, oldest first."""
    if not chain or chain[0].get("kind") != "full":
        raise ValueError("chain must start with a checkpoint")
    doc = {"nodes": chain[0]["nodes"], "edges": chain[0]["edges"]}
    for blob in chain[1:]:
        doc = apply(doc, blob["diff"]) if blob.get("kind") == "diff" else \
            {"nodes": blob["nodes"], "edges": blob["edges"]}
    return doc


def summary(change: dict[str, Any]) -> dict[str, int]:
    return {k: len(v) for k, v in change.items()}
