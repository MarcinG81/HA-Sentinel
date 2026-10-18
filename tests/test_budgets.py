"""Per-class data budgets (spec 7.3): aggregates, snapshot cap, audit budget."""

from __future__ import annotations

import json
import uuid

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel import data_services
from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.ha_sentinel.repositories import history

DAY = 86_400_000_000


async def _call(client, **msg):
    await client.send_json_auto_id(msg)
    while True:
        reply = await client.receive_json()
        if reply.get("type") == "result":
            return reply


async def _setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                            options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["runtime"]


async def test_samples_become_aggregates_and_snapshots_are_capped(hass, monkeypatch):
    runtime = await _setup(hass)
    monkeypatch.setattr(data_services, "BLOB_GRACE_S", -60)  # treat every blob as old
    now = runtime.clock.now_us()
    # Beyond the 7-day observation retention, aligned to a minute so all three samples
    # share one minute bucket whatever the wall clock is.
    old = (now - 10 * DAY) // 60_000_000 * 60_000_000

    def seed(conn):
        for i, value in enumerate((40.0, 30.0, 20.0)):
            conn.execute(
                "INSERT INTO observations(id, ingest_seq, boot_id, source_id, source_event_id, "
                "kind, subject_uid, received_at_us, occurred_at_us, payload_json, quality_json, "
                "schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                (str(uuid.uuid4()), i, "b", "ha.states", f"x{i}", "numeric.sample", "bat-1",
                 old + i * 1_000_000, old, json.dumps({"value": value, "unit": "%"}), "{}"))
        conn.execute("INSERT INTO numeric_aggregates VALUES ('sig-1','signal','minute',?,2,"
                     "-90,-80,-170)", (now - 40 * DAY,))
        for i in range(55):
            history.save_snapshot_tx(
                conn, runtime.config.blobs, resource_id=history.find_resource(
                    conn, "import", "automation") if i else None, provider="import",
                kind="automation", label="a.yaml", canonical_tree=[{"i": i}],
                canonical_hash=f"sha256:{i:064x}", raw=None, raw_hash=None, redacted_paths=[],
                validation={}, actor_id=None, request_id="t", now_us=now + i)

    await runtime.db.run(seed)
    totals = await runtime.data.async_purge()
    # The cap is enforced on save already; purge keeps it as a second line of defence,
    # and the blobs of the dropped snapshots are removed.
    assert totals["observations"] == 3 and totals["blobs"] >= 5 and "snapshots_capped" in totals
    rows = await runtime.db.run(lambda c: [tuple(r) for r in c.execute(
        "SELECT subject_uid, kind, granularity, n, vmin, vmax, vsum FROM numeric_aggregates "
        "ORDER BY subject_uid")])
    assert rows == [("bat-1", "battery", "minute", 3, 20.0, 40.0, 90.0),
                    ("sig-1", "signal", "hour", 2, -90.0, -80.0, -170.0)]
    left = await runtime.db.run(lambda c: c.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM snapshots GROUP BY resource_id)")
                                .fetchone()[0])
    assert left == 50


async def test_audit_over_budget_blocks_mutations_but_not_deletion(hass, hass_ws_client,
                                                                   monkeypatch):
    runtime = await _setup(hass)
    monkeypatch.setattr(data_services, "AUDIT_BUDGET_BYTES", 10)
    assert await runtime.data.check_audit_budget() is True
    client = await hass_ws_client(hass)
    res = await _call(client, type=f"{DOMAIN}/maintenance/create", scope_uid="x", minutes=5,
                      reason="r", request_key=str(uuid.uuid4()))
    assert res["error"]["code"] == "storage_unavailable"
    assert res["error"]["details"]["reason"] == "audit_budget"
    status = (await _call(client, type=f"{DOMAIN}/data/status"))["result"]
    assert status["audit_blocked"] is True and "audit_bytes" in status["storage"]
    res = await _call(client, type=f"{DOMAIN}/data/delete", scope="observations",
                      confirm="observations", request_key=str(uuid.uuid4()))
    assert res.get("success") or res["error"]["details"].get("reason") != "audit_budget"


async def test_young_orphan_blob_survives_the_purge(hass):
    """A blob written after the reference list was read must not be deleted (race)."""
    runtime = await _setup(hass)
    ref = await hass.async_add_executor_job(runtime.config.blobs.put, b"written just now")
    await runtime.data.async_purge()
    assert await hass.async_add_executor_job(runtime.config.blobs.get, ref) == b"written just now"
