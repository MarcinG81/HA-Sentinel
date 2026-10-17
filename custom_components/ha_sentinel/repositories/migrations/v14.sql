-- v14: numeric aggregates (spec 7.3): raw samples become minute buckets when they leave
-- the observation retention, minute buckets become hourly ones after 30 days.

CREATE TABLE numeric_aggregates (
  subject_uid TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('battery', 'signal')),
  granularity TEXT NOT NULL CHECK (granularity IN ('minute', 'hour')),
  bucket_us INTEGER NOT NULL,
  n INTEGER NOT NULL CHECK (n > 0),
  vmin REAL NOT NULL,
  vmax REAL NOT NULL,
  vsum REAL NOT NULL,
  PRIMARY KEY (subject_uid, kind, granularity, bucket_us)
);
