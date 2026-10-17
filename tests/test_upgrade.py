"""Upgrade, downgrade and damaged storage (T16, T17, spec 19) on a real HA instance."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_sentinel.const import DEFAULT_OPTIONS, DOMAIN, SCHEMA_VERSION
from custom_components.ha_sentinel.repositories import db as dbmod


def _entry(hass) -> MockConfigEntry:
    e = MockConfigEntry(domain=DOMAIN, data={"instance_id": str(uuid.uuid4())},
                        options=dict(DEFAULT_OPTIONS))
    e.add_to_hass(hass)
    return e


def _db_path(hass) -> Path:
    return Path(hass.config.config_dir) / "ha_sentinel" / "sentinel.db"


def _make_old_db(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, autocommit=True)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                 "applied_at_us INTEGER NOT NULL, checksum TEXT NOT NULL)")
    for v in range(1, version + 1):
        for stmt in dbmod._split_statements(dbmod._migration_sql(v)):
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_migrations VALUES (?, 0, 'x')", (v,))
    conn.execute("INSERT INTO incidents(id, fingerprint, status, severity, opened_at_us, "
                 "updated_at_us, revision, summary_json) VALUES "
                 "('keep-me','fp','resolved','high',1,1,1,'{}')")
    conn.close()


async def test_upgrade_from_v1_migrates_and_keeps_history(hass):
    _make_old_db(_db_path(hass), 1)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.storage_writable and runtime.db.reason is None
    count = await runtime.db.run(lambda c: c.execute(
        "SELECT COUNT(*) FROM incidents WHERE id='keep-me'").fetchone()[0])
    assert count == 1
    assert (_db_path(hass).parent / "sentinel.v1.bak.db").exists()


async def test_t16_failed_migration_preserves_files_and_degrades(hass, monkeypatch):
    path = _db_path(hass)
    _make_old_db(path, SCHEMA_VERSION - 1)
    before = path.read_bytes()
    real = dbmod._migration_sql
    calls = {"n": 0}

    def fails_on_existing_data(version):
        # Data-dependent failure: the upgrade of the existing file fails once; the fresh
        # in-memory fallback DB migrates fine (a broken script would be caught by CI).
        if version == SCHEMA_VERSION and calls["n"] == 0:
            calls["n"] += 1
            return real(version) + "\nINSERT INTO incidents(id) VALUES ('violates not null');\n"
        return real(version)

    monkeypatch.setattr(dbmod, "_migration_sql", fails_on_existing_data)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)  # Core still runs
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.db.reason == "migration_failed"
    assert not runtime.storage_writable
    assert ir.async_get(hass).async_get_issue(DOMAIN, "migration_failed") is not None
    # The rolled-back file still has the old schema and data; the backup exists.
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == \
        SCHEMA_VERSION - 1
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    conn.close()
    assert (path.parent / f"sentinel.v{SCHEMA_VERSION - 1}.bak.db").exists()
    assert len(before) > 0


async def test_corrupted_database_is_kept_and_sentinel_degrades(hass):
    path = _db_path(hass)
    path.parent.mkdir(parents=True, exist_ok=True)
    garbage = b"this is not a sqlite database" * 100
    path.write_bytes(garbage)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.db.reason == "database_corrupted"
    assert runtime.state.value == "degraded"
    assert path.read_bytes() == garbage  # never silently replaced
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "database_corrupted")
    assert issue is not None and issue.translation_placeholders == {"file": "sentinel.db"}
    hass.states.async_set("sensor.x", "1")
    await hass.async_block_till_done()  # Doctor keeps running in memory


async def test_t17_newer_schema_read_only_with_repair(hass):
    path = _db_path(hass)
    _make_old_db(path, SCHEMA_VERSION)
    conn = sqlite3.connect(path, autocommit=True)
    conn.execute("INSERT INTO schema_migrations VALUES (?, 0, 'future')", (SCHEMA_VERSION + 1,))
    conn.close()
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN]["runtime"]
    assert runtime.db.reason == "unsupported_schema" and not runtime.storage_writable
    assert ir.async_get(hass).async_get_issue(DOMAIN, "unsupported_schema") is not None
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == \
        SCHEMA_VERSION + 1  # untouched


async def test_diagnostics_are_allowlisted(hass, hass_client):
    from custom_components.ha_sentinel.diagnostics import async_get_config_entry_diagnostics

    entry = _entry(hass)
    hass.states.async_set("sensor.secret_room_temp", "21", {"friendly_name": "Bedroom"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "secret_room" not in text and "Bedroom" not in text
    assert hass.config.config_dir not in text
    assert diag["schema_version"] == SCHEMA_VERSION
