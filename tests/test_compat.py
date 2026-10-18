"""Compatibility helpers for HA API differences between 2026.5 and 2026.10."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.ha_sentinel.adapters.compat import belongs_to, device_entry_ids, devices


class _OldItems(dict):
    """2026.5: a mapping whose iteration yields device ids."""


class _NewItems:
    """2026.10: iteration yields entries; mapping access is deprecated."""

    def __init__(self, entries):
        self._entries = entries

    def __iter__(self):
        return iter(self._entries)

    def values(self):  # pragma: no cover - must not be called on the new API
        raise AssertionError("deprecated mapping access")


def test_devices_on_both_registry_shapes():
    a, b = SimpleNamespace(id="a"), SimpleNamespace(id="b")
    assert devices(SimpleNamespace(devices=_OldItems(a=a, b=b))) == [a, b]
    assert devices(SimpleNamespace(devices=_NewItems([a, b]))) == [a, b]
    assert devices(SimpleNamespace(devices=_NewItems([]))) == []


def test_device_entry_ids_on_both_device_shapes():
    new = SimpleNamespace(config_entry_id="e1")
    assert device_entry_ids(new) == ("e1",)
    assert device_entry_ids(SimpleNamespace(config_entry_id=None)) == ()
    old = SimpleNamespace(config_entries={"e2", "e1"})
    assert device_entry_ids(old) == ("e1", "e2")
    assert belongs_to(old, ["e2"]) and not belongs_to(new, ["e2"])
