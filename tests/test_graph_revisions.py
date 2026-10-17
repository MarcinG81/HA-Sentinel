"""Graph revisions through the API: AC MAP 05, stable revision, restart (spec 12.2)."""

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


async def test_old_revision_keeps_old_alias_and_survives_restart(hass, hass_ws_client):
    reg = er.async_get(hass)
    ent = reg.async_get_or_create("light", "demo", "hall", suggested_object_id="hall")
    hass.states.async_set("light.hall", "off")
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    cfg = runtime.config
    first = cfg.graph.revision
    await cfg.async_rebuild_graph()
    assert cfg.graph.revision == first  # nothing changed: same revision

    reg.async_update_entity(ent.entity_id, new_entity_id="light.hallway")
    hass.states.async_remove("light.hall")  # HA drops the old state on rename
    hass.states.async_set("light.hallway", "off")
    await hass.async_block_till_done()
    await cfg.async_rebuild_graph()
    second = cfg.graph.revision
    assert second == first + 1
    uid = next(n.uid for n in cfg.graph.nodes.values() if n.external_ref == "light.hallway")

    client = await hass_ws_client(hass)
    old = await _call(client, type=f"{DOMAIN}/graph/query", uid=uid, revision=first)
    assert old["success"], old
    old = old["result"]
    assert old["graph_revision"] == first and old["current_revision"] == second
    assert old["nodes"][0]["external_ref"] == "light.hall"  # AC MAP 05: old alias kept
    revs = (await _call(client, type=f"{DOMAIN}/graph/revisions"))["result"]
    assert [r["revision"] for r in revs["items"]][:2] == [second, first]
    missing = await _call(client, type=f"{DOMAIN}/graph/query", uid=uid, revision=999)
    assert missing["error"]["code"] == "not_found"
    bad = await _call(client, type=f"{DOMAIN}/graph/query", uid=uid, cursor="bm9wZQ==")
    assert bad["error"]["code"] == "invalid_request"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    cfg = hass.data[DOMAIN]["runtime"].config
    assert cfg.graph.revision == second  # sequence continues, identical graph not re-stored
