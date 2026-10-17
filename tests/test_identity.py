"""Registry reconciliation (5.1) and suggested identity continuity (6.1)."""

from __future__ import annotations

import uuid

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def test_reconcile_and_identity_suggestion(hass, hass_ws_client, monkeypatch):
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "zha", "old-id", suggested_object_id="garden_temp")
    hass.states.async_set("sensor.garden_temp", "12")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    old_uid = runtime.inventory.uid_for_alias("sensor.garden_temp")
    await runtime.rules.set(rule_id="DOC001", subject_uid=old_uid, enabled=False, params={},
                            actor="admin")

    # A missed registry event: the periodic reconciliation still tombstones the entity.
    monkeypatch.setattr(runtime.inventory, "remove", lambda *_a: None)  # event "lost"
    reg.async_remove("sensor.garden_temp")
    await hass.async_block_till_done()
    monkeypatch.undo()
    hass.states.async_remove("sensor.garden_temp")
    await hass.async_block_till_done()
    assert runtime.inventory.get(old_uid).deleted_at_us is None  # not seen yet
    assert runtime._reconcile_registries() >= 1
    assert runtime.inventory.get(old_uid).deleted_at_us is not None

    # Re-added with a new unique_id and the same entity_id: only a suggestion.
    reg.async_get_or_create("sensor", "zha", "new-id", suggested_object_id="garden_temp")
    hass.states.async_set("sensor.garden_temp", "13")
    await hass.async_block_till_done()
    await runtime.async_flush()
    new_uid = runtime.inventory.uid_for_alias("sensor.garden_temp")
    assert new_uid != old_uid
    client = await hass_ws_client(hass)
    items = (await _call(client, type=f"{DOMAIN}/identity/list"))["result"]["items"]
    assert [(i["old"]["uid"], i["new"]["uid"]) for i in items] == [(old_uid, new_uid)]
    assert runtime.rules.overrides[0].subject_uid == old_uid  # nothing moved yet

    res = await _call(client, type=f"{DOMAIN}/identity/decide", suggestion_id=items[0]["id"],
                      confirm=True, request_key=str(uuid.uuid4()))
    assert res["success"] and res["result"]["moved"]["rule_overrides"] == 1
    assert runtime.rules.overrides[0].subject_uid == new_uid
    assert (await _call(client, type=f"{DOMAIN}/identity/list"))["result"]["items"] == []
    again = await _call(client, type=f"{DOMAIN}/identity/decide", suggestion_id=items[0]["id"],
                        confirm=False, request_key=str(uuid.uuid4()))
    assert again["error"]["code"] == "not_found"
    diag = await runtime.diagnostics()
    assert diag["registry_synced_at"] is not None
