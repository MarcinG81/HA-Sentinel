"""Export, download authorisation and data deletion on a real HA instance (T19, spec 17.1)."""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
)

CANARY_NAME = "CanaryName-91c2"
CANARY_SECRET = "CanarySecret-55d0"
CANARY_ALIAS = "sensor.canary_alias_7b"


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


@pytest.fixture
async def setup(hass, hass_ws_client, freezer):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    hass.states.async_set(CANARY_ALIAS, "1", {"friendly_name": CANARY_NAME})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    hass.states.async_set(CANARY_ALIAS, STATE_UNAVAILABLE, {"friendly_name": CANARY_NAME})
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    for _ in range(12):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        if runtime.active_incidents:
            break
    assert runtime.active_incidents == 1
    # Open the WebSocket only after advancing time: HA's server heartbeat would otherwise
    # close an idle test client while timers are fired in bulk.
    client = await hass_ws_client(hass)

    yaml_text = (f"- alias: Uses {CANARY_ALIAS}\n  triggers:\n    - trigger: state\n"
                 f"      entity_id: {CANARY_ALIAS}\n  actions:\n    - action: notify.x\n"
                 f"      data:\n        password: {CANARY_SECRET}\n")
    imported = await _call(client, type=f"{DOMAIN}/import/create", content_utf8=yaml_text,
                           content_hash="sha256:" + hashlib.sha256(yaml_text.encode()).hexdigest(),
                           resource_kind="automation", label="automations.yaml",
                           redact_secrets=True, request_key=str(uuid.uuid4()))
    assert imported["success"], imported

    return entry, client


async def _export(hass, client, redacted: bool) -> str:
    started = await _call(client, type=f"{DOMAIN}/export/create",
                          scopes=["diagnostics", "incidents", "config_history"],
                          redacted=redacted, request_key=str(uuid.uuid4()))
    assert started["success"], started
    job_id = started["result"]["job_id"]
    for _ in range(50):
        await hass.async_block_till_done()
        job = (await _call(client, type=f"{DOMAIN}/jobs/get", job_id=job_id))["result"]
        if job["state"] in ("succeeded", "failed", "cancelled"):
            break
    assert job["state"] == "succeeded", job
    assert "file" not in job["result"]  # no server paths leak
    return job["result"]["export_id"]


async def test_t19_redacted_export_contains_no_canaries(hass, setup, hass_client):
    _, client = setup
    export_id = await _export(hass, client, redacted=True)
    http = await hass_client()
    resp = await http.get(f"/api/ha_sentinel/exports/{export_id}")
    assert resp.status == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Content-Disposition"].startswith("attachment;")
    data = await resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names)
    assert "manifest.json" in names and "incidents.json" in names
    for canary in (CANARY_NAME, CANARY_SECRET, CANARY_ALIAS, hass.config.config_dir):
        assert canary.encode() not in blob, canary


async def test_unredacted_export_keeps_yaml_but_never_the_plain_secret(hass, setup,
                                                                       hass_client):
    _, client = setup
    export_id = await _export(hass, client, redacted=False)
    data = await (await (await hass_client()).get(f"/api/ha_sentinel/exports/{export_id}")).read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist())
    assert CANARY_ALIAS.encode() in blob  # the admin asked for unredacted data
    assert CANARY_SECRET.encode() not in blob  # redacted at import, never stored


async def test_download_requires_admin_and_existing_export(hass, setup, hass_client,
                                                           hass_read_only_access_token):
    _, client = setup
    export_id = await _export(hass, client, redacted=True)
    reader = await hass_client(hass_read_only_access_token)
    assert (await reader.get(f"/api/ha_sentinel/exports/{export_id}")).status == 403
    admin = await hass_client()
    assert (await admin.get(f"/api/ha_sentinel/exports/{uuid.uuid4()}")).status == 404
    assert (await admin.get("/api/ha_sentinel/exports/not-a-uuid")).status == 404


async def test_delete_scopes_and_all(hass, setup):
    entry, client = setup
    bad = await _call(client, type=f"{DOMAIN}/data/delete", scope="config_history",
                      confirm="yes", request_key=str(uuid.uuid4()))
    assert bad["error"]["code"] == "invalid_request"

    res = await _call(client, type=f"{DOMAIN}/data/delete", scope="config_history",
                      confirm="config_history", request_key=str(uuid.uuid4()))
    assert res["result"]["deleted"]["snapshots"] >= 1
    items = (await _call(client, type=f"{DOMAIN}/resources/list"))["result"]["items"]
    assert items == []

    # Active failures are facts: deleting incidents re-derives the current one.
    await _call(client, type=f"{DOMAIN}/data/delete", scope="incidents", confirm="incidents",
                request_key=str(uuid.uuid4()))
    assert hass.data[DOMAIN]["runtime"].active_incidents == 1

    res = await _call(client, type=f"{DOMAIN}/data/delete", scope="all", confirm="all",
                      request_key=str(uuid.uuid4()))
    assert res["result"]["scheduled"] is True
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    status = (await _call(client, type=f"{DOMAIN}/data/status"))["result"]
    assert status["storage"]["observations"] < 10  # fresh DB after the wipe
    assert runtime.active_incidents == 0
    assert entry.state.value == "loaded"
