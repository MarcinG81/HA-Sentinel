"""Network discovery on a real HA instance with real sockets (B21, AC NET 02-05, T18)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.adapters.probe import ProbeResult
from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
    OPT_NETWORK_ACTIVE_PROBES,
)
from custom_components.ha_sentinel.modules.network.scheduler import Outcome


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _setup(hass, hass_ws_client, active: bool):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_NETWORK_ACTIVE_PROBES: active,
                                     OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await hass_ws_client(hass)


async def _scan(hass, client):
    started = await _call(client, type=f"{DOMAIN}/network/scan", request_key=str(uuid.uuid4()))
    assert started["success"], started
    for _ in range(50):
        await hass.async_block_till_done()
        job = (await _call(client, type=f"{DOMAIN}/jobs/get",
                           job_id=started["result"]["job_id"]))["result"]
        if job["state"] in ("succeeded", "failed"):
            return job
    raise AssertionError("scan did not finish")


async def _add(client, **target):
    return await _call(client, type=f"{DOMAIN}/network/targets/add",
                       request_key=str(uuid.uuid4()), **target)


async def test_ac_net_04_n0_never_probes(hass, hass_ws_client, freezer):
    client = await _setup(hass, hass_ws_client, active=False)
    inv = (await _call(client, type=f"{DOMAIN}/network/inventory"))["result"]
    assert inv["level"] == "N0"
    scan = await _call(client, type=f"{DOMAIN}/network/scan", request_key=str(uuid.uuid4()))
    assert scan["error"]["code"] == "unsupported_capability"
    for _ in range(5):
        freezer.tick(timedelta(minutes=2))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.data[DOMAIN]["runtime"].network.probes_started == 0


async def test_real_tcp_and_http_probes(hass, hass_ws_client, freezer):
    connections = 0

    async def tcp_handler(reader, writer):
        nonlocal connections
        connections += 1
        writer.close()

    async def http_handler(reader, writer):
        nonlocal connections
        connections += 1
        await reader.read(1024)
        # A redirect to a metadata endpoint must never be followed.
        writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest\r\n"
                     b"Content-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    tcp = await asyncio.start_server(tcp_handler, "127.0.0.1", 0)
    http = await asyncio.start_server(http_handler, "127.0.0.1", 0)
    tcp_port = tcp.sockets[0].getsockname()[1]
    http_port = http.sockets[0].getsockname()[1]
    closed = await asyncio.start_server(tcp_handler, "127.0.0.1", 0)
    closed_port = closed.sockets[0].getsockname()[1]
    closed.close()
    await closed.wait_closed()

    client = await _setup(hass, hass_ws_client, active=True)
    ok = await _add(client, label="TCP", host="127.0.0.1", port=tcp_port, kind="tcp",
                    allow_loopback=True)
    assert ok["success"], ok
    await _add(client, label="HTTP", host="127.0.0.1", port=http_port, kind="http",
               path="/status", allow_loopback=True)
    await _add(client, label="Closed", host="127.0.0.1", port=closed_port, kind="tcp",
               allow_loopback=True)
    await _add(client, label="No loopback consent", host="localhost", port=tcp_port,
               kind="tcp")

    job = await _scan(hass, client)
    assert job["result"]["probed"] == 4
    targets = {t["label"]: t for t in (await _call(
        client, type=f"{DOMAIN}/network/targets/list"))["result"]["items"]}
    assert targets["TCP"]["status"] == "reachable"
    assert targets["HTTP"]["status"] == "reachable"
    assert targets["Closed"]["last_outcome"] == "host_reachable_service_closed"
    assert targets["Closed"]["status"] == "service_closed"  # not a network failure
    assert targets["No loopback consent"]["last_outcome"] == "blocked_by_policy"
    assert connections == 2  # the redirect was not followed, the blocked one never connected

    # One attempt per target per minute: an immediate second scan probes nothing.
    assert (await _scan(hass, client))["result"]["probed"] == 0
    tcp.close()
    http.close()


@pytest.mark.parametrize(("host", "reason"), [
    ("http://192.168.1.5", "bad_host"), ("file:///etc/passwd", "bad_host"),
    ("8.8.8.8", "outside_allowlist"), ("169.254.169.254", "metadata_endpoint"),
    ("127.0.0.1", "loopback_not_allowed"), ("224.0.0.251", "multicast"),
])
async def test_t18_invalid_targets_rejected_before_any_connection(hass, hass_ws_client, host,
                                                                  reason):
    client = await _setup(hass, hass_ws_client, active=True)
    res = await _add(client, label="x", host=host, port=80, kind="http")
    assert res["error"]["details"]["reason"] == reason


async def test_unreachable_endpoint_drives_rca(hass, hass_ws_client, freezer, monkeypatch):
    other = MockConfigEntry(domain="hue", title="Hue bridge")
    other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("hue", "b")}, name="Hue bridge")
    for i in range(3):
        er.async_get(hass).async_get_or_create("light", "hue", f"l{i}",
                                               suggested_object_id=f"lamp_{i}",
                                               device_id=dev.id, config_entry=other)
        hass.states.async_set(f"light.lamp_{i}", "on")
    client = await _setup(hass, hass_ws_client, active=True)
    res = await _add(client, label="Hue bridge API", host="192.168.1.50", port=443,
                     kind="tcp", linked_device_id=dev.id)
    assert res["success"], res

    async def fake_probe(spec, allowlist):  # the bridge stopped answering
        return ProbeResult(Outcome.TIMEOUT, address="192.168.1.50")

    monkeypatch.setattr("custom_components.ha_sentinel.network_services.probe", fake_probe)
    runtime = hass.data[DOMAIN]["runtime"]
    await runtime.config.async_rebuild_graph()
    for _ in range(3):  # three consecutive failures -> unreachable
        freezer.tick(timedelta(seconds=61))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        await _scan(hass, client)
    for i in range(3):
        hass.states.async_set(f"light.lamp_{i}", "unavailable")
    for _ in range(12):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    client = await hass_ws_client(hass)  # fresh socket after bulk timer firing
    items = (await _call(client, type=f"{DOMAIN}/incidents/list"))["result"]["items"]
    infra = [i for i in items if i["kind"] == "infrastructure"]
    assert infra, items
    detail = (await _call(client, type=f"{DOMAIN}/incidents/get",
                          incident_id=infra[0]["id"]))["result"]
    labels = {h["candidate_label"]: h for h in detail["hypotheses"]}
    assert "Hue bridge API" in labels
    assert labels["Hue bridge API"]["evidence_source"] == "probe_unreachable"
    assert labels["Hue bridge API"]["features"]["E"] == 1.0
