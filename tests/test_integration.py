"""Integration tests on a real HA instance: T01, T08, DOC001 end to end."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
)

ANY_UUID = "2c9a3f0e-5b7d-4e1a-8f6c-0d9e8b7a6c5d"
WS_COMMANDS = [
    {"type": f"{DOMAIN}/capabilities"},
    {"type": f"{DOMAIN}/overview"},
    {"type": f"{DOMAIN}/diagnostics"},
    {"type": f"{DOMAIN}/incidents/list"},
    {"type": f"{DOMAIN}/subscribe"},
]
WS_MUTATIONS_AND_LOOKUPS = [
    {"type": f"{DOMAIN}/incidents/get", "incident_id": ANY_UUID},
    {"type": f"{DOMAIN}/incidents/ack", "incident_id": ANY_UUID, "expected_revision": 1,
     "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/incidents/suppress", "incident_id": ANY_UUID, "expected_revision": 1,
     "request_key": ANY_UUID, "until": "2099-01-01T00:00:00Z", "reason": "x"},
    {"type": f"{DOMAIN}/import/create", "content_utf8": "a: 1",
     "content_hash": "sha256:" + "0" * 64, "resource_kind": "automation", "label": "x",
     "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/incidents/confirm_cause", "incident_id": ANY_UUID,
     "expected_revision": 1, "request_key": ANY_UUID, "candidate_uid": None,
     "verdict": "rejected"},
    {"type": f"{DOMAIN}/export/create", "scopes": ["diagnostics"], "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/jobs/get", "job_id": ANY_UUID},
    {"type": f"{DOMAIN}/jobs/cancel", "job_id": ANY_UUID, "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/data/status"},
    {"type": f"{DOMAIN}/data/delete", "scope": "observations", "confirm": "observations",
     "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/managed/register", "relative_path": "a.yaml", "label": "x",
     "exclusive_owner_confirmed": True, "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/managed/sync", "resource_id": ANY_UUID, "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/changes/preview", "resource_id": ANY_UUID, "snapshot_id": ANY_UUID},
    {"type": f"{DOMAIN}/changes/apply", "plan_id": ANY_UUID, "plan_hash": "sha256:" + "0" * 64,
     "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/changes/get", "plan_id": ANY_UUID},
    {"type": f"{DOMAIN}/network/inventory"},
    {"type": f"{DOMAIN}/network/targets/list"},
    {"type": f"{DOMAIN}/network/targets/add", "label": "x", "host": "h", "port": 80,
     "kind": "tcp", "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/network/targets/update", "target_id": ANY_UUID, "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/network/targets/delete", "target_id": ANY_UUID, "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/network/scan", "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/autopilot/replay", "snapshot_id": ANY_UUID},
    {"type": f"{DOMAIN}/maintenance/list"},
    {"type": f"{DOMAIN}/maintenance/create", "scope_uid": "x", "minutes": 5, "reason": "r",
     "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/maintenance/cancel", "window_id": ANY_UUID, "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/modules/resume", "module": "writer", "request_key": ANY_UUID},
    {"type": f"{DOMAIN}/resources/list"},
    {"type": f"{DOMAIN}/snapshots/list", "resource_id": ANY_UUID},
    {"type": f"{DOMAIN}/snapshots/get", "snapshot_id": ANY_UUID},
    {"type": f"{DOMAIN}/snapshots/diff", "from_snapshot_id": ANY_UUID,
     "to_snapshot_id": ANY_UUID},
    {"type": f"{DOMAIN}/autopilot/analyze", "snapshot_id": ANY_UUID},
    {"type": f"{DOMAIN}/graph/query", "uid": "x"},
    {"type": f"{DOMAIN}/graph/search", "q": "x"},
]


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        data={"instance_id": "0b7e0c0e-1d2f-4a8b-9c3d-5e6f7a8b9c0d"},
        options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                 OPT_DOCTOR_UNAVAILABLE_S: 30},
    )
    e.add_to_hass(hass)
    return e


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_t01_start_and_five_reloads_leave_one_listener(hass, entry):
    await _setup(hass, entry)
    baseline = hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)
    for _ in range(5):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0) == baseline

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0) == baseline - 1
    assert DOMAIN not in hass.data or "runtime" not in hass.data[DOMAIN]


async def test_diagnostic_entities_created(hass, entry):
    from homeassistant.helpers import entity_registry as er

    await _setup(hass, entry)
    reg = er.async_get(hass)

    def state_of(platform: str, key: str):
        entity_id = reg.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
        assert entity_id, f"{key} not registered; have {sorted(reg.entities)}"
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} has no state; have {hass.states.async_entity_ids()}"
        return state

    assert state_of("binary_sensor", "sentinel_degraded").state == "off"
    assert state_of("sensor", "active_incidents").state == "0"
    for key in ("health_score", "coverage_percent", "dropped_observations_total", "queue_depth"):
        state_of("sensor", key)


@pytest.mark.parametrize("command", WS_COMMANDS + WS_MUTATIONS_AND_LOOKUPS,
                         ids=lambda c: c["type"])
async def test_t08_non_admin_is_rejected(hass, entry, hass_ws_client, hass_admin_user, command):
    await _setup(hass, entry)
    hass_admin_user.groups = []  # demote the test user to a regular user
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(command)
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "forbidden"
    assert "result" not in msg


@pytest.mark.parametrize("command", WS_COMMANDS, ids=lambda c: c["type"])
async def test_admin_commands_succeed(hass, entry, hass_ws_client, command):
    await _setup(hass, entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(command)
    msg = await client.receive_json()
    assert msg["success"], msg


async def test_commands_without_runtime_return_not_ready(hass, entry, hass_ws_client):
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/overview"})
    msg = await client.receive_json()
    assert msg["error"]["code"] == "not_ready"


async def test_doc001_end_to_end_opens_incident(hass, entry, hass_ws_client, freezer):
    hass.states.async_set("sensor.hall_temp", "21.5")
    await _setup(hass, entry)

    freezer.tick(timedelta(seconds=40))  # past the 30 s startup grace
    async_fire_time_changed(hass)
    hass.states.async_set("sensor.hall_temp", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _wait_for_incidents(hass, freezer, 1)  # writer flush, then the 30 s DOC001 timer

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/incidents/list"})
    msg = await client.receive_json()
    assert msg["success"]
    assert [i["severity"] for i in msg["result"]["items"]] == ["high"]


async def test_t08_registered_handlers_all_covered(hass, entry):
    """Every ha_sentinel/* command registered must be in the non-admin test list."""
    await _setup(hass, entry)
    registered = {k for k in hass.data["websocket_api"] if k.startswith(f"{DOMAIN}/")}
    tested = {c["type"] for c in WS_COMMANDS + WS_MUTATIONS_AND_LOOKUPS}
    assert registered == tested


async def test_sidebar_panel_registered_admin_only_and_removed(hass, entry, hass_client):
    from homeassistant.components.frontend import DATA_PANELS

    await _setup(hass, entry)
    panel = hass.data[DATA_PANELS]["ha-sentinel"]
    assert panel.sidebar_title == "Sentinel"
    assert panel.sidebar_icon == "mdi:shield-check-outline"
    assert panel.require_admin is True
    module_url = panel.config["_panel_custom"]["module_url"]
    assert module_url.startswith("/ha_sentinel_static/ha-sentinel.js?v=")

    client = await hass_client()
    resp = await client.get(module_url)
    assert resp.status == 200
    body = await resp.text()
    assert 'customElements.define("ha-sentinel-panel"' in body

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "ha-sentinel" in hass.data[DATA_PANELS]  # re-registered, not duplicated

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "ha-sentinel" not in hass.data[DATA_PANELS]


async def _open_incident_via_doc001(hass, freezer):
    hass.states.async_set("sensor.hall_temp", "21.5")
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    hass.states.async_set("sensor.hall_temp", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    await _wait_for_incidents(hass, freezer, 1)


async def _wait_for_incidents(hass, freezer, expected: int, max_ticks: int = 12) -> None:
    """Advance time until the writer, Doctor timer and correlator have committed.

    The pipeline is eventually consistent (writer batch, deadline, DB worker); tests wait
    for the observable result instead of assuming a fixed number of ticks.
    """
    from custom_components.ha_sentinel.repositories import repository as repo

    runtime = hass.data[DOMAIN]["runtime"]
    for _ in range(max_ticks):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        if await runtime.db.run(repo.count_active_incidents) >= expected:
            return
    raise AssertionError(f"expected {expected} active incident(s)")


async def test_ack_suppress_get_and_subscription(hass, entry, hass_ws_client, freezer):
    await _setup(hass, entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    sub = await client.receive_json()
    assert sub["success"]
    sub_id = sub["id"]

    await _open_incident_via_doc001(hass, freezer)
    event = await client.receive_json()
    assert event["id"] == sub_id and event["event"]["resync_required"] is True

    async def reply() -> dict:
        while (msg := await client.receive_json())["id"] == sub_id:
            pass  # skip subscription events
        return msg

    await client.send_json_auto_id({"type": f"{DOMAIN}/incidents/list"})
    item = (await reply())["result"]["items"][0]
    assert item["subject"]["entity_id"] == "sensor.hall_temp"
    assert item["rule_id"] == "DOC001"

    await client.send_json_auto_id({"type": f"{DOMAIN}/incidents/get", "incident_id": item["id"]})
    detail = (await reply())["result"]
    evidence = detail["findings"][0]["evidence"]
    assert evidence[0]["payload"] == {"from": "available", "to": "unavailable"}
    assert detail["hypotheses"] == [] and detail["counter_evidence"] == []

    key = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
    ack = {"type": f"{DOMAIN}/incidents/ack", "incident_id": item["id"],
           "expected_revision": item["revision"], "request_key": key}
    await client.send_json_auto_id(ack)
    first = await reply()
    assert first["result"] == {"revision": 2, "status": "acknowledged"}
    await client.send_json_auto_id(ack)  # same key and content -> same result, no conflict
    assert (await reply())["result"] == first["result"]

    await client.send_json_auto_id({
        "type": f"{DOMAIN}/incidents/suppress", "incident_id": item["id"],
        "expected_revision": 1, "request_key": "8d2f3e4a-5b6c-4d7e-9f8a-0b1c2d3e4f5a",
        "until": "2099-01-01T00:00:00Z", "reason": "battery swap",
    })
    assert (await reply())["error"]["code"] == "conflict"  # stale expected_revision


async def test_unknown_fields_and_bad_cursor_rejected(hass, entry, hass_ws_client):
    await _setup(hass, entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/incidents/ack", "incident_id": ANY_UUID,
                                    "expected_revision": 1, "request_key": ANY_UUID,
                                    "force": True})
    assert (await client.receive_json())["error"]["code"] == "invalid_format"
    await client.send_json_auto_id({"type": f"{DOMAIN}/incidents/list", "cursor": "bm9wZQ=="})
    assert (await client.receive_json())["error"]["code"] == "invalid_request"


async def test_mutation_rate_limit(hass, entry, hass_ws_client):
    await _setup(hass, entry)
    client = await hass_ws_client(hass)
    codes = []
    for i in range(11):
        await client.send_json_auto_id({
            "type": f"{DOMAIN}/incidents/ack", "incident_id": ANY_UUID, "expected_revision": 1,
            "request_key": f"9e3a4b5c-6d7e-4f8a-9b0c-1d2e3f4a5b{i:02d}",
        })
        codes.append((await client.receive_json())["error"]["code"])
    assert codes[:10] == ["not_found"] * 10
    assert codes[10] == "rate_limited"
