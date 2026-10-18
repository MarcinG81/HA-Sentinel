# Compatibility matrix

Published with every release (spec 19.1). "Qualified" means the contract tests and the
end-to-end checks listed below passed on that line; it is not a promise for other lines.

| Sentinel | Home Assistant | Python (from HA) | DB schema | Snapshot schema | API | Collector protocol |
|---|---|---|---|---|---|---|
| 0.2.0 | 2026.5.1 – 2026.10.x (qualified on 2026.5.1 and 2026.10.2) | 3.14 | 14 (reads 14 only) | 1 | 1 | — (R2) |

## Qualification evidence (2026-10-17)

- Automated: unit and integration tests on `homeassistant==2026.10.2`
  (`pytest-homeassistant-custom-component`), including config/options flows over the HTTP
  API, a real `automation.reload`, a real recorder (replay) and real sockets (probes).
- Manual on a live HA 2026.10.2 instance: install, config flow, sidebar panel, all panel
  tabs, managed file restore with reload, export download, network probes, restart with
  schema migration v2→v3, no "Detected blocking call" warnings.

## Qualification on 2026.5.1 (2026-10-18)

- Automated: the full test suite on `homeassistant==2026.5.1`
  (`pytest-homeassistant-custom-component==0.13.330`, frontend `20260429.3`); CI runs it
  next to the current release.
- Manual on a fresh HA 2026.5.1 instance: onboarding, config flow, options form with
  sections, every API command used by the panel, panel registration and bundle download.
- Differences handled in `adapters/compat.py`: device registry iteration (ids before
  2026.10, entries after) and `DeviceEntry.config_entry_id` (2026.10) versus
  `config_entries` (earlier). The options form validates the managed directory in the
  step, because the 2026.5 form serializer cannot convert `vol.Match` inside a section.
- HA itself refuses to start on a configuration written by a newer HA (storage files
  are not downgradable); that is HA behaviour, not Sentinel's.

## Class A adapters (feature-flagged by capability checks)

| Capability | Depends on | Behaviour if the contract differs |
|---|---|---|
| `history.read`, `automation.replay` | `recorder.history.get_significant_states` | `unsupported` (`history_contract`) |
| `managed_yaml.restore` | `automation.reload` service | `manual_reload_required` |
| Panel registration | `panel_custom.async_register_panel`, `http.StaticPathConfig` | setup fails loudly, Core unaffected after removal |

Not qualified yet: HA beta, previous monthly line, HA Supervisor-less Core installs.
