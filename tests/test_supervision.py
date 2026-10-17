"""Supervised writer (spec 4.3): repeated failures pause it until an admin resumes."""

from __future__ import annotations

import uuid

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.ha_sentinel.repositories import repository as repo


async def test_writer_pauses_after_failures_and_resumes(hass, hass_ws_client, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]

    real_insert = repo.insert_observations

    def broken(conn, batch):
        raise OSError("disk I/O error")

    monkeypatch.setattr(repo, "insert_observations", broken)
    monkeypatch.setattr(runtime._supervisor, "failed", lambda now: None)  # pause at once
    hass.states.async_set("sensor.x", "1")
    runtime._wake.set()
    for _ in range(20):
        await hass.async_block_till_done()
        if runtime.reason_code == "writer_paused":
            break
    assert runtime.state.value == "failed" and runtime.reason_code == "writer_paused"
    assert runtime.metrics.counters["module_failures_total{writer}"] >= 1

    client = await hass_ws_client(hass)
    monkeypatch.setattr(repo, "insert_observations", real_insert)
    res = await _call(client, f"{DOMAIN}/modules/resume")
    assert res["success"], res
    hass.states.async_set("sensor.x", "2")
    await runtime.async_flush()
    assert runtime.state.value == "ready"
    again = await _call(client, f"{DOMAIN}/modules/resume")
    assert again["error"]["code"] == "conflict"


async def _call(client, kind):
    await client.send_json_auto_id({"type": kind, "module": "writer",
                                    "request_key": str(uuid.uuid4())})
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply
