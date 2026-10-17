"""Network hosts for level N0 (spec 13.1, 13.2). Pure logic, no HA imports.

A host has identity keys (MAC, hostname) and addresses. An IP address is never an
identity: it may change through DHCP without creating a new host (AC NET 01). A MAC may
be randomised and is not always known. Two hosts never share an identity key; a clash
stops any automatic merge and needs the admin.

Links host <-> HA device are only ever created by an admin. Matching keys produce
suggestions with a reason and a confidence, never a link.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ...core.errors import ErrorCode, SentinelError

MAX_ADDRESSES = 8
_MAC = re.compile(r"^[0-9a-f]{2}([:-]?[0-9a-f]{2}){5}$")
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*$")


def normalize_mac(value: str) -> str:
    raw = value.strip().lower()
    if not _MAC.match(raw):
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "invalid MAC address", param="mac")
    hexed = re.sub(r"[^0-9a-f]", "", raw)
    return ":".join(hexed[i:i + 2] for i in range(0, 12, 2))


def normalize_hostname(value: str) -> str:
    raw = value.strip().lower().rstrip(".")
    if not _HOSTNAME.match(raw):
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "invalid hostname", param="hostname")
    return raw


def normalize_address(value: str) -> tuple[str, str]:
    """(family, canonical text). Only literal addresses; no ranges in N0."""
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError as err:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "invalid IP address",
                            param="addresses") from err
    return ("ipv4" if addr.version == 4 else "ipv6", str(addr))


@dataclass(frozen=True, slots=True)
class HostInput:
    display_name: str
    mac: str | None = None
    hostname: str | None = None
    addresses: tuple[tuple[str, str], ...] = ()


def validate_host(display_name: str, mac: str | None, hostname: str | None,
                  addresses: list[str]) -> HostInput:
    name = display_name.strip()
    if not 1 <= len(name) <= 100:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "name required", param="display_name")
    if len(addresses) > MAX_ADDRESSES:
        raise SentinelError(ErrorCode.VALIDATION_FAILED, "too many addresses",
                            param="addresses")
    addrs = tuple(dict.fromkeys(normalize_address(a) for a in addresses if a.strip()))
    return HostInput(
        display_name=name,
        mac=normalize_mac(mac) if mac and mac.strip() else None,
        hostname=normalize_hostname(hostname) if hostname and hostname.strip() else None,
        addresses=addrs,
    )


@dataclass(frozen=True, slots=True)
class DeviceNet:
    """What HA already knows about a device's network identity (N0, no probing)."""

    device_id: str
    name: str | None
    macs: tuple[str, ...] = ()
    url_host: str | None = None


@dataclass(slots=True)
class Suggestion:
    device_id: str
    device_name: str | None
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0


def url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def suggest(host: HostInput, devices: list[DeviceNet], linked: set[str]) -> list[Suggestion]:
    """Devices that may be this host. A MAC match is strong, hostname / IP are hints.

    Confidence follows spec 12.1: at most 0.5 for heuristics; a MAC from the device
    registry matching the admin's MAC is the strongest hint, still not a link.
    """
    out: list[Suggestion] = []
    addresses = {a for _, a in host.addresses}
    for dev in devices:
        if dev.device_id in linked:
            continue
        s = Suggestion(dev.device_id, dev.name)
        macs = set()
        for m in dev.macs:
            try:
                macs.add(normalize_mac(m))
            except SentinelError:
                continue
        if host.mac and host.mac in macs:
            s.reasons.append("mac_match")
            s.confidence = max(s.confidence, 0.9)
        if dev.url_host:
            if host.hostname and dev.url_host in (host.hostname, host.hostname.split(".")[0]):
                s.reasons.append("hostname_match")
                s.confidence = max(s.confidence, 0.5)
            if dev.url_host in addresses:
                s.reasons.append("ip_match")
                s.confidence = max(s.confidence, 0.3)  # DHCP can reassign it
        if s.reasons:
            out.append(s)
    out.sort(key=lambda s: (-s.confidence, s.device_name or "", s.device_id))
    return out[:10]
