"""HA diagnostics download (spec 20.1): built from an allowlist, never a dump of hass.data.

No entity ids, names, aliases, addresses or paths are included.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import SentinelConfigEntry
from .const import SCHEMA_VERSION


async def async_get_config_entry_diagnostics(hass: HomeAssistant,
                                             entry: SentinelConfigEntry) -> dict[str, Any]:
    runtime = entry.runtime_data
    diag = await runtime.diagnostics()
    overview = runtime.overview()
    return {
        "version": runtime.version,
        "schema_version": SCHEMA_VERSION,
        "state": diag["state"],
        "reason_code": diag["reason_code"],
        "storage": {k: diag["storage"][k] for k in ("mode", "schema_version", "db_bytes",
                                                    "observations")},
        "queues": diag["queues"],
        "doctor": {k: diag["doctor"][k] for k in ("active_findings", "flapping_partial")},
        "coverage_gaps": [{k: g[k] for k in ("scope", "status", "reason")}
                          for g in diag["coverage_gaps"]],
        "metrics": diag["metrics"],
        "modules": overview["modules"],
        "counters": overview["counters"],
        "health": {k: overview["health"][k] for k in ("score", "coverage_pct", "units")},
        "capabilities": [c.as_dict() for c in runtime.capabilities.values()],
        "options": {k: v for k, v in entry.options.items() if k != "network_allowlist"},
    }
