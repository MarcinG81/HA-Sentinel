"""HA backup status and explicit backup request (spec 10.5)."""

from __future__ import annotations

import uuid

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def test_backup_request_is_audited_and_only_accepted(hass, hass_ws_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    client = await hass_ws_client(hass)

    status = (await _call(client, type=f"{DOMAIN}/backup/status"))["result"]
    assert status["service"] is None and status["settings_path"] == "/config/backup"
    res = await _call(client, type=f"{DOMAIN}/backup/create", request_key=str(uuid.uuid4()))
    assert res["error"]["code"] == "unsupported_capability"

    calls = async_mock_service(hass, "backup", "create_automatic")
    res = await _call(client, type=f"{DOMAIN}/backup/create", request_key=str(uuid.uuid4()))
    assert res["success"], res
    assert res["result"] == {"accepted": True, "service": "backup.create_automatic",
                             "settings_path": "/config/backup"}
    await hass.async_block_till_done()
    assert len(calls) == 1 and calls[0].context.user_id is not None  # HA re-checks the user
    audit = await runtime.db.run(lambda c: c.execute(
        "SELECT result FROM audit WHERE operation='backup.request'").fetchall())
    assert [r[0] for r in audit] == ["requested", "accepted"]

    from homeassistant.exceptions import HomeAssistantError

    async def refuse(call):
        raise HomeAssistantError("At least one available backup agent must be selected")

    hass.services.async_register("backup", "create_automatic", refuse)
    res = await _call(client, type=f"{DOMAIN}/backup/create", request_key=str(uuid.uuid4()))
    assert res["error"]["code"] == "conflict"
    assert res["error"]["details"] == {"reason": "backup_rejected"}  # HA text not leaked
