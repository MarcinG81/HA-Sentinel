"""Network probes: target validation (T18, AC NET 03), limits (AC NET 05), state machine."""

from __future__ import annotations

import pytest

from custom_components.ha_sentinel.modules.network.scheduler import (
    MAX_PARALLEL,
    MAX_PER_MINUTE,
    Outcome,
    ProbeBook,
    ProbeLimiter,
    Status,
)
from custom_components.ha_sentinel.modules.network.targets import (
    DEFAULT_ALLOWLIST,
    TargetRejected,
    TargetSpec,
    check_resolved,
    parse_allowlist,
    validate_spec,
)

ALLOW = parse_allowlist(",".join(DEFAULT_ALLOWLIST))
S_NS = 1_000_000_000


@pytest.mark.parametrize(("addr", "reason"), [
    ("8.8.8.8", "outside_allowlist"),
    ("169.254.169.254", "metadata_endpoint"),
    ("fd00:ec2::254", "metadata_endpoint"),
    ("169.254.10.1", "link_local"),
    ("224.0.0.251", "multicast"),
    ("0.0.0.0", "unspecified"),
    ("255.255.255.255", "broadcast"),
    ("192.168.1.255", "broadcast"),
    ("127.0.0.1", "loopback_not_allowed"),
    ("::ffff:127.0.0.1", "loopback_not_allowed"),
    ("fe80::1", "link_local"),
])
def test_blocked_addresses(addr, reason):
    with pytest.raises(TargetRejected) as err:
        check_resolved([addr], ALLOW, allow_loopback=False)
    assert err.value.reason == reason


def test_allowed_lan_and_explicit_loopback():
    assert check_resolved(["192.168.1.20", "192.168.1.20"], ALLOW, False) == ["192.168.1.20"]
    assert check_resolved(["fd12::5"], ALLOW, False) == ["fd12::5"]
    assert check_resolved(["127.0.0.1"], ALLOW, allow_loopback=True) == ["127.0.0.1"]


def test_t18_dns_rebinding_any_bad_answer_rejects():
    # A name resolving to a LAN address and a public/metadata address is rejected outright.
    with pytest.raises(TargetRejected) as err:
        check_resolved(["192.168.1.20", "169.254.169.254"], ALLOW, False)
    assert err.value.reason == "metadata_endpoint"
    with pytest.raises(TargetRejected):
        check_resolved([], ALLOW, False)


@pytest.mark.parametrize(("spec", "reason"), [
    (TargetSpec("file:///etc/passwd", 80, "http"), "bad_host"),
    (TargetSpec("gopher://x", 70, "tcp"), "bad_host"),
    (TargetSpec("host", 0, "tcp"), "bad_port"),
    (TargetSpec("host", 80, "udp"), "bad_kind"),
    (TargetSpec("host", 80, "http", "/a\r\nX: y"), "bad_path"),
    (TargetSpec("user@host", 80, "http"), "bad_host"),
])
def test_spec_rejections(spec, reason):
    with pytest.raises(TargetRejected) as err:
        validate_spec(spec)
    assert err.value.reason == reason


def test_ac_net_05_global_limits_shared():
    lim = ProbeLimiter()
    granted = [lim.try_acquire(0) for _ in range(10)]
    assert sum(granted) == MAX_PARALLEL  # parallel cap
    for _ in range(MAX_PARALLEL):
        lim.release()
    started = MAX_PARALLEL
    t = 0
    while started < MAX_PER_MINUTE:
        t += 1
        assert lim.try_acquire(t)
        lim.release()
        started += 1
    assert not lim.try_acquire(t + 1)  # 30 per minute across manual + periodic
    assert lim.try_acquire(61 * S_NS)


def test_state_machine_three_failures_two_successes():
    book = ProbeBook()
    tr = [book.record("t", Outcome.TIMEOUT, i, i) for i in range(3)]
    assert [x.became_unreachable for x in tr] == [False, False, True]
    assert book.targets["t"].status is Status.UNREACHABLE
    assert not book.record("t", Outcome.OPEN, 10, 10).recovered
    assert book.record("t", Outcome.OPEN, 11, 11).recovered
    assert book.targets["t"].status is Status.REACHABLE


def test_refused_is_not_a_network_failure():
    book = ProbeBook()
    for i in range(5):
        book.record("t", Outcome.REFUSED, i, i)
    assert book.targets["t"].status is Status.SERVICE_CLOSED


def test_one_attempt_per_target_per_minute():
    book = ProbeBook()
    assert book.due("t", 0)
    book.record("t", Outcome.OPEN, 0, 0)
    assert not book.due("t", 59 * S_NS)
    assert book.due("t", 60 * S_NS)
