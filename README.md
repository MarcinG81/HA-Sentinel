# HA Sentinel

Local health, change history and dependency insight for Home Assistant.
Status: **pre-MVP (0.1.0-dev)** — see [docs/PLAN.md](docs/PLAN.md).

What works today:

- Local ingestion of entity availability and battery samples (privacy-sensitive domains excluded).
- Private SQLite store (`<config>/ha_sentinel/sentinel.db`) with versioned migrations.
- Doctor rules DOC001 (unavailable ≥ 120 s), DOC002 (unknown ≥ 10 min), DOC003 (flapping),
  DOC004 (low battery), DOC005 (stale, explicit heartbeat only).
- Health score and coverage sensors; `binary_sensor` for Sentinel's own degradation.
- Configuration history: import automations, scripts or blueprints as export-only snapshots
  (safe YAML parser, secrets refused or redacted), semantic and text diff, YAML download.
- Autopilot lint AUT001–AUT010 with source positions, loop detection and analysis coverage.
  Static only: templates are never executed and no action is ever called.
- Dependency map from registries and imported YAML with provenance and impact queries.
- Sidebar panel **Sentinel** (admin only): overview, Doctor, configuration history, autopilot,
  map, diagnostics. PL and EN, light and dark themes, works at 360 px.
- Admin-only WebSocket API: `capabilities`, `overview`, `diagnostics`, `incidents/list|get|ack|suppress`,
  `subscribe` (all under `ha_sentinel/`), with per-user rate limits and idempotent mutations.

No cloud, no AI, no network probes. Install via HACS as a custom repository (Integration).

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install pytest-homeassistant-custom-component ruff home-assistant-frontend
.venv/bin/pytest

cd frontend-src && npm ci && npm test && npm run build   # rebuilds custom_components/ha_sentinel/frontend/ha-sentinel.js
```

The built bundle is committed: HACS installs never run npm.
