"""Export builder redaction and retention purge (T19 domain part, REQ10)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from custom_components.ha_sentinel.core.models import new_uuid
from custom_components.ha_sentinel.modules.export.builder import (
    MAX_EXPORT_BYTES,
    ExportTooLargeError,
    build_export,
)
from custom_components.ha_sentinel.repositories import jobs
from custom_components.ha_sentinel.repositories.db import DatabaseWorker

CANARY = "CANARY-7f3a"
INCIDENT = {
    "id": "i1", "status": "open", "severity": "high", "opened_at_us": 1, "resolved_at_us": None,
    "revision": 1,
    "summary": {"title_key": "rule.DOC001", "subject_uid": f"sensor.{CANARY}",
                "finding_ids": ["f"], "hypotheses": [{
                    "candidate_uid": "device:x", "candidate_label": f"Hub {CANARY}",
                    "counter_evidence": [{"kind": "healthy_dependents", "count": 1,
                                          "sample": [f"Lamp {CANARY}"]}]}]},
}


def _build(redacted: bool, **kw) -> dict[str, bytes]:
    data = build_export(
        scopes=["diagnostics", "incidents", "config_history"], redacted=redacted,
        salt=b"s" * 32, created_at="2026-10-17T10:00:00Z", versions={"sentinel": "0.1.0"},
        diagnostics={"queues": {"depth": 0}}, incidents=[INCIDENT],
        resources=[{"id": "r1", "kind": "automation", "label": f"{CANARY}.yaml",
                    "snapshots": 1, "write_policy": "export_only"}],
        snapshots=[], yaml_files={"automation-r1.yaml": f"- alias: {CANARY}\n"}, **kw)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_t19_redacted_export_has_no_canary_and_stable_pseudonyms():
    files = _build(redacted=True)
    blob = b"".join(files.values())
    assert CANARY.encode() not in blob
    incidents = json.loads(files["incidents.json"])
    assert incidents[0]["subject"].startswith("obj-")
    assert "config_history/yaml/automation-r1.yaml" not in files
    manifest = json.loads(files["manifest.json"])
    assert manifest["redacted"] is True and "not anonymization" in manifest["pseudonymization"]
    for entry in manifest["files"]:
        assert len(entry["sha256"]) == 64 and not entry["name"].startswith("/")


def test_unredacted_export_includes_yaml():
    files = _build(redacted=False)
    assert files["config_history/yaml/automation-r1.yaml"].decode().startswith("- alias:")


def test_export_size_limit():
    with pytest.raises(ExportTooLargeError):
        build_export(scopes=["diagnostics"], redacted=True, salt=b"x" * 32, created_at="t",
                     versions={}, diagnostics={"blob": __import__("os").urandom(
                         MAX_EXPORT_BYTES).hex()},
                     incidents=None, resources=None, snapshots=None, yaml_files=None)


DAY = jobs.DAY_US


@pytest.fixture
def db(tmp_path):
    w = DatabaseWorker(tmp_path / "s.db")
    w.open_sync(0)
    yield w
    w.close_sync()


def test_purge_respects_retention_and_keeps_active(db):
    c = db.conn
    now = 100 * DAY
    for i in range(3):
        c.execute("INSERT INTO observations(id, ingest_seq, boot_id, source_id, kind, "
                  "received_at_us, payload_json, quality_json, schema_version) "
                  "VALUES (?,?,?,?,?,?,?,?,1)",
                  (new_uuid(), i, "b", "ha.states", "availability.changed",
                   now - (10 if i < 2 else 1) * DAY, "{}", "{}"))
    for iid, status, resolved in (("old", "resolved", now - 120 * DAY),
                                  ("recent", "resolved", now - 5 * DAY),
                                  ("active", "open", None)):
        c.execute("INSERT INTO incidents(id, fingerprint, status, severity, opened_at_us, "
                  "updated_at_us, resolved_at_us, revision, summary_json) "
                  "VALUES (?,?,?,?,?,?,?,1,'{}')",
                  (iid, iid, status, "high", 0, 0, resolved))
    counts = jobs.purge_batch(c, now, observations_days=7, incidents_days=90)
    assert counts["observations"] == 2 and counts["incidents"] == 1
    left = {r[0] for r in c.execute("SELECT id FROM incidents")}
    assert left == {"recent", "active"}
    assert not any(jobs.purge_batch(c, now, observations_days=7, incidents_days=90).values())


def test_job_states_never_leave_terminal(db):
    job_id, created = jobs.create_job(db.conn, kind="export", request_key=new_uuid(),
                                      request_hash="h", actor_id="u", now_us=1)
    assert created
    jobs.update_job(db.conn, job_id, state=jobs.JobState.CANCELLED, now_us=2)
    jobs.update_job(db.conn, job_id, state=jobs.JobState.RUNNING, now_us=3)
    assert jobs.get_job(db.conn, job_id)["state"] == "cancelled"


def test_heavy_job_limit(db):
    for _ in range(2):
        jobs.create_job(db.conn, kind="export", request_key=new_uuid(), request_hash="h",
                        actor_id="u", now_us=1)
    from custom_components.ha_sentinel.core.errors import ErrorCode, SentinelError

    with pytest.raises(SentinelError) as err:
        jobs.create_job(db.conn, kind="export", request_key=new_uuid(), request_hash="h",
                        actor_id="u", now_us=1)
    assert err.value.code is ErrorCode.RATE_LIMITED
