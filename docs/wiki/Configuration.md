# Configuration

Settings → Devices & services → **HA Sentinel** → **Configure**. The form has six sections.
Saving reloads the integration; open incidents are kept and not announced again. Every
change is recorded in [[Config history|Panel Config History]] as a version of Sentinel's own
configuration.

Settings for a single entity (a threshold, turning a rule off, heartbeat, expected runs) are
not here but in the panel: see [[Rules per Object]].

## Modules

| Option | Default | Effect |
|---|---|---|
| Doctor | on | Health rules and the health score. When off, open Doctor findings are closed and the score shows *unknown*, never "healthy". Core observation keeps running. |
| Configuration history | on | Imports, versions, diffs, managed-file restore |
| Autopilot | **off** | Lint, draft wizard and replay. Turning it on never enables AI (there is none). |
| Dependency map | on | The graph. Without it, incidents are not grouped by shared parents. |
| Network | on | Network tab, hosts and (if enabled below) probes |

A switched-off module shows a "module disabled" card in its tab instead of an empty view.

## Observation

| Option | Default | Effect |
|---|---|---|
| Observed domains | empty (= all) | Limit observation to chosen entity domains. Privacy exclusions always apply. |
| Numeric sampling | off | Needed for *signal* numeric sources (DOC008). Battery mapping works without it. |

**Never observed, whatever you choose (privacy):** `person`, `zone`, `device_tracker`,
`media_player`, `camera`, `text`, `input_text`. Sentinel's own entities are not observed either.

## Doctor

| Option | Default | Range | Effect |
|---|---|---|---|
| Unavailable threshold | 120 s | 30–3600 | How long an entity must be unavailable before DOC001 |
| Startup grace period | 120 s | 30–600 | No availability alarms right after a restart |
| Show new incidents as HA notifications | off | | Persistent notifications in HA. Nothing is sent outside HA. |

## Storage and retention

| Option | Default | Range | Effect |
|---|---|---|---|
| Observation retention | 7 days | 1–30 | Raw observations; numeric samples then live on as aggregates |
| Incident retention | 90 days | 7–365 | Resolved incidents and findings; active ones are never deleted |
| Cleanup window | 30 days | 7–365 | Objects removed from HA longer ago are dropped from Sentinel's data; HA objects inactive longer are listed in *Settings → Cleanup in Home Assistant* (Sentinel never deletes them) |
| Storage budget | 1024 MiB | 256–16384 | Warning at 80 %, restrictions at 95 % (DOC010) |

See [[Data and Privacy]] for every data class and its limit.

## Managed files

| Option | Default | Effect |
|---|---|---|
| Allow Sentinel to write managed files | off | Enables restore for automation files you register as managed |
| Managed files directory | `ha_sentinel_managed` | Only files inside this folder (in the config directory) can be managed |
| Reload automations after restore | on | Runs `automation.reload` and checks the restored automations are loaded |

How to set up a managed file: [[Config history|Panel Config History]].

## Network

| Option | Default | Range | Effect |
|---|---|---|---|
| Active network probes | off | | Level N1: probes only targets you approve |
| Probe interval | 60 s | 60–3600 | With ±10 % jitter |
| Allowed address ranges | `10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7` | | Probes to other addresses are refused (checked after every DNS lookup) |

Details: [[Network|Panel Network]].
