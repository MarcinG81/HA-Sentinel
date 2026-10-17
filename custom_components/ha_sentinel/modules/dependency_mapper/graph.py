"""Dependency graph (spec 6.3, 12). Pure logic, in-memory adjacency indexes.

Edges always point from the dependent to the dependency: automation -> entity (reads or
controls), entity -> device (provided_by), device -> integration_entry (managed_by).
Area membership is grouping only and is never followed by impact analysis. References
to unknown entities become unresolved_reference nodes instead of disappearing.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..autopilot.model import ResourceModel

MAX_DEPTH = 5
MAX_NODES = 1000
MAX_EDGES = 3000
TOTAL_EDGE_CAP = 50_000
CAUSAL = frozenset({"reads", "provided_by", "managed_by", "depends_on", "calls"})
CONF_EXPLICIT = 1.0
CONF_BLUEPRINT_INPUT = 0.8


@dataclass(frozen=True, slots=True)
class EntityInfo:
    uid: str
    entity_id: str
    name: str | None
    device_id: str | None
    area_id: str | None
    config_entry_id: str | None


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    ha_device_id: str
    name: str | None
    area_id: str | None
    config_entry_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntryInfo:
    entry_id: str
    domain: str
    title: str


@dataclass(slots=True)
class Node:
    uid: str
    type: str
    label: str
    external_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"uid": self.uid, "type": self.type, "label": self.label,
                "external_ref": self.external_ref, "metadata": self.metadata}


@dataclass(frozen=True, slots=True)
class Edge:
    from_uid: str
    to_uid: str
    relation: str
    provenance: str  # registry | static_parse | adapter | observed | manual
    confidence: float
    evidence_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"from": self.from_uid, "to": self.to_uid, "relation": self.relation,
                "provenance": self.provenance, "confidence": self.confidence,
                "evidence_ref": self.evidence_ref}


@dataclass(slots=True)
class ImportedResource:
    resource_id: str
    label: str
    model: ResourceModel
    imported_at: str


class DependencyGraph:
    def __init__(self, revision: int = 0) -> None:
        self.revision = revision
        self.nodes: dict[str, Node] = {}
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}
        self._edge_keys: set[tuple[str, str, str]] = set()

    def add_node(self, node: Node) -> Node:
        return self.nodes.setdefault(node.uid, node)

    def add_edge(self, edge: Edge) -> None:
        key = (edge.from_uid, edge.to_uid, edge.relation)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self._out.setdefault(edge.from_uid, []).append(edge)
        self._in.setdefault(edge.to_uid, []).append(edge)

    @property
    def edge_count(self) -> int:
        return len(self._edge_keys)

    @property
    def unresolved_count(self) -> int:
        return sum(1 for n in self.nodes.values() if n.type == "unresolved_reference")

    def search(self, text: str, limit: int = 20) -> list[Node]:
        q = text.strip().lower()
        if not q:
            return []
        hits = [n for n in self.nodes.values()
                if q in n.label.lower() or (n.external_ref and q in n.external_ref.lower())]
        hits.sort(key=lambda n: (not n.label.lower().startswith(q), n.type, n.label))
        return hits[:limit]

    def query(self, uid: str, direction: str, depth: int,
              include_grouping: bool = False, offset: int = 0) -> dict[str, Any]:
        """BFS with visited set; cycles terminate; limits page the answer (spec 12.2).

        direction="dependents" answers "what depends on this object" (reverse edges);
        "dependencies" answers "what does this object depend on". A page holds at most
        MAX_EDGES edges and MAX_NODES nodes; `next_offset` continues a truncated answer
        on the same revision. The walk itself stops at TOTAL_EDGE_CAP edges.
        """
        depth = max(1, min(depth, MAX_DEPTH))
        if uid not in self.nodes:
            return {"found": False, "nodes": [], "edges": [], "truncated": False,
                    "next_offset": None, "graph_revision": self.revision,
                    "unresolved_count": 0}
        index = self._in if direction == "dependents" else self._out
        visited = {uid}
        ordered: list[Edge] = []
        capped = False
        queue: deque[tuple[str, int]] = deque([(uid, 0)])
        while queue and not capped:
            current, d = queue.popleft()
            if d >= depth:
                continue
            for edge in sorted(index.get(current, []),
                               key=lambda e: (e.relation, e.from_uid, e.to_uid)):
                if edge.relation == "member_of" and not include_grouping:
                    continue
                if len(ordered) >= TOTAL_EDGE_CAP:
                    capped = True
                    break
                nxt = edge.from_uid if direction == "dependents" else edge.to_uid
                if nxt not in visited:
                    visited.add(nxt)
                    # controls is a potential effect, not an availability dependency:
                    # list it but do not traverse further through it (spec 9.2, 12.2).
                    if edge.relation in CAUSAL:
                        queue.append((nxt, d + 1))
                ordered.append(edge)
        page: list[Edge] = []
        node_ids: list[str] = [uid] if offset == 0 else []
        seen_nodes = set(node_ids)
        i = offset
        while i < len(ordered) and len(page) < MAX_EDGES:
            edge = ordered[i]
            new = [n for n in (edge.from_uid, edge.to_uid) if n not in seen_nodes]
            if len(node_ids) + len(new) > MAX_NODES:
                break
            node_ids += new
            seen_nodes.update(new)
            page.append(edge)
            i += 1
        more = i < len(ordered)
        nodes = [self.nodes[n] for n in node_ids if n in self.nodes]
        return {
            "found": True,
            "nodes": [n.as_dict() for n in nodes],
            "edges": [e.as_dict() for e in page],
            "truncated": more or capped,
            "next_offset": i if more else None,
            "graph_revision": self.revision,
            "unresolved_count": sum(1 for n in nodes if n.type == "unresolved_reference"),
        }


# Relations RCA follows upwards; areas, controls and reads are excluded (spec 9.2).
RCA_RELATIONS = frozenset({"provided_by", "managed_by", "depends_on", "uses_endpoint"})
RCA_MIN_CONFIDENCE = 0.5


def ancestors(graph: DependencyGraph, uid: str, max_hops: int = 3) -> dict[str, tuple[int, float]]:
    """Candidates up to `max_hops` dependency edges up: uid -> (hops, best path confidence)."""
    best: dict[str, tuple[int, float]] = {}
    frontier: list[tuple[str, int, float]] = [(uid, 0, 1.0)]
    while frontier:
        nxt: list[tuple[str, int, float]] = []
        for current, hops, conf in frontier:
            if hops >= max_hops:
                continue
            for edge in graph._out.get(current, []):
                if edge.relation not in RCA_RELATIONS or edge.confidence < RCA_MIN_CONFIDENCE:
                    continue
                c = conf * edge.confidence
                prev = best.get(edge.to_uid)
                if prev is None or c > prev[1] or (c == prev[1] and hops + 1 < prev[0]):
                    best[edge.to_uid] = (hops + 1, c)
                    nxt.append((edge.to_uid, hops + 1, c))
        frontier = nxt
    best.pop(uid, None)
    return best


def dependent_entities(graph: DependencyGraph, uid: str, max_hops: int = 3) -> set[str]:
    """Entity nodes that depend on `uid` through RCA relations (reverse traversal)."""
    seen = {uid}
    out: set[str] = set()
    frontier = [uid]
    for _ in range(max_hops):
        nxt: list[str] = []
        for current in frontier:
            for edge in graph._in.get(current, []):
                if edge.relation not in RCA_RELATIONS or edge.from_uid in seen:
                    continue
                seen.add(edge.from_uid)
                nxt.append(edge.from_uid)
                node = graph.nodes.get(edge.from_uid)
                if node is not None and node.type == "entity":
                    out.add(edge.from_uid)
        frontier = nxt
    return out


def build_graph(
    *,
    entities: list[EntityInfo],
    devices: list[DeviceInfo],
    entries: list[EntryInfo],
    areas: dict[str, str],
    imports: list[ImportedResource],
    revision: int,
    extra_nodes: list[Node] | None = None,
    extra_edges: list[Edge] | None = None,
) -> DependencyGraph:
    g = DependencyGraph(revision)
    entry_by_id = {e.entry_id: e for e in entries}
    for e in entries:
        g.add_node(Node(f"config_entry:{e.entry_id}", "integration_entry", e.title or e.domain,
                        e.domain))
    for area_id, name in areas.items():
        g.add_node(Node(f"area:{area_id}", "area", name, area_id))
    for d in devices:
        uid = f"device:{d.ha_device_id}"
        g.add_node(Node(uid, "device", d.name or d.ha_device_id, d.ha_device_id))
        for entry_id in d.config_entry_ids:
            if entry_id in entry_by_id:
                g.add_edge(Edge(uid, f"config_entry:{entry_id}", "managed_by", "registry",
                                CONF_EXPLICIT))
        if d.area_id and f"area:{d.area_id}" in g.nodes:
            g.add_edge(Edge(uid, f"area:{d.area_id}", "member_of", "registry", CONF_EXPLICIT))

    by_entity_id: dict[str, str] = {}
    for e in entities:
        g.add_node(Node(e.uid, "entity", e.name or e.entity_id, e.entity_id))
        by_entity_id[e.entity_id] = e.uid
        if e.device_id and f"device:{e.device_id}" in g.nodes:
            g.add_edge(Edge(e.uid, f"device:{e.device_id}", "provided_by", "registry",
                            CONF_EXPLICIT))
        elif e.config_entry_id and e.config_entry_id in entry_by_id:
            g.add_edge(Edge(e.uid, f"config_entry:{e.config_entry_id}", "managed_by",
                            "registry", CONF_EXPLICIT))
        if e.area_id and f"area:{e.area_id}" in g.nodes:
            g.add_edge(Edge(e.uid, f"area:{e.area_id}", "member_of", "registry", CONF_EXPLICIT))

    for node in extra_nodes or []:
        g.add_node(node)
    for edge in extra_edges or []:
        if edge.from_uid in g.nodes and edge.to_uid in g.nodes:
            g.add_edge(edge)

    for imp in imports:
        for r in imp.model.runnables:
            ntype = "script" if r.kind == "script" else "automation"
            ruid = f"{ntype}:{imp.resource_id}:{r.key}"
            g.add_node(Node(ruid, ntype, r.label, r.id,
                            {"resource_id": imp.resource_id, "imported_at": imp.imported_at,
                             "pointer": r.pointer,
                             "complete": not r.templates and not r.unsupported}))
            for ref in r.refs:
                target = _resolve(g, by_entity_id, ref.kind, ref.value)
                if target is None:
                    continue
                relation = {"trigger": "reads", "condition": "reads", "target": "controls",
                            "call": "calls"}[ref.role]
                if ref.kind == "area":
                    relation = "controls"  # an area target is a group, resolved per revision
                confidence = CONF_BLUEPRINT_INPUT if ref.kind == "blueprint_input" \
                    else CONF_EXPLICIT
                g.add_edge(Edge(ruid, target, relation, "static_parse", confidence,
                                f"{imp.resource_id}#{ref.pointer}"))
    return g


def _resolve(g: DependencyGraph, by_entity_id: dict[str, str], kind: str,
             value: str) -> str | None:
    if kind in ("entity", "scene", "script"):
        uid = by_entity_id.get(value)
        if uid:
            return uid
    elif kind == "device":
        if f"device:{value}" in g.nodes:
            return f"device:{value}"
    elif kind == "area":
        if f"area:{value}" in g.nodes:
            return f"area:{value}"
    elif kind in ("service", "blueprint_input"):
        return None
    uid = f"unresolved:{kind}:{value}"
    g.add_node(Node(uid, "unresolved_reference", value, value, {"kind": kind}))
    return uid
