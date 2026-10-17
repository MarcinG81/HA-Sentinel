-- v12: rule_overrides also for DOC007 (expected runs) and DOC008 (weak signal on/off).
-- SQLite cannot alter a CHECK constraint: the table is rebuilt with its data.

CREATE TABLE rule_overrides_v12 (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL CHECK (rule_id IN ('DOC001', 'DOC002', 'DOC003', 'DOC004',
                                           'DOC005', 'DOC007', 'DOC008', 'DOC009')),
  subject_uid TEXT NOT NULL CHECK (subject_uid <> ''),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  params_json TEXT NOT NULL,
  policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
  actor_id TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  updated_us INTEGER NOT NULL,
  UNIQUE (rule_id, subject_uid)
);
INSERT INTO rule_overrides_v12 SELECT id, rule_id, subject_uid, enabled, params_json,
  policy_revision, actor_id, created_us, updated_us FROM rule_overrides;
DROP TABLE rule_overrides;
ALTER TABLE rule_overrides_v12 RENAME TO rule_overrides;
