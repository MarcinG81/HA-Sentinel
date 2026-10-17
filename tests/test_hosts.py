"""N0 hosts through the API: AC NET 01, identity conflicts, admin-only links (13.2)."""

from __future__ import annotations

import uuid

from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


def _key() -> str:
    return str(uuid.uuid4())


async def test_hosts_identity_links_and_graph(hass, hass_ws_client):
    hub = MockConfigEntry(domain="synology_dsm", title="NAS")
    hub.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=hub.entry_id, identifiers={("synology_dsm", "nas")}, name="Synology",
        connections={(dr.CONNECTION_NETWORK_MAC, "AA:BB:CC:DD:EE:FF")},
        configuration_url="http://192.168.1.10:5000")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": _key()},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    client = await hass_ws_client(hass)

    created = await _call(client, type=f"{DOMAIN}/network/hosts/save", display_name="NAS",
                          mac="aa-bb-cc-dd-ee-ff", addresses=["192.168.1.10"],
                          request_key=_key())
    assert created["success"], created
    host = created["result"]
    assert host["links"] == []  # a matching MAC is a suggestion, never a link
    assert host["suggestions"][0]["device_id"] == dev.id
    assert set(host["suggestions"][0]["reasons"]) == {"mac_match", "ip_match"}
    assert host["reachability"] == "not_probed" and runtime.network.probes_started == 0

    # AC NET 01: a new IP for the same confirmed identity keeps the host.
    moved = await _call(client, type=f"{DOMAIN}/network/hosts/save", host_id=host["id"],
                        display_name="NAS", mac="aa:bb:cc:dd:ee:ff",
                        addresses=["192.168.1.77"], request_key=_key())
    assert moved["result"]["id"] == host["id"]
    assert moved["result"]["addresses"] == [{"family": "ipv4", "value": "192.168.1.77"}]

    clash = await _call(client, type=f"{DOMAIN}/network/hosts/save", display_name="Other",
                        mac="AA:BB:CC:DD:EE:FF", request_key=_key())
    assert clash["error"]["code"] == "conflict"
    assert clash["error"]["details"]["reason"] == "identity_conflict"

    linked = await _call(client, type=f"{DOMAIN}/network/hosts/link", host_id=host["id"],
                         device_id=dev.id, request_key=_key())
    assert linked["success"], linked
    listed = (await _call(client, type=f"{DOMAIN}/network/hosts/list"))["result"]["items"]
    assert listed[0]["links"] == [{"device_id": dev.id, "name": "Synology", "missing": False}]
    assert listed[0]["suggestions"] == []

    await runtime.config.async_rebuild_graph()
    edges = {(e.from_uid, e.to_uid, e.relation, e.provenance)
             for e in runtime.config.graph._out.get(f"device:{dev.id}", [])}
    assert (f"device:{dev.id}", f"host:{host['id']}", "depends_on", "manual") in edges

    gone = await _call(client, type=f"{DOMAIN}/network/hosts/delete", host_id=host["id"],
                       request_key=_key())
    assert gone["success"]
    assert (await _call(client, type=f"{DOMAIN}/network/hosts/list"))["result"]["items"] == []
    audit = await runtime.db.run(lambda c: [r[0] for r in c.execute(
        "SELECT operation FROM audit WHERE operation LIKE 'network.host.%' "
        "ORDER BY created_us")])
    assert audit == ["network.host.create", "network.host.update", "network.host.link",
                     "network.host.delete"]
