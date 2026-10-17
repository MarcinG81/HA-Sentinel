-- HA Sentinel schema v1 (spec 7.2)

CREATE TABLE observations (
  id TEXT PRIMARY KEY, ingest_seq INTEGER NOT NULL,
  boot_id TEXT NOT NULL, source_id TEXT NOT NULL,
  source_event_id TEXT, kind TEXT NOT NULL,
  subject_uid TEXT, received_at_us INTEGER NOT NULL,
  occurred_at_us INTEGER, payload_json TEXT NOT NULL,
  quality_json TEXT NOT NULL, schema_version INTEGER NOT NULL,
  UNIQUE(source_id, boot_id, source_event_id)
);
CREATE INDEX obs_subject_time ON observations(subject_uid, received_at_us);
CREATE INDEX obs_kind_time ON observations(kind, received_at_us);

CREATE TABLE entities (
  uid TEXT PRIMARY KEY,
  registry_id TEXT UNIQUE,
  current_alias TEXT NOT NULL,
  domain TEXT NOT NULL,
  first_seen_us INTEGER NOT NULL,
  last_seen_us INTEGER NOT NULL,
  deleted_at_us INTEGER
);
CREATE TABLE entity_aliases (
  entity_uid TEXT NOT NULL REFERENCES entities(uid),
  alias TEXT NOT NULL,
  boot_id TEXT,
  valid_from_us INTEGER NOT NULL,
  valid_to_us INTEGER
);
CREATE INDEX entity_alias_lookup ON entity_aliases(alias, valid_to_us);

CREATE TABLE devices (
  uid TEXT PRIMARY KEY,
  ha_device_id TEXT UNIQUE,
  metadata_json TEXT NOT NULL,
  deleted_at_us INTEGER
);
CREATE TABLE entity_devices (
  entity_uid TEXT NOT NULL REFERENCES entities(uid),
  device_uid TEXT NOT NULL REFERENCES devices(uid),
  PRIMARY KEY(entity_uid, device_uid)
);

CREATE TABLE findings (
  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
  rule_id TEXT NOT NULL, rule_version INTEGER NOT NULL,
  subject_uid TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'resolved')),
  severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'warning', 'info')),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  coverage_json TEXT NOT NULL,
  first_us INTEGER NOT NULL, last_us INTEGER NOT NULL,
  resolved_us INTEGER
);
CREATE UNIQUE INDEX finding_active_fp ON findings(fingerprint)
  WHERE status IN ('pending', 'active');
CREATE TABLE finding_evidence (
  finding_id TEXT NOT NULL REFERENCES findings(id),
  observation_id TEXT REFERENCES observations(id) ON DELETE SET NULL,
  digest TEXT NOT NULL, summary_json TEXT NOT NULL,
  PRIMARY KEY(finding_id, digest)
);

CREATE TABLE incidents (
  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'acknowledged', 'suppressed', 'resolved')),
  severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'warning', 'info')),
  opened_at_us INTEGER NOT NULL, updated_at_us INTEGER NOT NULL,
  resolved_at_us INTEGER, revision INTEGER NOT NULL,
  summary_json TEXT NOT NULL
);
CREATE UNIQUE INDEX incident_active_fp ON incidents(fingerprint)
  WHERE status IN ('open', 'acknowledged', 'suppressed');
CREATE TABLE incident_evidence (
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  observation_id TEXT REFERENCES observations(id) ON DELETE SET NULL,
  evidence_digest TEXT NOT NULL, summary_json TEXT NOT NULL,
  PRIMARY KEY(incident_id, evidence_digest)
);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY, actor_id TEXT, kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN
    ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
  updated_at_us INTEGER NOT NULL,
  request_key TEXT UNIQUE, request_hash TEXT,
  checkpoint_json TEXT, result_json TEXT
);

CREATE TABLE audit (
  id TEXT PRIMARY KEY, actor_id TEXT, request_id TEXT NOT NULL,
  operation TEXT NOT NULL, resource_id TEXT, plan_hash TEXT,
  result TEXT NOT NULL, created_us INTEGER NOT NULL
);

CREATE TABLE coverage (
  source_id TEXT NOT NULL, scope TEXT NOT NULL,
  from_us INTEGER NOT NULL, to_us INTEGER,
  status TEXT NOT NULL CHECK (status IN ('missing', 'partial', 'complete')),
  reason TEXT NOT NULL
);
CREATE INDEX coverage_source_time ON coverage(source_id, from_us);

CREATE TABLE checkpoints (
  consumer TEXT PRIMARY KEY, boot_id TEXT NOT NULL, ingest_seq INTEGER NOT NULL
);

CREATE TABLE notification_outbox (
  id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL REFERENCES incidents(id),
  revision INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'uncertain', 'failed')),
  attempt INTEGER NOT NULL DEFAULT 0,
  next_try_us INTEGER NOT NULL,
  UNIQUE(incident_id, revision)
);

-- Graph and config changes: tables created in v1, logic arrives in E3/E4.
CREATE TABLE graph_nodes (
  uid TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL,
  metadata_json TEXT NOT NULL, deleted_at_us INTEGER
);
CREATE TABLE graph_edges (
  id TEXT PRIMARY KEY,
  from_uid TEXT NOT NULL REFERENCES graph_nodes(uid),
  to_uid TEXT NOT NULL REFERENCES graph_nodes(uid),
  relation TEXT NOT NULL,
  provenance TEXT NOT NULL CHECK (provenance IN
    ('registry', 'static_parse', 'adapter', 'observed', 'manual')),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  evidence_ref TEXT,
  valid_from_us INTEGER NOT NULL, valid_to_us INTEGER,
  revision INTEGER NOT NULL
);
CREATE INDEX graph_edges_from ON graph_edges(from_uid, relation, valid_to_us);
CREATE INDEX graph_edges_to ON graph_edges(to_uid, relation, valid_to_us);
CREATE TABLE graph_revisions (
  revision INTEGER PRIMARY KEY, created_at_us INTEGER NOT NULL, diff_blob_ref TEXT
);

CREATE TABLE resources (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, locator_json TEXT NOT NULL,
  current_hash TEXT, write_policy TEXT NOT NULL
);
CREATE TABLE snapshots (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES resources(id),
  hash TEXT NOT NULL, blob_ref TEXT NOT NULL, created_us INTEGER NOT NULL,
  schema_version INTEGER NOT NULL, restorable INTEGER NOT NULL
);
CREATE TABLE change_plans (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES resources(id),
  base_hash TEXT NOT NULL, target_hash TEXT NOT NULL, plan_hash TEXT UNIQUE NOT NULL,
  state TEXT NOT NULL, actor_id TEXT NOT NULL, expires_us INTEGER NOT NULL,
  report_json TEXT NOT NULL
);
CREATE TABLE operations (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES change_plans(id),
  phase TEXT NOT NULL, before_hash TEXT NOT NULL, after_hash TEXT,
  last_error TEXT, updated_us INTEGER NOT NULL
);
