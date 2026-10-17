"""Network hosts at level N0: manual hosts and admin-confirmed links to HA devices.

No probing happens here (AC NET 04). Reachability (from approved N1 probes, if any) and
the availability of the linked devices' entities are reported as two separate states.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .core.clock import us_to_rfc3339
from .core.errors import ErrorCode, SentinelError
from .core.models import Availability, new_uuid
from .modules.dependency_mapper.graph import Edge, Node
from .modules.network.hosts import DeviceNet, HostInput, suggest, url_host, validate_host
from .repositories.repository import _tx, insert_audit

if TYPE_CHECKING:
    from .network_services import NetworkService

MAX_HOSTS = 256
MAX_LINKS_PER_HOST = 8


def _host_input(h: dict[str, Any]) -> HostInput:
    keys = {k["kind"]: k["value"] for k in h["keys"]}
    return HostInput(h["display_name"], keys.get("mac"), keys.get("hostname"),
                     tuple((a["family"], a["value"]) for a in h["addresses"]))


class HostService:
    def __init__(self, network: NetworkService) -> None:
        self.net = network
        self.rt = network.rt
        self.hass: HomeAssistant = network.hass
        self.hosts: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        def load(conn):
            hosts = {r["id"]: {**dict(r), "keys": [], "addresses": [], "links": []}
                     for r in conn.execute("SELECT * FROM network_hosts ORDER BY display_name")}
            for table, field in (("network_host_keys", "keys"),
                                 ("network_host_addresses", "addresses"),
                                 ("network_host_links", "links")):
                for r in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608 - fixed names
                    hosts[r["host_id"]][field].append(dict(r))
            return hosts

        self.hosts = await self.rt.db.run(load)

    # --- Read model -----------------------------------------------------------------

    def _devices(self) -> list[DeviceNet]:
        out = []
        for dev in dr.async_get(self.hass).devices.values():
            macs = tuple(v for t, v in dev.connections if t == dr.CONNECTION_NETWORK_MAC)
            host = url_host(dev.configuration_url)
            if macs or host:
                out.append(DeviceNet(dev.id, dev.name_by_user or dev.name, macs, host))
        return out

    def _ha_status(self, device_ids: list[str]) -> dict[str, int]:
        """Availability of the linked devices' entities; never inferred from the network."""
        avail = self.rt.doctor.availability_snapshot()
        counts = {"available": 0, "unavailable": 0, "unknown": 0}
        wanted = set(device_ids)
        devices = {d.uid: d.ha_device_id for d in self.rt.inventory.devices()}
        for ent in self.rt.inventory.active_entities():
            if ent.device_uid and devices.get(ent.device_uid) in wanted:
                state = avail.get(ent.uid)
                key = "unknown" if state is None else (
                    "available" if state is Availability.AVAILABLE else
                    "unavailable" if state is Availability.UNAVAILABLE else "unknown")
                counts[key] += 1
        return counts

    def _reachability(self, host_id: str) -> str:
        """Worst status of the approved endpoints of this host; not_probed in N0."""
        statuses = [self.net.target_api(t)["status"] for t in self.net.targets.values()
                    if t.get("host_id") == host_id and t["enabled"]]
        if not statuses or not self.net.active:
            return "not_probed"
        for status in ("unreachable", "service_closed", "unknown", "reachable"):
            if status in statuses:
                return status
        return statuses[0]

    def host_api(self, h: dict[str, Any], devices: list[DeviceNet] | None = None
                 ) -> dict[str, Any]:
        reg = dr.async_get(self.hass)
        linked = [lk["device_id"] for lk in h["links"]]
        devices = self._devices() if devices is None else devices
        return {
            "id": h["id"], "display_name": h["display_name"], "source": h["source"],
            "keys": {k["kind"]: k["value"] for k in h["keys"]},
            "addresses": [{"family": a["family"], "value": a["value"]} for a in h["addresses"]],
            "links": [{"device_id": d,
                       "name": (dev.name_by_user or dev.name)
                       if (dev := reg.async_get(d)) else None,
                       "missing": dev is None} for d in linked],
            "suggestions": [{"device_id": s.device_id, "name": s.device_name,
                             "reasons": s.reasons, "confidence": s.confidence}
                            for s in suggest(_host_input(h), devices, set(linked))],
            "reachability": self._reachability(h["id"]),
            "ha_entities": self._ha_status(linked),
            "endpoints": [tid for tid, t in self.net.targets.items()
                          if t.get("host_id") == h["id"]],
            "first_seen": us_to_rfc3339(h["first_seen_us"]),
            "updated": us_to_rfc3339(h["updated_us"]),
        }

    def hosts_api(self) -> list[dict[str, Any]]:
        devices = self._devices()
        return [self.host_api(h, devices) for h in self.hosts.values()]

    def graph_elements(self) -> tuple[list[Node], list[Edge]]:
        nodes, edges = [], []
        for hid, h in self.hosts.items():
            uid = f"host:{hid}"
            nodes.append(Node(uid, "network_host", h["display_name"], None))
            for lk in h["links"]:
                # Admin-confirmed: the device depends on this host (provenance manual).
                edges.append(Edge(f"device:{lk['device_id']}", uid, "depends_on", "manual",
                                  1.0, f"network_host:{hid}"))
        for tid, t in self.net.targets.items():
            if t.get("host_id") in self.hosts:
                edges.append(Edge(f"host:{t['host_id']}", f"endpoint:{tid}", "uses_endpoint",
                                  "manual", 1.0, f"network_target:{tid}"))
        return nodes, edges

    # --- Mutations (audited) -----------------------------------------------------------

    async def save(self, *, host_id: str | None, display_name: str, mac: str | None,
                   hostname: str | None, addresses: list[str], actor: str) -> dict[str, Any]:
        spec = validate_host(display_name, mac, hostname, addresses)
        now = self.rt.clock.now_us()
        hid = host_id or new_uuid()

        def write(conn):
            with _tx(conn):
                if host_id is None:
                    if conn.execute("SELECT COUNT(*) FROM network_hosts").fetchone()[0] \
                            >= MAX_HOSTS:
                        raise SentinelError(ErrorCode.CONFLICT, "too many hosts",
                                            limit=MAX_HOSTS)
                    conn.execute("INSERT INTO network_hosts(id, display_name, source, "
                                 "created_by, first_seen_us, last_seen_us, updated_us) "
                                 "VALUES (?,?,'manual',?,?,?,?)",
                                 (hid, spec.display_name, actor, now, now, now))
                elif conn.execute("UPDATE network_hosts SET display_name=?, updated_us=?, "
                                  "last_seen_us=? WHERE id=?",
                                  (spec.display_name, now, now, hid)).rowcount == 0:
                    raise SentinelError(ErrorCode.NOT_FOUND, "host not found")
                conn.execute("DELETE FROM network_host_keys WHERE host_id=?", (hid,))
                for kind, value in (("mac", spec.mac), ("hostname", spec.hostname)):
                    if value is None:
                        continue
                    try:
                        conn.execute("INSERT INTO network_host_keys(host_id, kind, value) "
                                     "VALUES (?,?,?)", (hid, kind, value))
                    except sqlite3.IntegrityError as err:
                        # Same identity on another host: never merge automatically.
                        raise SentinelError(ErrorCode.CONFLICT, "identity key in use",
                                            reason="identity_conflict", param=kind) from err
                # Addresses change in place: same host, new IP (AC NET 01).
                conn.execute("DELETE FROM network_host_addresses WHERE host_id=?", (hid,))
                for family, value in spec.addresses:
                    conn.execute("INSERT INTO network_host_addresses(host_id, family, value, "
                                 "source, updated_us) VALUES (?,?,?,'manual',?)",
                                 (hid, family, value, now))
                insert_audit(conn, actor, hid,
                             "network.host.create" if host_id is None else "network.host.update",
                             hid, "ok", now)

        await self.rt.db.run(write)
        await self._changed()
        return self.host_api(self.hosts[hid])

    async def delete(self, host_id: str, actor: str) -> None:
        now = self.rt.clock.now_us()

        def remove(conn):
            with _tx(conn):
                if conn.execute("DELETE FROM network_hosts WHERE id=?", (host_id,)).rowcount == 0:
                    raise SentinelError(ErrorCode.NOT_FOUND, "host not found")
                conn.execute("UPDATE network_targets SET host_id=NULL WHERE host_id=?",
                             (host_id,))
                insert_audit(conn, actor, host_id, "network.host.delete", host_id, "ok", now)

        await self.rt.db.run(remove)
        await self._changed()

    async def link(self, host_id: str, device_id: str, actor: str, *, unlink: bool) -> None:
        if host_id not in self.hosts:
            raise SentinelError(ErrorCode.NOT_FOUND, "host not found")
        if not unlink and dr.async_get(self.hass).async_get(device_id) is None:
            raise SentinelError(ErrorCode.NOT_FOUND, "unknown device")
        h = self.hosts[host_id]
        match = next((s for s in suggest(_host_input(h), self._devices(), set())
                      if s.device_id == device_id), None)
        evidence = ",".join(match.reasons) if match else "manual"
        now = self.rt.clock.now_us()

        def write(conn):
            with _tx(conn):
                if unlink:
                    n = conn.execute("DELETE FROM network_host_links WHERE host_id=? AND "
                                     "device_id=?", (host_id, device_id)).rowcount
                    if n == 0:
                        raise SentinelError(ErrorCode.NOT_FOUND, "link not found")
                else:
                    if len(h["links"]) >= MAX_LINKS_PER_HOST:
                        raise SentinelError(ErrorCode.CONFLICT, "too many links",
                                            limit=MAX_LINKS_PER_HOST)
                    conn.execute("INSERT OR IGNORE INTO network_host_links(host_id, device_id, "
                                 "evidence, confirmed_by, confirmed_us) VALUES (?,?,?,?,?)",
                                 (host_id, device_id, evidence, actor, now))
                insert_audit(conn, actor, host_id,
                             "network.host.unlink" if unlink else "network.host.link",
                             device_id, "ok", now)

        await self.rt.db.run(write)
        await self._changed()

    async def _changed(self) -> None:
        await self.async_load()
        if self.rt.config is not None:
            self.rt.config.schedule_graph_rebuild()
        self.rt.bump()
