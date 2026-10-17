-- v10: health score unit weights 1..5 set by the admin (spec 8.3); absent row = weight 1

CREATE TABLE unit_weights (
  unit_uid TEXT PRIMARY KEY CHECK (unit_uid <> ''),
  weight INTEGER NOT NULL CHECK (weight BETWEEN 2 AND 5),
  actor_id TEXT NOT NULL,
  updated_us INTEGER NOT NULL
);
