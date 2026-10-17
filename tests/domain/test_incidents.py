"""Incident lifecycle: ack, suppress, expiry, idempotency, audit, pagination, migration v2."""

from __future__ import annotations

import sqlite3

import pytest

from custom_components.ha_sentinel.const import SCHEMA_VERSION
from custom_components.ha_sentinel.core.clock import FakeClock
from custom_components.ha_sentinel.core.errors import ErrorCode, SentinelError
from custom_components.ha_sentinel.core.models import Finding, FindingStatus, Severity, new_uuid
from custom_components.ha_sentinel.core.ratelimit import RateLimiter, RequestClass
from custom_components.ha_sentinel.repositories import repository as repo
from custom_components.ha_sentinel.repositories.db import (
    DatabaseWorker,
    _migration_sql,
    _split_statements,
    current_schema_version,
)

from .helpers import S


@pytest.fixture
def db(tmp_path):
    w = DatabaseWorker(tmp_path / "sentinel.db")
    w.open_sync(0)
    yield w
    w.close_sync()


def _open_incident(conn, subject="e1", at=1) -> str:
    f = Finding(id=new_uuid(), rule_id="DOC001", rule_version=1, subject_uid=subject,
                status=FindingStatus.ACTIVE, severity=Severity.HIGH,
                first_seen_us=at, last_seen_us=at)
    repo.upsert_finding(conn, f, at)
    return conn.execute("SELECT id FROM incidents WHERE fingerprint=?",
                        (f.fingerprint,)).fetchone()[0]


def _ack(conn, iid, rev, key=None, now=10):
    return repo.run_idempotent(
        conn, request_key=key or new_uuid(), request_hash=f"h{iid}{rev}", actor_id="u1",
        kind="incident.ack", now_us=now,
        op=lambda c: repo.acknowledge_incident(c, iid, rev, "u1", "r1", now),
    )


def _suppress(conn, iid, rev, until, now=10):
    return repo.run_idempotent(
        conn, request_key=new_uuid(), request_hash="s", actor_id="u1",
        kind="incident.suppress", now_us=now,
        op=lambda c: repo.suppress_incident(c, iid, rev, until, "maintenance", "u1", "r", now),
    )


def test_ack_bumps_revision_and_writes_audit(db):
    iid = _open_incident(db.conn)
    assert _ack(db.conn, iid, 1) == {"revision": 2, "status": "acknowledged"}
    row = db.conn.execute("SELECT status, revision, acknowledged_by FROM incidents").fetchone()
    assert tuple(row) == ("acknowledged", 2, "u1")
    audit = db.conn.execute("SELECT operation, resource_id, result FROM audit").fetchall()
    assert [tuple(a) for a in audit] == [("incident.ack", iid, "ok")]


def test_stale_revision_is_conflict_and_changes_nothing(db):
    iid = _open_incident(db.conn)
    _ack(db.conn, iid, 1)
    with pytest.raises(SentinelError) as err:
        _suppress(db.conn, iid, 1, 1000)
    assert err.value.code is ErrorCode.CONFLICT
    assert db.conn.execute("SELECT status FROM incidents").fetchone()[0] == "acknowledged"
    assert db.conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='incident.suppress'"
                           ).fetchone()[0] == 0


def test_unknown_incident_is_not_found(db):
    with pytest.raises(SentinelError) as err:
        _ack(db.conn, new_uuid(), 1)
    assert err.value.code is ErrorCode.NOT_FOUND


def test_same_request_key_returns_previous_result(db):
    iid = _open_incident(db.conn)
    key = new_uuid()
    first = _ack(db.conn, iid, 1, key=key)
    again = _ack(db.conn, iid, 1, key=key)  # replay: not a second operation, no conflict
    assert again == first
    assert db.conn.execute("SELECT revision FROM incidents").fetchone()[0] == 2
    assert db.conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1


def test_same_request_key_with_other_content_is_conflict(db):
    iid = _open_incident(db.conn)
    key = new_uuid()
    _ack(db.conn, iid, 1, key=key)
    with pytest.raises(SentinelError) as err:
        repo.run_idempotent(db.conn, request_key=key, request_hash="different", actor_id="u1",
                            kind="incident.ack", now_us=11, op=lambda c: {})
    assert err.value.code is ErrorCode.CONFLICT


def test_suppress_expires_back_to_previous_status(db):
    iid = _open_incident(db.conn)
    _ack(db.conn, iid, 1)
    _suppress(db.conn, iid, 2, until=100 * S)
    row = db.conn.execute("SELECT status, previous_status FROM incidents").fetchone()
    assert tuple(row) == ("suppressed", "acknowledged")
    assert repo.expire_suppressions(db.conn, 99 * S) == 0
    assert repo.expire_suppressions(db.conn, 100 * S) == 1
    row = db.conn.execute("SELECT status, previous_status, revision FROM incidents").fetchone()
    assert tuple(row) == ("acknowledged", None, 4)


def test_suppress_in_the_past_rejected(db):
    iid = _open_incident(db.conn)
    with pytest.raises(SentinelError) as err:
        _suppress(db.conn, iid, 1, until=5, now=10)
    assert err.value.code is ErrorCode.INVALID_REQUEST


def test_recovery_resolves_suppressed_incident(db):
    iid = _open_incident(db.conn)
    _suppress(db.conn, iid, 1, until=100 * S)
    f = Finding(id=new_uuid(), rule_id="DOC001", rule_version=1, subject_uid="e1",
                status=FindingStatus.RESOLVED, severity=Severity.HIGH, first_seen_us=1,
                last_seen_us=50, resolved_at_us=50)
    repo.upsert_finding(db.conn, f, 50)
    assert db.conn.execute("SELECT status FROM incidents WHERE id=?", (iid,)).fetchone()[0] \
        == "resolved"


def test_keyset_pagination_is_stable(db):
    ids = [_open_incident(db.conn, subject=f"e{i}", at=i % 3) for i in range(7)]
    seen: list[str] = []
    after = None
    while True:
        page = repo.list_incidents(db.conn, 3, True, after)
        if not page:
            break
        seen += [r["id"] for r in page]
        after = (page[-1]["updated_at_us"], page[-1]["id"])
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))


def test_get_incident_returns_allowlisted_evidence(db):
    iid = _open_incident(db.conn)
    inc = repo.get_incident(db.conn, iid)
    assert inc["findings"][0]["rule_id"] == "DOC001"
    assert repo.get_incident(db.conn, new_uuid()) is None


def test_migration_v1_to_latest_keeps_data_and_backs_up(tmp_path):
    path = tmp_path / "sentinel.db"
    conn = sqlite3.connect(path, autocommit=True)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                 "applied_at_us INTEGER NOT NULL, checksum TEXT NOT NULL)")
    for stmt in _split_statements(_migration_sql(1)):
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_migrations VALUES (1, 0, 'x')")
    conn.execute("INSERT INTO incidents(id, fingerprint, status, severity, opened_at_us, "
                 "updated_at_us, revision, summary_json) VALUES "
                 "('i1','fp','open','high',1,1,1,'{}')")
    conn.close()

    w = DatabaseWorker(path)
    w.open_sync(5)
    assert current_schema_version(w.conn) == SCHEMA_VERSION
    row = w.conn.execute("SELECT status, previous_status FROM incidents").fetchone()
    assert tuple(row) == ("open", None)
    w.close_sync()
    assert (tmp_path / "sentinel.v1.bak.db").exists()


def test_rate_limiter_window():
    clock = FakeClock()
    rl = RateLimiter(clock)
    assert all(rl.allow("u", RequestClass.MUTATION) for _ in range(10))
    assert not rl.allow("u", RequestClass.MUTATION)
    assert rl.allow("other", RequestClass.MUTATION)  # per user
    assert rl.allow("u", RequestClass.QUERY)  # per class
    clock.advance(60)
    assert rl.allow("u", RequestClass.MUTATION)
