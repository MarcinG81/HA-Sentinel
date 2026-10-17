"""Probe target validation (spec 13.4, T18, AC NET 03). Pure logic.

Every address is checked after DNS resolution and the connection is made to that exact
validated address, so DNS rebinding cannot redirect a probe. Discovered service
announcements never become targets automatically.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

DEFAULT_ALLOWLIST = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
ALLOWED_PORTS = range(1, 65536)
KINDS = ("tcp", "http", "https")
# Cloud metadata endpoints (link-local is blocked anyway; listed for clarity).
METADATA = (ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"))
MAX_PATH = 200


class TargetRejected(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class TargetSpec:
    host: str
    port: int
    kind: str  # tcp | http | https
    path: str = "/"
    allow_loopback: bool = False


def parse_allowlist(text: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    nets = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError as err:
            raise TargetRejected("bad_allowlist") from err
    return tuple(nets)


def validate_spec(spec: TargetSpec) -> TargetSpec:
    """Syntactic checks before anything touches the network."""
    host = spec.host.strip().lower().rstrip(".")
    if not host or len(host) > 253 or any(c in host for c in "/\\@?#% \t\r\n"):
        raise TargetRejected("bad_host")
    if "://" in spec.host:
        raise TargetRejected("scheme_not_allowed")  # file:, gopher:, unix: ... never
    if spec.kind not in KINDS:
        raise TargetRejected("bad_kind")
    if spec.port not in ALLOWED_PORTS:
        raise TargetRejected("bad_port")
    path = spec.path or "/"
    if spec.kind != "tcp":
        if not path.startswith("/") or len(path) > MAX_PATH or any(c in path for c in "\r\n "):
            raise TargetRejected("bad_path")
    return TargetSpec(host, spec.port, spec.kind, path, spec.allow_loopback)


def check_address(address: str, allowlist, allow_loopback: bool) -> ipaddress._BaseAddress:
    """Accept only unicast addresses inside the allowlist (spec 13.4)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError as err:
        raise TargetRejected("bad_address") from err
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in METADATA:
        raise TargetRejected("metadata_endpoint")
    if ip.is_loopback:
        if not allow_loopback:
            raise TargetRejected("loopback_not_allowed")
        return ip
    if ip.is_multicast:
        raise TargetRejected("multicast")
    if ip.is_unspecified:
        raise TargetRejected("unspecified")
    if ip.is_link_local:
        raise TargetRejected("link_local")
    if isinstance(ip, ipaddress.IPv4Address) and (ip == ipaddress.IPv4Address(
            "255.255.255.255") or int(ip) & 0xFF == 0xFF and ip.is_private):
        # Limited broadcast and the common /24 broadcast address.
        raise TargetRejected("broadcast")
    if ip.is_reserved:
        raise TargetRejected("reserved")
    if not any(ip in net for net in allowlist):
        raise TargetRejected("outside_allowlist")
    return ip


def check_resolved(addresses: list[str], allowlist, allow_loopback: bool) -> list[str]:
    """All resolved addresses must pass; one bad answer rejects the target (rebinding)."""
    if not addresses:
        raise TargetRejected("dns_no_address")
    return [str(check_address(a, allowlist, allow_loopback)) for a in dict.fromkeys(addresses)]
