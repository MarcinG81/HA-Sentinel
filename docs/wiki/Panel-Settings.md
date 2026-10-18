# Settings

## Integration options

A link to Settings → Devices & services → HA Sentinel → **Configure**. All options are
described in [[Configuration]].

## Home Assistant backup

Sentinel's snapshots are **not** a backup of your home. Restoring the whole system is done
with Home Assistant Backup.

This card shows the state of HA's backup entities (backup manager, last successful and last
attempted automatic backup, next scheduled one) and links to the backup settings.

**Create backup now** asks Home Assistant to start a backup with **your automatic backup
settings** (locations, encryption), after a confirmation. Home Assistant checks your admin
rights again, and the request is written to the audit log before and after.

- *Accepted* means the backup **started**, not that it finished. Check the status above or
  HA's backup page.
- If Home Assistant refuses (for example, automatic backups have no location set), the card
  tells you so.

## Cleanup in Home Assistant

Entities and devices that have been inactive for longer than the **cleanup window**
(30 days by default, set in the options):

| Reason | Meaning |
|---|---|
| unavailable / unknown state | The state has not changed for the whole window |
| no longer provided by its integration | HA shows it as *restored*: the integration stopped providing it |
| registered but has no state | An entry in the registry that nothing creates any more |
| its integration is not loaded / was removed | The integration failed to start or no longer exists |
| all its entities are inactive | A device whose every entity is in the list |

Each row has **Open in Home Assistant**, where you decide whether to remove it.
**This is only a list: Sentinel never removes anything from Home Assistant.** Disabled
entities and entities that are `unknown` by nature (buttons, scenes, events…) are not listed.

Sentinel's own data is cleaned automatically: objects removed from Home Assistant more than
the window ago, together with the rule settings that stayed attached to them, are dropped by
the hourly purge.

## Data

- Sizes: database, observations, numeric aggregates, snapshots, audit log (with its budget).
- The retention in use and the time of the last purge (runs every hour).
- If the audit log reaches its budget, a banner explains that Sentinel refuses new changes
  until the purge frees space. Deleting data still works.

Details of every data class: [[Data and Privacy]].

## Export

Create an archive (ZIP) to attach to a bug report or keep for yourself:

- **Scopes:** *Diagnostics*, *Incidents*, *Config history*.
- **Redacted** (default): entity IDs, names and labels become pseudonyms (stable only inside
  that one export). IP addresses, MAC addresses, hostnames, area names and absolute paths are
  never included. This is pseudonymisation, not anonymisation, so glance at the file before
  you share it.
- Before creating it you see a preview of what each scope contains. The archive includes a
  `manifest.json` describing what is inside.
- Download works only for the administrator who created it; it expires after **24 h**.
  Limit: 20 MiB.

## Delete Sentinel data

Choose a scope and type its name to confirm:

| Scope | Deletes |
|---|---|
| Observations | Raw observations, coverage gaps, checkpoints |
| Incidents | Incidents, findings, their evidence, notification queue |
| Config history | Imported resources, snapshots, change plans and graph history |
| Everything | All of the above; Sentinel restarts with an empty database |

It never deletes Recorder data, your Home Assistant backups or any HA file. Copies of
Sentinel data inside existing HA backups follow the backup retention.
