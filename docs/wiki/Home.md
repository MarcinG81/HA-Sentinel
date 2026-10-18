# HA Sentinel

**Local health, change history and dependency insight for Home Assistant.**

HA Sentinel watches your Home Assistant installation and answers four questions:

- **What stopped working?**
- **Since when?**
- **What does it depend on?**
- **What can I safely do about it?**

Everything runs inside your Home Assistant: no cloud, no account, no telemetry, no AI.
Sentinel keeps its own SQLite database in `<config>/ha_sentinel/` and never writes to the
Recorder database, to `configuration.yaml` or to files owned by the HA automation editor.

> **Status:** 0.1.0, first public release, looking for testers. See [[Testing and Feedback]].

## Start here

| | |
|---|---|
| [[Installation]] | Requirements, HACS install, first start, update, uninstall |
| [[Configuration]] | Every option in the integration options form |
| [[Troubleshooting]] | Panel not visible, unexpected incidents, download errors |

## The panel

Sentinel adds a **Sentinel** item to the sidebar (administrators only) with eight tabs:

| Tab | What it is for |
|---|---|
| [Overview](Panel-Overview) | Health score, coverage, top incidents, score components, module states |
| [Doctor](Panel-Doctor) | Incidents, maintenance windows, rules per object, numeric sources |
| [Config history](Panel-Config-History) | Imported YAML versions, diffs, managed files and safe restore |
| [Autopilot](Panel-Autopilot) | Static checks of automations, draft wizard, replay of history |
| [Map](Panel-Map) | Dependency map: what depends on what, with history |
| [Network](Panel-Network) | Hosts you describe, device links, optional approved probes |
| [Diagnostics](Panel-Diagnostics) | Queues, gaps, performance, versions |
| [Settings](Panel-Settings) | Options link, HA backups, data, export, deletion |

## Reference

- [[Doctor Rules]]: every rule (DOC001–DOC010, NET001) with thresholds and exceptions
- [[Incidents and Probable Cause]]: grouping, ranking, counter-evidence, change hypotheses
- [[Rules per Object]]: overrides, heartbeat contracts, expected runs, numeric sources
- [[Autopilot Checks]]: lint rules AUT001–AUT010
- [[Entities]]: the sensors Sentinel publishes
- [[Data and Privacy]]: what is stored, retention, budgets, export, deletion

## Principles

- **Missing data is shown as missing, never as "healthy".** A score without enough coverage
  is greyed out as partial.
- **A ranking is not a probability.** A probable cause is a hypothesis with evidence and
  counter-evidence; only you or an explicit diagnostic event confirms it.
- **Nothing is merged or linked behind your back.** Re-added devices, network hosts and
  causes are suggestions until you confirm them.
- **Nothing changes your home on its own.** No service is called except an explicit
  restore of a managed file or a backup request you start.

Source code: https://github.com/MarcinG81/HA-Sentinel · Licence: GPL-3.0
