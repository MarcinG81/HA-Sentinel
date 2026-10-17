-- v13: identity suggestions (spec 6.1): a deleted and re-added object is not merged
-- automatically; the admin confirms or rejects the suggested continuity.

CREATE TABLE identity_suggestions (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('entity')),
  old_uid TEXT NOT NULL,
  new_uid TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  decision TEXT CHECK (decision IN ('confirmed', 'rejected')),
  decided_by TEXT,
  decided_us INTEGER,
  UNIQUE (old_uid, new_uid)
);
CREATE INDEX identity_suggestions_open ON identity_suggestions(decision);
