"""Database, ingestion and inventory: T03, T07, T17, queue overflow."""

from __future__ import annotations

import sqlite3

import pytest

from custom_components.ha_sentinel.const import SCHEMA_VERSION
from custom_components.ha_sentinel.core.clock import FakeClock
from custom_components.ha_sentinel.core.ingestion import IngestionQueues
from custom_components.ha_sentinel.core.inventory import Inventory
from custom_components.ha_sentinel.core.models import (
    Finding,
    FindingStatus,
    Observation,
    ObservationKind,
    Severity,
    dumps_strict,
    new_uuid,
)
from custom_components.ha_sentinel.repositories import repository as repo
from custom_components.ha_sentinel.repositories.db import (
    DatabaseWorker,
    DbMode,
    UnsupportedSchemaError,
    current_schema_version,
)

from .helpers import BOOT, S, avail


@pytest.fixture
def db(tmp_path):
    w = DatabaseWorker(tmp_path / "sentinel.db")
    w.open_sync(0)
    yield w
    w.close_sync()


def test_migration_v1_is_idempotent(tmp_path):
    path = tmp_path / "sentinel.db"
    w = DatabaseWorker(path)
    assert w.open_sync(0) is DbMode.READ_WRITE
    w.close_sync()
    w = DatabaseWorker(path)
    assert w.open_sync(1) is DbMode.READ_WRITE
    assert current_schema_version(w.conn) == SCHEMA_VERSION
    assert w.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION
    assert w.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    w.close_sync()


def test_t17_older_code_never_writes_newer_schema(tmp_path):
    path = tmp_path / "sentinel.db"
    w = DatabaseWorker(path)
    w.open_sync(0)
    w.conn.execute("INSERT INTO schema_migrations VALUES (?, 0, 'future')", (SCHEMA_VERSION + 1,))
    w.close_sync()

    w = DatabaseWorker(path)
    with pytest.raises(UnsupportedSchemaError):
        w.open_sync(0)
    assert w.mode is DbMode.READ_ONLY
    with pytest.raises(sqlite3.OperationalError):
        w.conn.execute("INSERT INTO checkpoints VALUES ('x', 'y', 1)")
    w.close_sync()


def test_status_check_rejects_values_outside_enum(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO jobs(id, kind, state, updated_at_us) VALUES ('j', 'k', 'bogus', 0)"
        )


def test_t03_transport_duplicates_are_stored_once(db):
    o = avail("e1", "unavailable", 10 * S)
    assert repo.insert_observations(db.conn, [o]) == 1
    assert repo.insert_observations(db.conn, [o]) == 0
    assert db.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_finding_persisted_without_incident_until_correlated(db):
    f = Finding(id=new_uuid(), rule_id="DOC001", rule_version=1, subject_uid="e1",
                status=FindingStatus.ACTIVE, severity=Severity.HIGH,
                first_seen_us=1, last_seen_us=1)
    repo.upsert_finding(db.conn, f, 2)
    repo.upsert_finding(db.conn, f, 3)  # repeated write is idempotent
    assert db.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    assert repo.count_active_incidents(db.conn) == 0


def test_t07_rename_keeps_uid_and_alias_history(db):
    inv = Inventory(BOOT)
    uid, ch = inv.observe(entity_id="sensor.hall_temp", registry_id="reg1", device_id="dev1",
                          now_us=1)
    repo.apply_inventory_changes(db.conn, ch)
    uid2, ch = inv.observe(entity_id="sensor.hall_temperature", registry_id="reg1",
                           device_id="dev1", now_us=2)
    repo.apply_inventory_changes(db.conn, ch)
    assert uid2 == uid
    assert inv.uid_for_alias("sensor.hall_temp") is None

    entities, devices = repo.load_inventory(db.conn)
    assert len(entities) == 1 and len(devices) == 1
    aliases = [(a.alias, a.valid_to_us) for a in entities[0].aliases]
    assert aliases == [("sensor.hall_temp", 2), ("sensor.hall_temperature", None)]

    repo.apply_inventory_changes(db.conn, [inv.remove(uid, 3)])
    entities, _ = repo.load_inventory(db.conn)
    assert entities[0].deleted_at_us == 3  # tombstone, history kept
    assert len(entities[0].aliases) == 2


def test_registry_less_entity_not_merged_across_boots(db):
    inv = Inventory(BOOT)
    uid, ch = inv.observe(entity_id="sensor.adhoc", registry_id=None, device_id=None, now_us=1)
    assert inv.observe(entity_id="sensor.adhoc", registry_id=None, device_id=None,
                       now_us=2)[0] == uid
    repo.apply_inventory_changes(db.conn, ch)

    inv2 = Inventory(new_uuid())
    inv2.load(*repo.load_inventory(db.conn))
    uid2, _ = inv2.observe(entity_id="sensor.adhoc", registry_id=None, device_id=None, now_us=3)
    assert uid2 != uid


def _obs(kind: ObservationKind, payload: dict, seq: int) -> Observation:
    return Observation(id=new_uuid(), ingest_seq=seq, boot_id=BOOT, source_id="ha.states",
                       kind=kind, received_at_us=seq, payload=payload, subject_uid=f"e{seq % 3}")


def test_control_overflow_creates_gap_and_resync():
    clock = FakeClock()
    q = IngestionQueues(clock, control_size=4, telemetry_size=4)
    for i in range(6):
        q.offer(_obs(ObservationKind.AVAILABILITY_CHANGED, {"to": "unavailable"}, i))
    assert q.stats.dropped_total == 2 and q.degraded
    clock.advance(5)
    r = q.drain()
    assert len(r.observations) == 4
    assert r.resync_required and len(r.gaps) == 1
    assert "dropped=2" in r.gaps[0].reason
    assert not q.degraded


def test_telemetry_overflow_keeps_last_sample_per_subject():
    q = IngestionQueues(FakeClock(), control_size=4, telemetry_size=2)
    for i in range(8):
        q.offer(_obs(ObservationKind.NUMERIC_SAMPLE, {"value": i}, i))
    assert q.telemetry_degraded
    r = q.drain()
    values = sorted(o.payload["value"] for o in r.observations)
    assert values[:2] == [0, 1]  # queued samples
    assert len(values) == 2 + 3  # plus last sample for each of 3 subjects
    assert not q.telemetry_degraded


def test_oversize_payload_rejected():
    q = IngestionQueues(FakeClock())
    big = _obs(ObservationKind.AVAILABILITY_CHANGED, {"x": "a" * 9000}, 1)
    assert q.offer(big) is False
    assert q.stats.rejected_oversize_total == 1
    assert q.offer(_obs(ObservationKind.AVAILABILITY_CHANGED, {"x": "a" * 100}, 2))


def test_nan_rejected():
    with pytest.raises(ValueError):
        dumps_strict({"v": float("nan")})


async def test_returning_a_live_cursor_is_rejected_and_does_not_poison_commits(tmp_path):
    w = DatabaseWorker(tmp_path / "c.db")
    await w.async_open(0)
    with pytest.raises(TypeError):
        await w.run(lambda c: c.execute("PRAGMA wal_checkpoint(PASSIVE)"))
    # The connection still commits normally afterwards.
    await w.run(lambda c: repo.save_checkpoint(c, "x", "b", 1))
    assert await w.run(lambda c: repo.load_checkpoint(c, "x")) == ("b", 1)
    await w.async_close()
