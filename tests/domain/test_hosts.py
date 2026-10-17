"""N0 network hosts: identity, addresses and suggestions (spec 13.2)."""

from __future__ import annotations

import pytest

from custom_components.ha_sentinel.core.errors import SentinelError
from custom_components.ha_sentinel.modules.network.hosts import (
    DeviceNet,
    normalize_mac,
    suggest,
    url_host,
    validate_host,
)


def test_normalisation():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"
    h = validate_host(" NAS ", "AA:BB:CC:DD:EE:FF", "NAS.Local.", ["192.168.1.10",
                                                                 "fe80::1", "192.168.1.10"])
    assert h.display_name == "NAS" and h.hostname == "nas.local"
    assert h.addresses == (("ipv4", "192.168.1.10"), ("ipv6", "fe80::1"))
    assert url_host("http://NAS.local:5000/x") == "nas.local" and url_host(None) is None
    assert url_host("http://[::1") is None


@pytest.mark.parametrize(("args", "param"), [
    (("", None, None, []), "display_name"),
    (("x", "zz:zz", None, []), "mac"),
    (("x", None, "bad host!", []), "hostname"),
    (("x", None, None, ["10.0.0.0/8"]), "addresses"),
    (("x", None, None, ["10.0.0.1"] * 9), "addresses"),
])
def test_validation(args, param):
    with pytest.raises(SentinelError) as err:
        validate_host(*args)
    assert err.value.details["param"] == param


def test_suggestions_rank_mac_over_hints_and_skip_linked():
    host = validate_host("NAS", "aa:bb:cc:dd:ee:ff", "nas.local", ["192.168.1.10"])
    devices = [
        DeviceNet("d-ip", "Printer", (), "192.168.1.10"),
        DeviceNet("d-mac", "Synology", ("AA:BB:CC:DD:EE:FF",), None),
        DeviceNet("d-name", "NAS web", (), "nas"),
        DeviceNet("d-none", "Other", ("11:22:33:44:55:66",), "10.0.0.9"),
        DeviceNet("d-bad", "Broken", ("not-a-mac",), None),
    ]
    out = suggest(host, devices, linked=set())
    assert [(s.device_id, s.reasons) for s in out] == [
        ("d-mac", ["mac_match"]), ("d-name", ["hostname_match"]), ("d-ip", ["ip_match"])]
    assert out[0].confidence == 0.9 and out[2].confidence <= 0.5  # heuristics stay <= 0.5
    assert [s.device_id for s in suggest(host, devices, linked={"d-mac"})][0] == "d-name"
