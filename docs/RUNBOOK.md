# HA Sentinel — operations runbook

Audience: Home Assistant administrators running HA Sentinel. Every procedure here works
without the Sentinel panel; most work without the HA frontend at all.

Where Sentinel keeps its data: `<config>/ha_sentinel/`

| Path | Content |
|---|---|
| `sentinel.db`, `sentinel.db-wal`, `sentinel.db-shm` | Sentinel's private SQLite database (WAL mode) |
| `sentinel.vN.bak.db` | Consistent copy taken before upgrading from schema N |
| `blobs/` | Snapshot contents (content addressed) |
| `exports/` | Export archives, deleted after 24 h |

Sentinel never writes to the Recorder database, `.storage` files of other integrations,
`configuration.yaml` or files of the HA automation editor. Files under the managed files
directory (default `ha_sentinel_managed/`) are written only if you enabled managed writes.

## 1. Before installing or upgrading

1. Create a Home Assistant backup and check that it finished (Settings → System → Backups).
2. Know how you reach the config directory if the UI is down: Terminal & SSH or Samba
   add-on on HA OS, the host volume on HA Container.

## 2. Install

1. HACS → Integrations → ⋮ → Custom repositories → add this repository as *Integration*.
2. Install the release, restart Home Assistant.
3. Settings → Devices & services → Add integration → *HA Sentinel*.
4. The **Sentinel** panel appears in the sidebar for administrators.

Defaults: local only, no AI, no network probes (level N0), no writes to any HA file.

## 3. Optional features (integration options)

| Option | Default | Effect |
|---|---|---|
| Allow Sentinel to write managed files | off | Enables restore for files you register as managed |
| Managed files directory | `ha_sentinel_managed` | Only files inside it can be managed |
| Reload automations after restore | on | Runs `automation.reload` and verifies the restored ids |
| Active network probes | off | Level N1: probes targets you approve, nothing else |
| Allowed address ranges | RFC 1918 + IPv6 ULA | Probes outside these ranges are refused |

### Managed automation file

1. Create `<config>/ha_sentinel_managed/automations.yaml` (mode `0644`, owned by the HA user).
2. Include it yourself, e.g. in `configuration.yaml`:
   `automation sentinel: !include ha_sentinel_managed/automations.yaml`
3. Check the configuration and restart HA.
4. Panel → Config history → *Managed file* → register it and confirm exclusive ownership.
5. From now on edit this file only through Sentinel. Edits made elsewhere are detected
   (hash check) and never overwritten; use *Sync with file* to record them.

A restore never undoes physical actions devices have already performed.

## 4. Upgrade

Sentinel upgrades its database automatically on start:

1. Reads the schema version without writing.
2. Checks free space (2 × DB size, at least 256 MiB); otherwise stays read-only and raises
   a Repairs issue *Not enough disk space…*.
3. Copies the database with the SQLite backup API to `sentinel.vN.bak.db`.
4. Migrates in a transaction, checks foreign keys and integrity, records a checksum.

If a migration fails it is rolled back, the file and backup are untouched, Sentinel runs
without storing history and Repairs shows *HA Sentinel database upgrade failed*. Keep both
files and report the issue.

## 5. Downgrade (rollback of a Sentinel release)

Downgrading the code does not downgrade the database. An older Sentinel opens a newer
database read-only and shows *HA Sentinel database is newer than this version*.

1. Settings → Devices & services → HA Sentinel → ⋮ → Disable (or stop Home Assistant).
2. Move the current `sentinel.db*` files aside (keep them).
3. Copy the backup for the target schema, e.g. `sentinel.v3.bak.db`, to `sentinel.db`.
4. Install the previous release in HACS and restart Home Assistant.
5. Enable the integration and check *Capabilities* and *Coverage* in the panel.

Changes made after the backup (incidents, snapshots) are lost; name them before you start.

Four different operations, none of which undoes physical actions:
restore of a managed file · rollback of the Sentinel schema · downgrade of the HACS
package · restore of a full Home Assistant backup.

## 6. Damaged database

Repairs: *HA Sentinel database is damaged*. The file is kept untouched; Sentinel keeps
detecting failures in memory but stores no history. Either restore `ha_sentinel/` from a
Home Assistant backup, or Panel → Settings → *Delete Sentinel data* → *Everything*.

## 7. Home Assistant does not start or the frontend is broken after an upgrade

1. Open the config directory (SSH/Samba/host volume).
2. Rename `custom_components/ha_sentinel` to `custom_components/ha_sentinel.disabled`.
3. If you use a managed automation file and suspect it, comment out its `!include` line.
4. Restart Home Assistant. Sentinel's data in `ha_sentinel/` is kept.
5. To return: rename the folder back, or reinstall a known-good release, and restart.

## 8. Load and storage pressure

- Panel → Diagnostics shows queue depth, dropped observations and coverage gaps.
- Rising queues: telemetry (battery samples) is coalesced to the last value per entity
  automatically; availability rules keep working while the control queue is healthy.
  Every drop is recorded as a coverage gap and a resync of current states follows.
- Storage: retention purge runs hourly (observations 7 days, incidents 90 days,
  snapshots 30 days / 50 per resource, audit 180 days). Sentinel never deletes HA files to
  free space.
- Sentinel cannot tell you that Home Assistant itself is down; use an external monitor.

## 9. Delete data and uninstall

1. Turn off network probes and managed writes in the options.
2. Optional: Panel → Settings → *Delete Sentinel data* (choose the scope; *Everything*
   restarts Sentinel with an empty database).
3. Remove the integration in Settings → Devices & services.
4. Remove it in HACS and restart Home Assistant.
5. `<config>/ha_sentinel/` stays until you delete it. Managed files and their `!include`
   lines stay too — remove them yourself if you no longer want those automations.
6. Copies of Sentinel data inside existing HA backups follow the backup retention.
