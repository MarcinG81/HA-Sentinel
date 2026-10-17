# HA Sentinel

Local health, change history and dependency insight for Home Assistant: what stopped
working, since when, what it depends on, and what you can safely do about it.

Status: **0.1.0-dev** — roadmap in [docs/PLAN.md](docs/PLAN.md), operations in
[docs/RUNBOOK.md](docs/RUNBOOK.md), supported versions in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

No cloud, no account, no telemetry, no AI. Everything runs inside your Home Assistant.

## Features

**Doctor** — availability (DOC001), unknown state (DOC002), flapping (DOC003), low battery
(DOC004, also for manually mapped levels), missing heartbeat (DOC005, only for declared
contracts), missing or stale references in imported YAML (DOC006), missed expected runs of
an automation on your weekday/time calendar (DOC007), weak signal against a scale you set
(DOC008), update available (DOC009), Sentinel storage pressure (DOC010). Missing data is shown
as missing, never as "healthy". Health score with visible components and unit weights,
per-object rule overrides, planned maintenance windows, acknowledge and suppress.

**Probable cause** — findings of one episode are grouped into one incident when they share
an explicit dependency (device, integration, network endpoint). Hypotheses are ranked with
time agreement, share of affected dependents, path confidence, direct evidence and
counter-evidence. A ranking is not a probability; you can confirm or reject it. A config
change on the dependency path in the 30 minutes before is listed separately, and a problem
that comes back points to its previous incident.

**Configuration history** — import automations, scripts or blueprints (safe YAML parser,
plain secrets refused or redacted), semantic and text diff, YAML download. Optional
*managed files*: restore a previous version with preview, plan approval, journaled atomic
write, automation reload, verification and automatic rollback.

**Autopilot** (off by default) — static lint AUT001–AUT010 with source positions, loop
detection, conflict and hysteresis checks, analysis coverage, a small draft wizard that goes
through the same parser and lint, and a replay of recorded history that never calls any action.

**Dependency map** — registries, imported YAML and network hosts, with provenance and
confidence on every edge; "what depends on this" and "what does this depend on"; versioned,
so older revisions keep the names and references they had.

**Network** — level N0 (default) only uses what HA knows, plus hosts you describe and link to
devices yourself (suggestions never link on their own). Optional N1 probes approved
TCP/HTTP/HTTPS targets only, with an address allowlist, DNS-rebinding protection and shared
rate limits.

**Operations** — admin-only panel in the sidebar (PL/EN, light/dark, 360 px, WCAG 2.1 AA
audited), module switches, opt-in HA notifications, redacted export, data deletion, retention
purge with per-class budgets, HA backup status and an explicit backup request, Repairs issues
for damaged or newer databases, diagnostics download, metrics.

## Install

HACS → Integrations → ⋮ → Custom repositories → add
`https://github.com/MarcinG81/HA-Sentinel` as *Integration*,
install, restart Home Assistant, then add **HA Sentinel** in Settings → Devices & services.
Details, upgrade and downgrade: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install pytest-homeassistant-custom-component ruff home-assistant-frontend
.venv/bin/pytest                       # unit + integration tests on a real HA test instance
.venv/bin/pytest -m perf tests/perf -s # standard-profile load test (spec 21.1 budgets)

cd frontend-src && npm ci
npm test && npm run build              # rebuilds custom_components/ha_sentinel/frontend/ha-sentinel.js
npm run a11y -- "/path/to/chrome"      # axe-core WCAG 2.1 AA audit of every view
```

The built bundle is committed: HACS installs never run npm. Code and comments are in
English; the specification and plan are in Polish.
