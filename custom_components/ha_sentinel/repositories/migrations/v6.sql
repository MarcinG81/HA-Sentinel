-- v6: maintenance windows (spec 8.2)

CREATE TABLE maintenance_windows (
  id TEXT PRIMARY KEY,
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('entity', 'device', 'integration')),
  scope_id TEXT NOT NULL,
  label TEXT NOT NULL,
  start_us INTEGER NOT NULL,
  end_us INTEGER NOT NULL,
  reason TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  created_us INTEGER NOT NULL,
  cancelled_us INTEGER,
  CHECK (end_us > start_us)
);
CREATE INDEX maintenance_active ON maintenance_windows(end_us, cancelled_us);
