"""Policy and failure paths of the API (spec 15.2, 15.3, 16.2)."""

from __future__ import annotations

import uuid
from pathlib import Path

from homeassistant import config as conf_util
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_MANAGED_WRITE_ENABLED,
)
from custom_components.ha_sentinel.core.models import (
    CapabilityStatus,
    Finding,
    FindingStatus,
    Severity,
    new_uuid,
)
from custom_components.ha_sentinel.modules.correlation.correlator import IncidentPlan
from custom_components.ha_sentinel.repositories import repository as repo

V1 = "- id: a\n  alias: A\n  triggers: []\n  actions: []\n"
V2 = V1.replace("alias: A", "alias: B")


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _setup(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["runtime"]


async def test_internal_error_has_request_id_and_no_stack(hass, hass_ws_client, monkeypatch):
    _, runtime = await _setup(hass)

    def boom():
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(runtime, "overview", boom)
    client = await hass_ws_client(hass)
    err = (await _call(client, type=f"{DOMAIN}/overview"))["error"]
    assert err["code"] == "internal_error" and "request_id=" in err["message"]
    assert "secret internal detail" not in str(err) and "Traceback" not in str(err)


async def test_invalid_uuid_and_read_only_storage(hass, hass_ws_client):
    _, runtime = await _setup(hass)
    client = await hass_ws_client(hass)
    bad = await _call(client, type=f"{DOMAIN}/incidents/get", incident_id="not-a-uuid")
    assert bad["error"]["code"] == "invalid_format"
    runtime.capabilities["storage.write"].status = CapabilityStatus.UNAVAILABLE
    for msg in (
        {"type": f"{DOMAIN}/incidents/ack", "incident_id": new_uuid(), "expected_revision": 1},
        {"type": f"{DOMAIN}/data/delete", "scope": "observations", "confirm": "observations"},
        {"type": f"{DOMAIN}/maintenance/create", "scope_uid": "x", "minutes": 5,
         "reason": "r"},
        {"type": f"{DOMAIN}/export/create", "scopes": ["diagnostics"]},
    ):
        res = await _call(client, **msg, request_key=new_uuid())
        assert res["error"]["code"] == "storage_unavailable", msg


async def test_subscription_limit_per_user(hass, hass_ws_client):
    await _setup(hass)
    client = await hass_ws_client(hass)
    codes = [(await _call(client, type=f"{DOMAIN}/subscribe"))["success"] for _ in range(6)]
    assert codes == [True] * 5 + [False]


async def test_incident_pagination_with_cursor(hass, hass_ws_client):
    _, runtime = await _setup(hass)

    def seed(conn):
        plans = []
        for i in range(7):
            f = Finding(id=new_uuid(), rule_id="DOC001", rule_version=1, subject_uid=f"e{i}",
                        status=FindingStatus.ACTIVE, severity=Severity.HIGH,
                        first_seen_us=i, last_seen_us=i)
            repo.upsert_finding(conn, f, i)
            plans.append(IncidentPlan(f"fp{i}", [f.id], f"e{i}", None, Severity.HIGH, i,
                                      "rule.DOC001"))
        repo.sync_incidents(conn, plans, 10)
    await runtime.db.run(seed)
    client = await hass_ws_client(hass)
    seen, cursor = [], None
    while True:
        msg = {"type": f"{DOMAIN}/incidents/list", "limit": 3}
        if cursor:
            msg["cursor"] = cursor
        page = (await _call(client, **msg))["result"]
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7
    other = await _call(client, type=f"{DOMAIN}/incidents/list", active_only=False,
                        cursor=cursor or "eyJ1IjoieCIsImEiOnRydWUsInQiOjEsImkiOiJ4In0=")
    assert other["error"]["code"] == "invalid_request"  # cursor bound to user and filters


async def _managed_env(hass, hass_ws_client):
    cfg = Path(hass.config.config_dir)
    (cfg / "ha_sentinel_managed").mkdir()
    target = cfg / "ha_sentinel_managed" / "automations.yaml"
    target.write_text(V1)
    target.chmod(0o644)
    (cfg / "configuration.yaml").write_text(
        "automation: !include ha_sentinel_managed/automations.yaml\n")
    assert await async_setup_component(hass, "automation",
                                       await conf_util.async_hass_config_yaml(hass))
    entry, runtime = await _setup(hass, **{OPT_MANAGED_WRITE_ENABLED: True})
    client = await hass_ws_client(hass)
    reg = (await _call(client, type=f"{DOMAIN}/managed/register",
                       relative_path="ha_sentinel_managed/automations.yaml", label="M",
                       exclusive_owner_confirmed=True, request_key=new_uuid()))["result"]
    target.write_text(V2)
    await _call(client, type=f"{DOMAIN}/managed/sync", resource_id=reg["resource_id"],
                request_key=new_uuid())
    preview = (await _call(client, type=f"{DOMAIN}/changes/preview",
                           resource_id=reg["resource_id"], snapshot_id=reg["snapshot_id"]))
    return entry, runtime, client, target, preview["result"]


async def _job(hass, client, job_id):
    for _ in range(40):
        await hass.async_block_till_done()
        job = (await _call(client, type=f"{DOMAIN}/jobs/get", job_id=job_id))["result"]
        if job["state"] not in ("queued", "running"):
            return job
    raise AssertionError("job did not finish")


async def test_t16_2_revoked_admin_cannot_apply_queued_plan(hass, hass_ws_client,
                                                            hass_admin_user, monkeypatch):
    _, runtime, client, target, plan = await _managed_env(hass, hass_ws_client)
    original = runtime.changes._execute

    async def demote_then_execute(plan_id, actor):
        hass_admin_user.groups = []  # admin rights revoked between acceptance and side effect
        return await original(plan_id, actor)

    monkeypatch.setattr(runtime.changes, "_execute", demote_then_execute)
    started = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=plan["plan_id"],
                          plan_hash=plan["plan_hash"], request_key=new_uuid())
    assert started["success"]
    hass_admin_user.groups = []
    for _ in range(10):
        await hass.async_block_till_done()
    assert target.read_text() == V2  # nothing written
    state = await runtime.db.run(lambda c: c.execute(
        "SELECT state FROM change_plans WHERE id=?", (plan["plan_id"],)).fetchone()[0])
    assert state == "failed"


async def test_policy_change_invalidates_plans(hass, hass_ws_client):
    entry, runtime, client, target, plan = await _managed_env(hass, hass_ws_client)
    runtime.changes.policy_revision += 1  # an options change creates a new policy revision
    started = await _call(client, type=f"{DOMAIN}/changes/apply", plan_id=plan["plan_id"],
                          plan_hash=plan["plan_hash"], request_key=new_uuid())
    job = await _job(hass, client, started["result"]["job_id"])
    assert job["result"]["plan_state"] == "expired" and job["result"]["detail"] == \
        "policy_changed"
    assert target.read_text() == V2


async def test_managed_writes_disabled_is_unsupported(hass, hass_ws_client):
    await _setup(hass)
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/managed/register", relative_path="x.yaml",
                      label="x", exclusive_owner_confirmed=True, request_key=new_uuid())
    assert res["error"]["code"] == "unsupported_capability"
    assert res["error"]["details"]["reason"] == "managed_write_disabled"
