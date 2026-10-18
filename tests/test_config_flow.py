"""Config and options flows through the real HTTP API, as the frontend uses them."""

from __future__ import annotations

import uuid

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN


async def test_user_flow_creates_single_entry(hass, hass_client):
    # Views must be registered before the test client freezes the HTTP router.
    for component in ("config", "frontend", "panel_custom", "websocket_api"):
        assert await async_setup_component(hass, component, {})
    client = await hass_client()
    resp = await client.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    assert resp.status == 200, await resp.text()
    flow = await resp.json()
    assert flow["type"] == "form"
    resp = await client.post(f"/api/config/config_entries/flow/{flow['flow_id']}", json={})
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    resp = await client.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    assert (await resp.json())["reason"] == "single_instance_allowed"


async def test_options_flow_form_serialises_and_validates(hass, hass_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.t", "1")
    hass.states.async_set("switch.s", "on")
    client = await hass_client()
    # Serialising the schema for the frontend must work (a custom validator broke it).
    resp = await client.post("/api/config/config_entries/options/flow",
                             json={"handler": entry.entry_id})
    assert resp.status == 200, await resp.text()
    flow = await resp.json()
    sections = {f["name"]: f for f in flow["data_schema"]}
    assert set(sections) == {"modules", "ingestion", "doctor", "storage", "managed", "network"}
    assert all(f["type"] == "expandable" for f in sections.values())
    fields = {name: {x["name"] for x in f["schema"]} for name, f in sections.items()}
    assert {"network_active_probes", "network_allowlist"} <= fields["network"]
    assert "ingestion_allowed_domains" in fields["ingestion"]

    data = {name: {k: DEFAULT_OPTIONS[k] for k in keys} for name, keys in fields.items()}
    data["managed"]["managed_dir"] = "../escape"
    resp = await client.post(f"/api/config/config_entries/options/flow/{flow['flow_id']}",
                             json=data)
    assert (await resp.json())["errors"] == {"base": "invalid_managed_dir"}
    data["managed"]["managed_dir"] = DEFAULT_OPTIONS["managed_dir"]
    data["network"]["network_allowlist"] = "not-a-cidr"
    resp = await client.post(f"/api/config/config_entries/options/flow/{flow['flow_id']}",
                             json=data)
    body = await resp.json()
    assert body["type"] == "form" and body["errors"] == {"base": "invalid_cidr"}

    data["network"]["network_allowlist"] = "192.168.1.0/24"
    data["network"]["network_active_probes"] = True
    data["ingestion"]["ingestion_allowed_domains"] = ["sensor", "switch"]
    resp = await client.post(f"/api/config/config_entries/options/flow/{flow['flow_id']}",
                             json=data)
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["network_active_probes"] is True  # stored flat, not per section
    assert entry.options["ingestion_allowed_domains"] == ["sensor", "switch"]
    runtime = hass.data[DOMAIN]["runtime"]  # the entry reloaded with the new options
    assert runtime.allowed_domains == frozenset({"sensor", "switch"})
    assert runtime.network.active is True
