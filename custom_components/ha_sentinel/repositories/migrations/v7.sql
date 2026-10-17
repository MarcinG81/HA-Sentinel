-- v7: per-object rule overrides (spec 27.1, 27.2); DOC005 overrides are heartbeat contracts

CREATE TABLE rule_overrides (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL CHECK (rule_id IN ('DOC001', 'DOC002', 'DOC003', 'DOC004',
                                           'DOC005', 'DOC009')),
  subject_uid TEXT NOT NULL CHECK (subject_uid <> ''),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  params_json TEXT NOT NULL,
  policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
  actor_id TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  updated_us INTEGER NOT NULL,
  UNIQUE (rule_id, subject_uid)
);
