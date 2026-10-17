"""Incident correlation and probable-cause ranking (spec 9). Pure logic, deterministic.

A finding is one rule result; an incident groups findings of one episode. Availability
findings (DOC001/DOC002) starting within 120 s that share an explicit dependency ancestor
form one incident. A shared area or mere simultaneity never merges findings.

Score = clamp(0.20T + 0.25D + 0.20P + 0.35E - 0.30K) is a heuristic ranking, not a
probability. A "strong hypothesis" needs score >= 0.75 and direct evidence E >= 0.5, and
is still not a confirmed cause.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...core.models import Severity
from ..dependency_mapper.graph import DependencyGraph, ancestors, dependent_entities

S = 1_000_000
RULE_VERSION = 1
GROUP_WINDOW_S = 120
CLOCK_TOLERANCE_S = 5
MAX_HYPOTHESES = 3
GROUPABLE_RULES = frozenset({"DOC001", "DOC002"})
CANDIDATE_TYPES = frozenset({"device", "integration_entry", "network_host", "service_endpoint"})
_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


@dataclass(frozen=True, slots=True)
class ActiveFinding:
    id: str
    rule_id: str
    subject_uid: str
    severity: Severity
    first_seen_us: int


@dataclass(frozen=True, slots=True)
class ExistingIncident:
    id: str
    fingerprint: str
    finding_ids: frozenset[str]
    opened_at_us: int
    root_uid: str | None


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Direct evidence about a candidate: E=1 explicit offline status, 0.5 one unavailable
    status entity, 0 nothing (spec 9.2)."""

    e: float
    start_us: int | None
    source: str


@dataclass(slots=True)
class IncidentPlan:
    fingerprint: str
    finding_ids: list[str]
    subject_uid: str
    root_uid: str | None
    severity: Severity
    opened_at_us: int
    title_key: str
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    merge_incident_ids: list[str] = field(default_factory=list)


EntityAvailable = Callable[[str], bool | None]
"""entity uid -> True available, False unavailable/unknown, None not observed."""


def fingerprint(instance_id: str, family: str, uid: str) -> str:
    return hashlib.sha256(f"{instance_id}|{family}|{uid}".encode()).hexdigest()[:32]


def correlate(
    *,
    instance_id: str,
    findings: list[ActiveFinding],
    existing: list[ExistingIncident],
    graph: DependencyGraph,
    evidence: dict[str, CandidateEvidence],
    entity_available: EntityAvailable,
    coverage_complete: Callable[[int, int], bool],
    now_us: int,
) -> list[IncidentPlan]:
    findings = sorted(findings, key=lambda f: (f.first_seen_us, f.id))
    by_id = {f.id: f for f in findings}
    anc: dict[str, dict[str, tuple[int, float]]] = {}

    def candidates_of(f: ActiveFinding) -> dict[str, tuple[int, float]]:
        if f.subject_uid not in anc:
            anc[f.subject_uid] = {
                uid: v for uid, v in ancestors(graph, f.subject_uid).items()
                if graph.nodes.get(uid) is not None
                and graph.nodes[uid].type in CANDIDATE_TYPES
            }
        return anc[f.subject_uid]

    # 1. Group incidents keep their members: an incident never silently loses members.
    #    Single-finding incidents are re-clustered so late-maturing peers can merge them.
    clusters: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for inc in sorted(existing, key=lambda i: (i.opened_at_us, i.id)):
        if inc.root_uid is None:
            continue
        members = [by_id[fid] for fid in sorted(inc.finding_ids) if fid in by_id]
        if not members:
            continue  # all members recovered -> the incident resolves
        clusters.append({"members": members, "root": inc.root_uid})
        assigned.update(m.id for m in members)

    # 2. Attach new groupable findings to a compatible cluster, or start a new one.
    for f in findings:
        if f.id in assigned:
            continue
        target = None
        if f.rule_id in GROUPABLE_RULES:
            mine = set(candidates_of(f))
            for c in clusters:
                groupable = [m for m in c["members"] if m.rule_id in GROUPABLE_RULES]
                if not groupable or not mine:
                    continue
                start = min(m.first_seen_us for m in groupable)
                if abs(f.first_seen_us - start) > GROUP_WINDOW_S * S:
                    continue
                shared = mine.intersection(*(set(candidates_of(m)) for m in groupable))
                if shared:
                    target = c
                    break
        if target is None:
            clusters.append({"members": [f], "root": None})
        else:
            target["members"].append(f)
        assigned.add(f.id)

    # 3. Build plans; an existing incident whose findings now belong to a different
    #    fingerprint is merged into it (closed as merged_into, never as recovered).
    plans: list[IncidentPlan] = []
    for c in clusters:
        members: list[ActiveFinding] = c["members"]
        groupable = [m for m in members if m.rule_id in GROUPABLE_RULES]
        root = c["root"]
        if len(groupable) >= 2 and root is None:
            shared = set(candidates_of(groupable[0])).intersection(
                *(set(candidates_of(m)) for m in groupable[1:]))
            root = _closest(shared, groupable, candidates_of)
        lead = members[0]
        if root is not None:
            fp = fingerprint(instance_id, "availability", root)
            title = "incident.infrastructure"
        else:
            fp = fingerprint(instance_id, lead.rule_id, lead.subject_uid)
            title = f"rule.{lead.rule_id}"
        hypotheses = _rank(members, graph, evidence, entity_available, coverage_complete,
                           candidates_of) if any(m.rule_id in GROUPABLE_RULES
                                                 for m in members) else []
        plans.append(IncidentPlan(
            fingerprint=fp,
            finding_ids=sorted(m.id for m in members),
            subject_uid=lead.subject_uid,
            root_uid=root,
            severity=max((m.severity for m in members), key=_SEVERITY_RANK.__getitem__),
            opened_at_us=min(m.first_seen_us for m in members),
            title_key=title,
            hypotheses=hypotheses,
        ))

    by_fp: dict[str, IncidentPlan] = {}
    for p in plans:
        if p.fingerprint in by_fp:  # two clusters converged on the same root
            other = by_fp[p.fingerprint]
            other.finding_ids = sorted(set(other.finding_ids) | set(p.finding_ids))
            other.merge_incident_ids += p.merge_incident_ids
            continue
        by_fp[p.fingerprint] = p
    for inc in existing:
        plan = next((p for p in by_fp.values() if inc.finding_ids & set(p.finding_ids)), None)
        if plan is not None and plan.fingerprint != inc.fingerprint \
                and inc.id not in plan.merge_incident_ids:
            plan.merge_incident_ids.append(inc.id)
    return sorted(by_fp.values(), key=lambda p: (p.opened_at_us, p.fingerprint))


def _closest(shared: set[str], members: list[ActiveFinding],
             candidates_of: Callable[[ActiveFinding], dict[str, tuple[int, float]]]) -> str | None:
    """Lowest common dependency: smallest total hop distance, then uid (stable)."""
    if not shared:
        return None
    return min(shared, key=lambda uid: (sum(candidates_of(m)[uid][0] for m in members), uid))


def _rank(
    members: list[ActiveFinding],
    graph: DependencyGraph,
    evidence: dict[str, CandidateEvidence],
    entity_available: EntityAvailable,
    coverage_complete: Callable[[int, int], bool],
    candidates_of: Callable[[ActiveFinding], dict[str, tuple[int, float]]],
) -> list[dict[str, Any]]:
    effects = [m for m in members if m.rule_id in GROUPABLE_RULES]
    affected_subjects = {m.subject_uid for m in effects}
    effect_start = min(m.first_seen_us for m in effects)
    candidates: set[str] = set()
    for m in effects:
        candidates.update(candidates_of(m))

    scored: list[dict[str, Any]] = []
    for uid in sorted(candidates):
        ev = evidence.get(uid, CandidateEvidence(0.0, None, "none"))
        reasons: list[str] = []
        # T: time agreement between the candidate and its effects.
        cand_start = ev.start_us if ev.start_us is not None else effect_start
        if cand_start - effect_start > CLOCK_TOLERANCE_S * S:
            t_score = 0.0
            reasons.append("candidate_started_after_effects")
        else:
            t_score = sum(max(0.0, 1 - abs(m.first_seen_us - cand_start) / (GROUP_WINDOW_S * S))
                          for m in effects) / len(effects)
        # D, K: affected and healthy observable dependents.
        observable = [e for e in dependent_entities(graph, uid)
                      if entity_available(e) is not None]
        if not observable:
            continue
        affected = [e for e in observable if e in affected_subjects or
                    entity_available(e) is False]
        healthy = [e for e in observable if entity_available(e) is True
                   and e not in affected_subjects]
        evidence_ok = coverage_complete(cand_start, max(cand_start, effect_start) + 1)
        d_score = len(affected) / len(observable)
        k_score = len(healthy) / len(observable) if evidence_ok else 0.0
        # P: mean best path confidence from each effect to the candidate.
        paths = [candidates_of(m)[uid][1] for m in effects if uid in candidates_of(m)]
        p_score = sum(paths) / len(effects) if paths else 0.0
        e_score = ev.e
        score = max(0.0, min(1.0, 0.20 * t_score + 0.25 * d_score + 0.20 * p_score
                             + 0.35 * e_score - 0.30 * k_score))
        if score >= 0.75 and e_score >= 0.5:
            label = "strong"
        elif score >= 0.5:
            label = "possible"
        else:
            label = "none"
        node = graph.nodes[uid]
        scored.append({
            "candidate_uid": uid,
            "candidate_label": node.label,
            "candidate_type": node.type,
            "score": round(score, 4),
            "label": label,
            "features": {"T": round(t_score, 4), "D": round(d_score, 4),
                         "P": round(p_score, 4), "E": e_score, "K": round(k_score, 4)},
            "evidence_source": ev.source,
            "affected": len(affected),
            "observable": len(observable),
            "counter_evidence": (
                [{"kind": "healthy_dependents", "count": len(healthy),
                  "sample": sorted(graph.nodes[h].label for h in healthy)[:5]}]
                if healthy else []
            ) + ([{"kind": "no_direct_evidence"}] if e_score == 0 else [])
              + ([{"kind": "incomplete_coverage"}] if not evidence_ok else []),
            "reasons": reasons,
            "graph_revision": graph.revision,
            "rule_version": RULE_VERSION,
        })
    scored.sort(key=lambda h: (-h["score"], h["candidate_uid"]))  # tie break by uid
    return scored[:MAX_HYPOTHESES]
