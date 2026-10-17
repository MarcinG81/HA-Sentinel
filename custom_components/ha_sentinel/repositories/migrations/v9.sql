-- v9: network hosts for level N0 (spec 13.1, 13.2)

CREATE TABLE network_hosts (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
  source TEXT NOT NULL CHECK (source IN ('manual')),
  created_by TEXT NOT NULL,
  first_seen_us INTEGER NOT NULL,
  last_seen_us INTEGER NOT NULL,
  updated_us INTEGER NOT NULL
);
-- Identity keys are unique across hosts: a clash stops automatic merging.
CREATE TABLE network_host_keys (
  host_id TEXT NOT NULL REFERENCES network_hosts(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('mac', 'hostname')),
  value TEXT NOT NULL,
  PRIMARY KEY (kind, value)
);
-- Addresses are attributes, never identity (DHCP may change them).
CREATE TABLE network_host_addresses (
  host_id TEXT NOT NULL REFERENCES network_hosts(id) ON DELETE CASCADE,
  family TEXT NOT NULL CHECK (family IN ('ipv4', 'ipv6')),
  value TEXT NOT NULL,
  source TEXT NOT NULL,
  updated_us INTEGER NOT NULL,
  PRIMARY KEY (host_id, value)
);
-- Host <-> HA device links exist only after an admin confirms them.
CREATE TABLE network_host_links (
  host_id TEXT NOT NULL REFERENCES network_hosts(id) ON DELETE CASCADE,
  device_id TEXT NOT NULL,
  evidence TEXT NOT NULL,
  confirmed_by TEXT NOT NULL,
  confirmed_us INTEGER NOT NULL,
  PRIMARY KEY (host_id, device_id)
);
ALTER TABLE network_targets ADD COLUMN host_id TEXT REFERENCES network_hosts(id)
  ON DELETE SET NULL;
