# Troubleshooting

## The Sentinel item does not appear in the sidebar

1. Downloading with HACS is not enough. **Restart** Home Assistant, then add the integration
   in Settings → Devices & services → **+ Add integration** → *HA Sentinel*.
2. Reload the browser page (Ctrl+F5 / Cmd+Shift+R). In the mobile app close and reopen it.
3. The panel is shown to **administrators** only.
4. Still missing? Check Settings → Devices & services → HA Sentinel for an error, and
   Settings → System → Logs for entries with `ha_sentinel`.

## HACS: "This version requires Home Assistant …"

Sentinel needs Home Assistant **2026.5.1** or newer. Update Home Assistant first.

## HACS: "Downloading … failed"

Install a **release** (e.g. v0.1.0) rather than a commit: Sentinel is distributed as a
release ZIP. In HACS use ⋮ → *Update information*, then download again and pick the latest
release. If it still fails, the HACS entries in Settings → System → Logs tell why.

## Many incidents right after installation

On the first start, devices that were **already** unavailable become incidents once the grace
period ends. Their date is Sentinel's start time and means "at least since".

- **The device is really broken:** keep the incident; it closes by itself 30 s after the
  device is back.
- **The device is normally off** (dryer, washing machine, watch, sleeping sensor): Doctor →
  *Rules per object* → turn DOC001 off for its entities or set a long threshold, or use
  *Maintenance* while it is unplugged. See [[Rules per Object]].
- **You already know and just don't want the alarm now:** *Mute* the incident.

"Common failure: X" means several entities of the same device or integration *X* are
unavailable together. See [[Incidents and Probable Cause]].

## The health score is grey / unknown

Coverage is below 80 %: during the first 15 minutes after a start, after a monitoring gap,
or when Doctor is switched off. This is intended: missing data is never shown as healthy.

## A tab says "module disabled"

That module is switched off in the options (*Modules*). Autopilot is off by default.

## "Possibly the same entity"

You removed an entity and a new one with the same ID appeared. Decide whether it is the same
device; see [[Doctor|Panel Doctor]].

## Repairs issues

| Issue | What to do |
|---|---|
| Database is damaged | The file is kept untouched. Restore `ha_sentinel/` from an HA backup, or Panel → Settings → Delete Sentinel data → Everything. |
| Database is newer than this version | You downgraded Sentinel; see *Downgrade* in [[Installation]]. |
| Database upgrade failed | Both files are kept; Sentinel runs without storing history. Please report it with the log. |
| Not enough disk space to upgrade | Free at least twice the database size (min. 256 MiB) and restart. |

## Home Assistant does not start after installing or updating

1. Open the config directory (SSH, Samba or the host volume).
2. Rename `custom_components/ha_sentinel` to `custom_components/ha_sentinel.disabled`.
3. If you use a managed automation file and suspect it, comment out its `!include` line.
4. Restart Home Assistant. Sentinel's data in `ha_sentinel/` is kept.
5. Please open an issue with the log.

## Going back to an older Home Assistant

Home Assistant itself refuses to start on a configuration written by a newer HA. That is HA
behaviour, not Sentinel's; restore an HA backup made before the update.
