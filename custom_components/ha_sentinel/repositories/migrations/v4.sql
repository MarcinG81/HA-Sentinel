-- v4: approved network probe targets and probe results (spec 13)

CREATE TABLE network_targets (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  host TEXT NOT NULL,
  port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
  kind TEXT NOT NULL CHECK (kind IN ('tcp', 'http', 'https')),
  path TEXT NOT NULL DEFAULT '/',
  allow_loopback INTEGER NOT NULL DEFAULT 0,
  linked_device_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  approved_by TEXT NOT NULL,
  approved_us INTEGER NOT NULL,
  UNIQUE(host, port, kind, path)
);
CREATE TABLE probe_results (
  target_id TEXT NOT NULL REFERENCES network_targets(id) ON DELETE CASCADE,
  at_us INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  latency_ms INTEGER,
  detail TEXT
);
CREATE INDEX probe_results_target_time ON probe_results(target_id, at_us);
