"""DOC006 evaluation rules (spec 8.1, 22.1)."""

from __future__ import annotations

from custom_components.ha_sentinel.modules.doctor.references import ImportRefs, evaluate

S = 1_000_000
NOW = 1_800_000_000 * S
STATUS = {"light.a": ("active", None), "light.old": ("renamed", "light.new"),
          "light.recent": ("deleted", None), "light.long_gone": ("deleted", None)}


def status(eid):
    return STATUS.get(eid, ("unknown", None))


def imp(rid, refs, complete=True):
    return ImportRefs(rid, f"{rid}.yaml", "2026-10-17T10:00:00Z", complete,
                      tuple((r, f"/0/{i}") for i, r in enumerate(refs)))


def test_missing_stale_and_migration_window():
    deleted = {"light.recent": NOW - 3600 * S, "light.long_gone": NOW - 3 * 86400 * S}
    out = evaluate([imp("r1", ["light.a", "light.old", "light.recent", "light.long_gone",
                               "light.typo", "light.typo"]),
                    imp("r2", ["light.a"])], status, deleted, NOW, inventory_complete=True)
    assert list(out) == ["r1"]
    d = out["r1"].details
    assert [m["entity_id"] for m in d["missing"]] == ["light.long_gone", "light.typo"]
    assert d["stale"] == [{"entity_id": "light.old", "current": "light.new", "pointer": "/0/1"}]
    assert d["missing_count"] == 2 and d["resource_label"] == "r1.yaml"


def test_exclusions_incomplete_import_and_inventory():
    assert evaluate([imp("r1", ["light.typo"], complete=False)], status, {}, NOW,
                    inventory_complete=True) == {}
    assert evaluate([imp("r1", ["light.typo"])], status, {}, NOW,
                    inventory_complete=False) == {}
