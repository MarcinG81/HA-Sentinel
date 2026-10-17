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

from .const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_DOCTOR_STARTUP_GRACE_S,
    OPT_DOCTOR_UNAVAILABLE_S,
    OPT_RETENTION_OBSERVATIONS_DAYS,
    OPT_STORAGE_MAX_TOTAL_MIB,
)
from .core.models import new_uuid


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


class SentinelOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        opts = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(OPT_DOCTOR_UNAVAILABLE_S, default=opts[OPT_DOCTOR_UNAVAILABLE_S]):
                    vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                vol.Required(OPT_DOCTOR_STARTUP_GRACE_S,
                             default=opts[OPT_DOCTOR_STARTUP_GRACE_S]):
                    vol.All(vol.Coerce(int), vol.Range(min=30, max=600)),
                vol.Required(OPT_RETENTION_OBSERVATIONS_DAYS,
                             default=opts[OPT_RETENTION_OBSERVATIONS_DAYS]):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Required(OPT_STORAGE_MAX_TOTAL_MIB, default=opts[OPT_STORAGE_MAX_TOTAL_MIB]):
                    vol.All(vol.Coerce(int), vol.Range(min=256, max=16384)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
