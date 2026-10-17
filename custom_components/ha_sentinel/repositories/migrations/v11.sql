-- v11: finding details (safe, allowlisted fields such as the measured value, the threshold,
-- missing references or the reason a finding was closed); previously kept in memory only

ALTER TABLE findings ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';
