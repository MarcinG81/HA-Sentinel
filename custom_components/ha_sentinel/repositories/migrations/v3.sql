-- v3: configuration history metadata (spec 10.1-10.2)

ALTER TABLE resources ADD COLUMN kind TEXT;
ALTER TABLE resources ADD COLUMN label TEXT;
ALTER TABLE resources ADD COLUMN created_us INTEGER;
ALTER TABLE snapshots ADD COLUMN raw_hash TEXT;
ALTER TABLE snapshots ADD COLUMN raw_blob_ref TEXT;
ALTER TABLE snapshots ADD COLUMN redacted_paths_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE snapshots ADD COLUMN validation_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE snapshots ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE snapshots ADD COLUMN created_by TEXT;
CREATE INDEX snapshots_resource_time ON snapshots(resource_id, created_us DESC, id);
