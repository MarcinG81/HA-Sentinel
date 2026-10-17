"""DOC006 missing / stale references, finding details and restore ownership (8.1, 22.1)."""

from __future__ import annotations

import hashlib
import uuid

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.ha_sentinel.core.models import (
    Finding,
    FindingStatus,
    Severity,
    new_uuid,
)
from custom_components.ha_sentinel.repositories import repository as repo

YAML = """
- id: a1
  alias: Hall
  triggers:
    - trigger: state
      entity_id: binary_sensor.hall_door
  actions:
    - action: light.turn_on
      target:
        entity_id: [light.hall_old, light.gone]
"""


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


async def test_doc006_missing_and_stale_alias(hass, hass_ws_client, freezer):
    reg = er.async_get(hass)
    hall = reg.async_get_or_create("light", "demo", "hall", suggested_object_id="hall_old")
    reg.async_get_or_create("binary_sensor", "demo", "door", suggested_object_id="hall_door")
    hass.states.async_set("light.hall_old", "off")
    hass.states.async_set("binary_sensor.hall_door", "off")
    _, runtime = await _setup(hass)
    # Rename after Sentinel saw the old alias: the import still uses the old name (22.1).
    reg.async_update_entity(hall.entity_id, new_entity_id="light.hall")
    hass.states.async_remove("light.hall_old")  # HA drops the old state on rename
    hass.states.async_set("light.hall", "off")
    await hass.async_block_till_done()
    await runtime.async_flush()
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/import/create", content_utf8=YAML,
                      content_hash="sha256:" + hashlib.sha256(YAML.encode()).hexdigest(),
                      resource_kind="automation", label="hall.yaml",
                      request_key=str(uuid.uuid4()))
    assert res["success"], res
    await runtime.config.async_rebuild_graph()
    findings = list(runtime.config.ref_findings.values())
    assert [f.rule_id for f in findings] == ["DOC006"]
    f = findings[0]
    assert f.severity is Severity.WARNING
    assert [m["entity_id"] for m in f.details["missing"]] == ["light.gone"]
    assert f.details["stale"] == [{"entity_id": "light.hall_old", "current": "light.hall",
                                   "pointer": f.details["stale"][0]["pointer"]}]
    assert runtime.active_incidents == 1

    incidents = (await _call(client, type=f"{DOMAIN}/incidents/list"))["result"]["items"]
    detail = (await _call(client, type=f"{DOMAIN}/incidents/get",
                          incident_id=incidents[0]["id"]))["result"]
    assert detail["findings"][0]["details"]["missing_count"] == 1  # persisted and exposed
    assert detail["findings"][0]["subject"]["name"] == "hall.yaml"

    # Re-import with the fix: the finding is closed.
    fixed = YAML.replace("[light.hall_old, light.gone]", "[light.hall]")
    res = await _call(client, type=f"{DOMAIN}/import/create", content_utf8=fixed,
                      content_hash="sha256:" + hashlib.sha256(fixed.encode()).hexdigest(),
                      resource_kind="automation", label="hall.yaml",
                      resource_id=res["result"]["resource_id"], request_key=str(uuid.uuid4()))
    assert res["success"], res
    await runtime.config.async_rebuild_graph()
    assert runtime.config.ref_findings == {}
    assert runtime.active_incidents == 0


async def test_restored_findings_go_to_their_owner(hass):
    entry, runtime = await _setup(hass, modules_network=False)
    now = runtime.clock.now_us()

    def seed(conn):
        for rule, subject in (("DOC010", "sentinel:storage"), ("NET001", "endpoint:x")):
            repo.upsert_finding(conn, Finding(
                id=new_uuid(), rule_id=rule, rule_version=1, subject_uid=subject,
                status=FindingStatus.ACTIVE, severity=Severity.WARNING,
                first_seen_us=now, last_seen_us=now, details={"ratio": 0.9}), now)

    await runtime.db.run(seed)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.doctor.active_findings() == []  # not the Doctor's rules
    rows = await runtime.db.run(lambda c: [tuple(r) for r in c.execute(
        "SELECT rule_id, status, details_json FROM findings ORDER BY rule_id")])
    by_rule = {r[0]: r for r in rows}
    assert by_rule["NET001"][1] == "resolved"  # network module is off: no owner
    assert '"resolution":"owner_unavailable"' in by_rule["NET001"][2]
    assert "sentinel:storage" in runtime.data.findings or by_rule["DOC010"][1] == "resolved"
