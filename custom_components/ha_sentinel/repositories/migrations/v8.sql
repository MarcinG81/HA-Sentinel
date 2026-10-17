-- v8: explicit numeric sources (spec 5.1, 8.1): manual battery mapping and signal scales

CREATE TABLE numeric_sources (
  id TEXT PRIMARY KEY,
  subject_uid TEXT NOT NULL CHECK (subject_uid <> ''),
  purpose TEXT NOT NULL CHECK (purpose IN ('battery', 'signal')),
  scale_min REAL NOT NULL,
  scale_max REAL NOT NULL CHECK (scale_max > scale_min),
  threshold REAL,
  actor_id TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  updated_us INTEGER NOT NULL,
  UNIQUE (subject_uid, purpose),
  CHECK (purpose = 'battery' OR threshold IS NOT NULL)
);
