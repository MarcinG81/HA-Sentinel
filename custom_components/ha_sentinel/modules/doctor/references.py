"""DOC006 missing reference and stale aliases in imported YAML (spec 8.1, 22.1).

Pure logic, no HA imports. Only literal entity references of the latest import of each
resource are checked; templates are never evaluated. Exclusions (spec 8.1):
- a tombstone younger than the migration window (an integration may be re-creating it);
- an incomplete import (unsupported fragments: the reference list may be partial);
- an incomplete inventory (startup, overload): nothing is reported at all.
A renamed entity is a *possibly* stale reference: the YAML may already be fixed in HA,
the import is only a copy from the given date. Sentinel never rewrites the text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

MIGRATION_WINDOW_S = 24 * 3600
MAX_LISTED = 20

EntityStatusFn = Callable[[str], tuple[str, str | None]]
"""entity_id -> (active | renamed | deleted | unknown, current alias if renamed)."""


@dataclass(frozen=True, slots=True)
class ImportRefs:
    resource_id: str
    label: str
    imported_at: str
    complete: bool
    refs: tuple[tuple[str, str], ...]  # (entity_id, JSON pointer)


@dataclass(slots=True)
class ReferenceIssue:
    resource_id: str
    label: str
    imported_at: str
    missing: list[dict[str, str]] = field(default_factory=list)
    stale: list[dict[str, str]] = field(default_factory=list)

    @property
    def details(self) -> dict:
        return {
            "resource_label": self.label, "imported_at": self.imported_at,
            "missing_count": len(self.missing), "stale_count": len(self.stale),
            "missing": self.missing[:MAX_LISTED], "stale": self.stale[:MAX_LISTED],
        }


def evaluate(imports: list[ImportRefs], entity_status: EntityStatusFn,
             deleted_at_us: dict[str, int], now_us: int, *,
             inventory_complete: bool) -> dict[str, ReferenceIssue]:
    """resource_id -> issue, only for resources with at least one finding."""
    if not inventory_complete:
        return {}
    window_us = MIGRATION_WINDOW_S * 1_000_000
    out: dict[str, ReferenceIssue] = {}
    for imp in imports:
        if not imp.complete:
            continue
        issue = ReferenceIssue(imp.resource_id, imp.label, imp.imported_at)
        seen: set[str] = set()
        for entity_id, pointer in imp.refs:
            if entity_id in seen:
                continue
            seen.add(entity_id)
            status, current = entity_status(entity_id)
            if status == "active":
                continue
            if status == "renamed" and current:
                issue.stale.append({"entity_id": entity_id, "current": current,
                                    "pointer": pointer})
                continue
            deleted = deleted_at_us.get(entity_id)
            if status == "deleted" and deleted is not None and now_us - deleted < window_us:
                continue  # inside the migration window
            issue.missing.append({"entity_id": entity_id, "pointer": pointer})
        if issue.missing or issue.stale:
            out[imp.resource_id] = issue
    return out
