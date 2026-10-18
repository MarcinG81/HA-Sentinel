# Installation

## Requirements

- **Home Assistant 2026.5.1 or newer.** Tested on 2026.5.1 and 2026.10.2.
- [HACS](https://hacs.xyz/).
- An administrator account: the panel and every API call require admin rights.
- Works on HA OS, HA Container and HA Core. No add-on and no Supervisor required.

## Before you install

1. Create a Home Assistant backup and check that it finished (Settings → System → Backups).
2. Know how to reach your config directory if the UI is not available (Terminal & SSH or
   Samba add-on on HA OS, the host volume on HA Container).

## Install with HACS

1. HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/MarcinG81/HA-Sentinel` with type **Integration**.
3. Open **HA Sentinel** in HACS, click **Download** and pick the latest release.
4. **Restart Home Assistant.**
5. Settings → **Devices & services** → **+ Add integration** → **HA Sentinel**.
   It asks for no token and no settings.
6. Reload the browser page. A **Sentinel** item appears in the sidebar.

> Downloading files with HACS does not start the integration. The sidebar item appears
> only after step 5.

## The first minutes

- **Start-up grace (120 s).** Sentinel does not raise availability alarms right after a
  restart, so entities that are still starting up are not reported.
- **Coverage warm-up (15 min).** The health score needs 15 minutes of continuous
  observation. Until then it is shown greyed out as *partial*, with a countdown.
- **Devices that were already offline.** Anything unavailable when Sentinel started and still
  unavailable after the grace period becomes an incident. Its date is Sentinel's start time
  and means "at least since": Sentinel cannot know what happened before it was running. See
  [[Troubleshooting]] for devices that are normally off.

## Update

Update from HACS and restart Home Assistant. Sentinel upgrades its database automatically:

1. It reads the schema version without writing.
2. It checks free disk space (at least twice the database size, minimum 256 MiB). If there
   is not enough, Sentinel stays read-only and raises a Repairs issue.
3. It copies the database to `ha_sentinel/sentinel.vN.bak.db` first.
4. It migrates in a transaction and checks integrity.

If a migration fails, both files are kept, Sentinel runs without storing history, and
Repairs shows *HA Sentinel database upgrade failed*. Please report it.

## Downgrade

Downgrading the code does not downgrade the database. An older Sentinel opens a newer
database read-only. To go back:

1. Disable the integration (or stop Home Assistant).
2. Move the current `ha_sentinel/sentinel.db*` files aside.
3. Copy the matching `sentinel.vN.bak.db` to `sentinel.db`.
4. Install the previous release in HACS and restart.

Changes made after that backup are lost.

## Uninstall

1. In the integration options, turn off network probes and managed writes.
2. Optional: Sentinel panel → **Settings** → **Delete Sentinel data**.
3. Settings → Devices & services → HA Sentinel → ⋮ → **Delete**.
4. Remove HA Sentinel in HACS and restart Home Assistant.
5. `<config>/ha_sentinel/` stays until you delete it yourself. Managed automation files
   and their `!include` lines also stay; remove them if you no longer want those
   automations.

Removing Sentinel never touches the Recorder database or your Home Assistant backups.
