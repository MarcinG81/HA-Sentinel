# Data and Privacy

## Local only

No cloud, no account, no telemetry, no AI, no external analytics. Nothing leaves Home
Assistant unless you download an export yourself. Network probes, if you enable them, go only
to targets you approved, inside your allowed address ranges.

## Where data lives

`<config>/ha_sentinel/`:

| Path | Content |
|---|---|
| `sentinel.db` (+ `-wal`, `-shm`) | Sentinel's private SQLite database |
| `sentinel.vN.bak.db` | Consistent copy taken before upgrading from schema N |
| `blobs/` | Snapshot contents and graph history |
| `exports/` | Export archives, deleted after 24 h |

Sentinel never reads or writes the Recorder database, `.storage` files of other
integrations, `configuration.yaml` or the HA automation editor's files. With managed writes
enabled, it writes only the managed files you registered.

## What is collected

Only what the enabled rules need:

- **Availability** of entities (available / unavailable / unknown), not their full state
  history and never their attributes in bulk.
- **Numbers** only where a rule needs them: battery levels (device class *battery* with unit
  %, or your mapping) and the signal sources you declared.
- **Update entities:** whether an update is offered and its version.
- **Registry metadata:** entity IDs, devices, integrations, areas.
- **Imported YAML** you paste in (plain secrets refused or redacted, `!secret` kept as a
  reference and never resolved).

**Never observed:** `person`, `zone`, `device_tracker`, `media_player`, `camera`, `text`,
`input_text`. You can narrow observation further with *Observed domains*.

## Retention and budgets

| Data | Kept | Limit |
|---|---|---|
| Observations | 7 days (1–30) | Purged hourly in batches |
| Numeric aggregates | minute buckets 30 days, then hourly buckets 1 year | |
| Incidents and findings | 90 days after resolution (7–365); active ones never | |
| Snapshots | 30 days, at most 50 per resource; the newest is always kept | |
| Audit log | 180 days | 64 MiB; when full, new changes are refused until purge frees space |
| Exports and job results | 24 h | 20 MiB per export |
| Graph history | last 200 revisions | |
| Removed entities and devices (tombstones) and their settings | cleanup window, 30 days (7–365) | |
| Everything | | Storage budget (1024 MiB default): DOC010 warning at 80 %, restrictions at 95 % |

Sentinel never deletes Home Assistant files to free space.

## Audit

Every change you make through Sentinel is recorded: rule settings, weights, maintenance,
imports, restores, network targets and hosts, backup requests, identity decisions and
deletions. The record holds who, when, what and the result, never secrets or full payloads.

## Access

The panel and every API command require a Home Assistant **administrator**. Rights are
checked on the server for each request, and again right before any change is executed. Hiding
the panel would not be enough, so Sentinel does not rely on that.

## Deleting

Panel → Settings → **Delete Sentinel data** (see [[Settings|Panel Settings]]). Removing the
integration does not delete `<config>/ha_sentinel/` silently; you decide. Copies inside HA
backups follow the backup retention.
