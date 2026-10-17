"""Config and options flow (spec 18.2, 27.1). Never asks for an HA admin token."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
    OPT_INGESTION_ALLOWED_DOMAINS,
    OPT_INGESTION_NUMERIC_SAMPLING,
    OPT_MANAGED_DIR,
    OPT_MANAGED_RELOAD,
    OPT_MANAGED_WRITE_ENABLED,
    OPT_MODULES_AUTOPILOT,
    OPT_MODULES_CONFIG_HISTORY,
    OPT_MODULES_DEPENDENCY_MAPPER,
    OPT_MODULES_DOCTOR,
    OPT_MODULES_NETWORK,
    OPT_NETWORK_ACTIVE_PROBES,
    OPT_NETWORK_ALLOWLIST,
    OPT_NETWORK_INTERVAL_S,
    OPT_NOTIFICATIONS_ENABLED,
    OPT_RETENTION_INCIDENTS_DAYS,
    OPT_RETENTION_OBSERVATIONS_DAYS,
    OPT_STORAGE_MAX_TOTAL_MIB,
    PRIVACY_EXCLUDED_DOMAINS,
)
from .core.models import new_uuid


def _allowlist_ok(value: str) -> bool:
    from .modules.network.targets import TargetRejected, parse_allowlist

    try:
        return bool(parse_allowlist(value))
    except TargetRejected:
        return False


class SentinelConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # single_config_entry in manifest.json enforces a single instance.
        if user_input is not None:
            return self.async_create_entry(
                title="HA Sentinel",
                data={"instance_id": new_uuid()},
                options=dict(DEFAULT_OPTIONS),
            )
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SentinelOptionsFlow()


# Option groups shown as collapsible sections; stored flat in entry.options.
SECTIONS: dict[str, tuple[str, ...]] = {
    "modules": (OPT_MODULES_DOCTOR, OPT_MODULES_CONFIG_HISTORY, OPT_MODULES_AUTOPILOT,
                OPT_MODULES_DEPENDENCY_MAPPER, OPT_MODULES_NETWORK),
    "ingestion": (OPT_INGESTION_ALLOWED_DOMAINS, OPT_INGESTION_NUMERIC_SAMPLING),
    "doctor": (OPT_DOCTOR_UNAVAILABLE_S, OPT_DOCTOR_STARTUP_GRACE_S, OPT_NOTIFICATIONS_ENABLED),
    "storage": (OPT_RETENTION_OBSERVATIONS_DAYS, OPT_RETENTION_INCIDENTS_DAYS,
                OPT_STORAGE_MAX_TOTAL_MIB),
    "managed": (OPT_MANAGED_WRITE_ENABLED, OPT_MANAGED_DIR, OPT_MANAGED_RELOAD),
    "network": (OPT_NETWORK_ACTIVE_PROBES, OPT_NETWORK_INTERVAL_S, OPT_NETWORK_ALLOWLIST),
}


def _int(lo: int, hi: int) -> vol.All:
    return vol.All(vol.Coerce(int), vol.Range(min=lo, max=hi))


class SentinelOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        flat: dict[str, Any] = {}
        for group in (user_input or {}).values():
            if isinstance(group, dict):
                flat.update(group)
        if user_input is not None:
            if _allowlist_ok(flat.get(OPT_NETWORK_ALLOWLIST, "")):
                return self.async_create_entry(data={**self.config_entry.options, **flat})
            errors["base"] = "invalid_cidr"
        opts = {**DEFAULT_OPTIONS, **self.config_entry.options, **flat}
        # Domains present now, minus privacy exclusions (they always win, spec 27.1).
        domains = sorted(
            ({s.domain for s in self.hass.states.async_all()}
             | set(opts[OPT_INGESTION_ALLOWED_DOMAINS])) - PRIVACY_EXCLUDED_DOMAINS)
        fields: dict[str, Any] = {
            OPT_MODULES_DOCTOR: bool,
            OPT_MODULES_CONFIG_HISTORY: bool,
            OPT_MODULES_AUTOPILOT: bool,
            OPT_MODULES_DEPENDENCY_MAPPER: bool,
            OPT_MODULES_NETWORK: bool,
            OPT_INGESTION_ALLOWED_DOMAINS: SelectSelector(SelectSelectorConfig(
                options=domains, multiple=True, mode=SelectSelectorMode.DROPDOWN)),
            OPT_INGESTION_NUMERIC_SAMPLING: bool,
            OPT_DOCTOR_UNAVAILABLE_S: _int(30, 3600),
            OPT_DOCTOR_STARTUP_GRACE_S: _int(30, 600),
            OPT_NOTIFICATIONS_ENABLED: bool,
            OPT_RETENTION_OBSERVATIONS_DAYS: _int(1, 30),
            OPT_RETENTION_INCIDENTS_DAYS: _int(7, 365),
            OPT_STORAGE_MAX_TOTAL_MIB: _int(256, 16384),
            OPT_MANAGED_WRITE_ENABLED: bool,
            OPT_MANAGED_DIR: vol.All(str, vol.Match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")),
            OPT_MANAGED_RELOAD: bool,
            OPT_NETWORK_ACTIVE_PROBES: bool,
            OPT_NETWORK_INTERVAL_S: _int(60, 3600),
            # Plain str: the schema must stay serialisable for the frontend form;
            # CIDR validation happens in the step and is reported as a form error.
            OPT_NETWORK_ALLOWLIST: str,
        }
        schema = vol.Schema({
            vol.Required(name): section(
                vol.Schema({vol.Required(key, default=opts[key]): fields[key] for key in keys}),
                {"collapsed": name not in ("modules", "doctor")},
            )
            for name, keys in SECTIONS.items()
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
