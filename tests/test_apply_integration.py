"""Managed file restore on a real HA instance with a real automation.reload (B20)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant import config as conf_util
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_MANAGED_WRITE_ENABLED,
)

V1 = "- id: heat_on\n  alias: Heat on\n  triggers:\n    - trigger: event\n      event_type: x\n" \
     "  actions: []\n"
V2 = V1.replace("Heat on", "Heat on edited").replace("event_type: x", "event_type: y")


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _wait_job(hass, client, job_id):
    for _ in range(50):
        await hass.async_block_till_done()
        job = (await _call(client, type=f"{DOMAIN}/jobs/get", job_id=job_id))["result"]
        if job["state"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return job
    raise AssertionError("job did not finish")


@pytest.fixture
async def env(hass, hass_ws_client):
    cfg = Path(hass.config.config_dir)
    managed = cfg / "ha_sentinel_managed"
    managed.mkdir()
    target = managed / "automations.yaml"
    target.write_text(V1)
    target.chmod(0o644)
    (cfg / "configuration.yaml").write_text(
        "automation: !include ha_sentinel_managed/automations.yaml\n")
    conf = await conf_util.async_hass_config_yaml(hass)
    assert await async_setup_component(hass, "automation", conf)
    await hass.async_block_till_done()

    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_MANAGED_WRITE_ENABLED: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    reg = await _call(client, type=f"{DOMAIN}/managed/register",
                      relative_path="ha_sentinel_managed/automations.yaml", label="Managed",
                      exclusive_owner_confirmed=True, request_key=str(uuid.uuid4()))
    assert reg["success"], reg
    return client, target, reg["result"]


async def _snapshots(client, rid):
    return (await _call(client, type=f"{DOMAIN}/snapshots/list", resource_id=rid))["result"][
        "items"]


async def test_restore_previous_version_with_reload(hass, env):
    client, target, reg = env
    rid, v1_snapshot = reg["resource_id"], reg["snapshot_id"]
    target.write_text(V2)  # edited outside Sentinel
    synced = await _call(client, type=f"{DOMAIN}/managed/sync", resource_id=rid,
                         request_key=str(uuid.uuid4()))
    assert synced["success"] and not synced["result"]["deduplicated"]
    assert all(s["restorable"] for s in await _snapshots(client, rid))

    preview = (await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=rid,
                           snapshot_id=v1_snapshot))["result"]
    assert preview["diff"]["semantic"], preview
    assert preview["impact"]["automations"] == ["Heat on"]
    assert preview["expected_ids"] == ["heat_on"]

    started = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=preview["plan_id"],
                          plan_hash=preview["plan_hash"], request_key=str(uuid.uuid4()))
    job = await _wait_job(hass, client, started["result"]["job_id"])
    assert job["result"]["plan_state"] == "applied", job
    assert target.read_text() == V1
    state = next(s for s in hass.states.async_all("automation")
                 if s.attributes.get("id") == "heat_on")
    assert state.attributes["friendly_name"] == "Heat on"  # reloaded content is live

    plan = (await _call(client, type=f"{DOMAIN}/changes/get",
                        plan_id=preview["plan_id"]))["result"]
    assert plan["state"] == "applied"
    # A second apply of the same plan is refused: no double effect.
    again = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=preview["plan_id"],
                        plan_hash=preview["plan_hash"], request_key=str(uuid.uuid4()))
    assert again["error"]["code"] == "conflict"


async def test_t12_file_changed_after_preview_conflicts_without_write(hass, env):
    client, target, reg = env
    rid = reg["resource_id"]
    target.write_text(V2)
    await _call(client, type=f"{DOMAIN}/managed/sync", resource_id=rid,
                request_key=str(uuid.uuid4()))
    preview = (await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=rid,
                           snapshot_id=reg["snapshot_id"]))["result"]
    target.write_text(V2 + "# someone else\n")
    started = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=preview["plan_id"],
                          plan_hash=preview["plan_hash"], request_key=str(uuid.uuid4()))
    job = await _wait_job(hass, client, started["result"]["job_id"])
    assert job["result"]["plan_state"] == "conflict"
    assert target.read_text().endswith("# someone else\n")


async def test_scenario_22_5_expired_plan_and_wrong_hash(hass, env, freezer):
    client, target, reg = env
    rid = reg["resource_id"]
    target.write_text(V2)
    await _call(client, type=f"{DOMAIN}/managed/sync", resource_id=rid,
                request_key=str(uuid.uuid4()))
    preview = (await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=rid,
                           snapshot_id=reg["snapshot_id"]))["result"]
    wrong = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=preview["plan_id"],
                        plan_hash="sha256:" + "1" * 64, request_key=str(uuid.uuid4()))
    assert wrong["error"]["code"] == "conflict"
    freezer.tick(timedelta(minutes=11))
    expired = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=preview["plan_id"],
                          plan_hash=preview["plan_hash"], request_key=str(uuid.uuid4()))
    assert expired["error"]["code"] == "plan_expired"
    assert target.read_text() == V2  # nothing written


async def test_registration_rejects_unsafe_files(hass, env):
    client, target, _ = env
    cfg = Path(hass.config.config_dir)
    (cfg / "configuration.yaml").chmod(0o644)
    outside = await _call(client, type=f"{DOMAIN}/managed/register",
                          relative_path="configuration.yaml", label="x",
                          exclusive_owner_confirmed=True, request_key=str(uuid.uuid4()))
    assert outside["error"]["details"]["reason"] == "outside_allowlist"
    traversal = await _call(client, type=f"{DOMAIN}/managed/register",
                            relative_path="ha_sentinel_managed/../configuration.yaml",
                            label="x", exclusive_owner_confirmed=True,
                            request_key=str(uuid.uuid4()))
    assert traversal["error"]["details"]["reason"] == "traversal"
    (target.parent / "other.yaml").write_text(V1)
    (target.parent / "other.yaml").chmod(0o644)
    unconfirmed = await _call(client, type=f"{DOMAIN}/managed/register",
                              relative_path="ha_sentinel_managed/other.yaml", label="x",
                              exclusive_owner_confirmed=False, request_key=str(uuid.uuid4()))
    assert unconfirmed["error"]["details"]["reason"] == "ownership_not_confirmed"


async def test_export_only_snapshots_cannot_be_restored_via_api(hass, env):
    client, _, _ = env
    text = "- alias: imported\n  triggers: []\n  actions: []\n"
    import hashlib

    imp = (await _call(client, type=f"{DOMAIN}/import/create", content_utf8=text,
                       content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                       resource_kind="automation", label="imported",
                       request_key=str(uuid.uuid4())))["result"]
    res = await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=imp["resource_id"],
                      snapshot_id=imp["snapshot_id"])
    assert res["error"]["code"] == "unsupported_capability"  # AC HK 04


async def test_no_change_broken_file_and_duplicate_registration(hass, env):
    client, target, reg = env
    rid = reg["resource_id"]
    same = await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=rid,
                       snapshot_id=reg["snapshot_id"])
    assert same["error"]["details"]["reason"] == "no_change"
    dup = await _call(client, type=f"{DOMAIN}/managed/register",
                      relative_path="ha_sentinel_managed/automations.yaml", label="again",
                      exclusive_owner_confirmed=True, request_key=str(uuid.uuid4()))
    assert dup["error"]["code"] == "conflict"
    target.write_text("- alias: [broken\n")
    synced = await _call(client, type=f"{DOMAIN}/managed/sync", resource_id=rid,
                         request_key=str(uuid.uuid4()))
    assert synced["success"]  # a broken file is still history, marked not restorable
    snaps = await _snapshots(client, rid)
    assert snaps[0]["restorable"] is False and snaps[0]["validation"]["parser"] == "invalid"
    # The broken current file can still be replaced by a good snapshot.
    preview = await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=rid,
                          snapshot_id=reg["snapshot_id"])
    assert preview["success"], preview


async def test_ac_hk_03_crash_after_write_is_recovered_on_start(hass, env, hass_ws_client):
    client, target, reg = env
    rid = reg["resource_id"]
    target.write_text(V2)
    await _call(client, type=f"{DOMAIN}/managed/sync", resource_id=rid,
                request_key=str(uuid.uuid4()))
    preview = (await _call(client, type=f"{DOMAIN}/changes/preview", resource_id=rid,
                           snapshot_id=reg["snapshot_id"]))["result"]
    runtime = hass.data[DOMAIN]["runtime"]
    # Simulate a crash right after the atomic replace: journal says "prepared", the file
    # already holds the target, and the plan is stuck in "applying".
    target.write_text(V1)

    def stuck(conn):
        conn.execute("UPDATE change_plans SET state='applying' WHERE id=?",
                     (preview["plan_id"],))
        conn.execute("INSERT INTO operations(id, plan_id, phase, before_hash, updated_us) "
                     "VALUES (?,?,?,?,0)", (str(uuid.uuid4()), preview["plan_id"], "prepared",
                                           preview["base_hash"]))
    await runtime.db.run(stuck)
    entry = runtime.entry
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    plan = (await _call(client, type=f"{DOMAIN}/changes/get",
                        plan_id=preview["plan_id"]))["result"]
    assert plan["state"] == "manual_reload_required"  # never applied a second time
    assert target.read_text() == V1

