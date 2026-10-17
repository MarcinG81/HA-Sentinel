"""Per-object rule overrides and DOC005 heartbeat contracts (8.1, 27.1, AC DOC 03, T06)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ha_sentinel.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
)


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options={**DEFAULT_OPTIONS, OPT_DOCTOR_STARTUP_GRACE_S: 30,
                                     OPT_DOCTOR_UNAVAILABLE_S: 30})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["runtime"]


async def _tick(hass, freezer, seconds=10, n=1, report=None):
    for _ in range(n):
        freezer.tick(timedelta(seconds=seconds))
        if report:
            hass.states.async_set(report, "21.0")  # unchanged value: state_reported only
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _active(runtime, rule):
    return [f for f in runtime.doctor.active_findings() if f.rule_id == rule]


async def test_heartbeat_contract_end_to_end(hass, freezer):
    # A registry entry gives a stable identity, so the contract survives a reload.
    er.async_get(hass).async_get_or_create("sensor", "demo", "temp",
                                           suggested_object_id="temp")
    hass.states.async_set("sensor.temp", "21.0")
    entry, runtime = await _setup(hass)
    await _tick(hass, freezer, 40)
    uid = runtime.inventory.uid_for_alias("sensor.temp")

    # AC DOC 03: an unchanged value without a contract never becomes "stale".
    await _tick(hass, freezer, 3600, n=3)
    assert _active(runtime, "DOC005") == []

    await runtime.rules.set(rule_id="DOC005", subject_uid=uid, enabled=True,
                            params={"period_s": 60}, actor="admin")
    assert runtime._heartbeat_unsub is not None
    await _tick(hass, freezer, 30, n=12, report="sensor.temp")  # reports, same value
    assert _active(runtime, "DOC005") == []

    await _tick(hass, freezer, 30, n=7)  # silence > 3 x 60 s
    assert [f.subject_uid for f in _active(runtime, "DOC005")] == [uid]
    assert runtime.active_incidents == 1

    await _tick(hass, freezer, 30, report="sensor.temp")
    assert _active(runtime, "DOC005") == []

    # The contract survives a reload and keeps its subscription.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.doctor.heartbeat_subjects == frozenset({uid})
    assert "sensor.temp" in runtime._heartbeat_aliases


async def test_rules_api_disable_and_validation(hass, hass_ws_client, freezer):
    hass.states.async_set("switch.pump", "on")
    _, runtime = await _setup(hass)
    await _tick(hass, freezer, 40)
    uid = runtime.inventory.uid_for_alias("switch.pump")
    client = await hass_ws_client(hass)

    bad = await _call(client, type=f"{DOMAIN}/rules/set", rule_id="DOC001", subject_uid=uid,
                      enabled=True, params={"unavailable_s": 5}, request_key=str(uuid.uuid4()))
    assert bad["error"]["code"] == "validation_failed"
    missing = await _call(client, type=f"{DOMAIN}/rules/set", rule_id="DOC001",
                          subject_uid="no-such-uid", enabled=False,
                          request_key=str(uuid.uuid4()))
    assert missing["error"]["code"] == "not_found"

    ok = await _call(client, type=f"{DOMAIN}/rules/set", rule_id="DOC001", subject_uid=uid,
                     enabled=False, request_key=str(uuid.uuid4()))
    assert ok["success"] and ok["result"]["subject_label"] == "switch.pump"
    listed = (await _call(client, type=f"{DOMAIN}/rules/list"))["result"]
    assert [i["id"] for i in listed["items"]] == [ok["result"]["id"]]
    assert listed["ranges"]["DOC001"]["unavailable_s"] == [30, 3600]

    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    assert _active(runtime, "DOC001") == []  # disabled for this subject only

    client = await hass_ws_client(hass)  # time jumps close an idle test client
    gone = await _call(client, type=f"{DOMAIN}/rules/delete", override_id=ok["result"]["id"],
                       request_key=str(uuid.uuid4()))
    assert gone["success"]
    hass.states.async_set("switch.pump", "on")
    await _tick(hass, freezer, 40)
    hass.states.async_set("switch.pump", STATE_UNAVAILABLE)
    await _tick(hass, freezer, 30, n=4)
    assert [f.subject_uid for f in _active(runtime, "DOC001")] == [uid]
    audit = await runtime.db.run(lambda c: [r[0] for r in c.execute(
        "SELECT operation FROM audit WHERE operation LIKE 'rules.%' ORDER BY created_us")])
    assert audit == ["rules.set", "rules.delete"]


async def test_override_of_registry_less_entity_stays_with_its_tombstone(hass, freezer):
    hass.states.async_set("sensor.legacy", "1")  # no unique_id: identity is per boot
    entry, runtime = await _setup(hass)
    await _tick(hass, freezer, 40)
    old_uid = runtime.inventory.uid_for_alias("sensor.legacy")
    await runtime.rules.set(rule_id="DOC001", subject_uid=old_uid, enabled=False, params={},
                            actor="admin")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.inventory.uid_for_alias("sensor.legacy") != old_uid  # new identity
    assert runtime.inventory.get(old_uid).deleted_at_us is not None  # tombstone
    item = runtime.rules.override_api(runtime.rules.overrides[0])
    assert item["subject_uid"] == old_uid and item["subject_deleted"] is True
