-- v2: incident acknowledge and suppress state (spec 9.1, 15.1)

ALTER TABLE incidents ADD COLUMN previous_status TEXT;
ALTER TABLE incidents ADD COLUMN suppressed_until_us INTEGER;
ALTER TABLE incidents ADD COLUMN suppress_reason TEXT;
ALTER TABLE incidents ADD COLUMN acknowledged_by TEXT;
ALTER TABLE incidents ADD COLUMN acknowledged_at_us INTEGER;
CREATE INDEX incidents_suppressed_until ON incidents(suppressed_until_us)
  WHERE status = 'suppressed';
CREATE INDEX incidents_updated ON incidents(updated_at_us, id);
