# Compatibility matrix

Published with every release (spec 19.1). "Qualified" means the contract tests and the
end-to-end checks listed below passed on that line; it is not a promise for other lines.

| Sentinel | Home Assistant | Python (from HA) | DB schema | Snapshot schema | API | Collector protocol |
|---|---|---|---|---|---|---|
| 0.1.0-dev | 2026.10.x (qualified on 2026.10.2) | 3.14 | 4 (reads 4 only) | 1 | 1 | — (R2) |

## Qualification evidence (2026-10-17)

- Automated: unit and integration tests on `homeassistant==2026.10.2`
  (`pytest-homeassistant-custom-component`), including config/options flows over the HTTP
  API, a real `automation.reload`, a real recorder (replay) and real sockets (probes).
- Manual on a live HA 2026.10.2 instance: install, config flow, sidebar panel, all panel
  tabs, managed file restore with reload, export download, network probes, restart with
  schema migration v2→v3, no "Detected blocking call" warnings.

## Class A adapters (feature-flagged by capability checks)

| Capability | Depends on | Behaviour if the contract differs |
|---|---|---|
| `history.read`, `automation.replay` | `recorder.history.get_significant_states` | `unsupported` (`history_contract`) |
| `managed_yaml.restore` | `automation.reload` service | `manual_reload_required` |
| Panel registration | `panel_custom.async_register_panel`, `http.StaticPathConfig` | setup fails loudly, Core unaffected after removal |

Not qualified yet: HA beta, previous monthly line, HA Supervisor-less Core installs.
