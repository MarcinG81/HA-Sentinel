"""Direct evidence about RCA candidates (spec 9.2 feature E), collected on the HA loop.

E=1: an explicit provider status says offline (config entry failed or retrying, or a
device connectivity sensor is off). E=0.5: the device's status entity itself is
unavailable. Nothing else counts as direct evidence; timing alone never does.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from ..core.clock import datetime_to_us
from ..modules.correlation.correlator import CandidateEvidence

_FAILED_ENTRY_STATES = frozenset({
    ConfigEntryState.SETUP_ERROR,
    ConfigEntryState.SETUP_RETRY,
    ConfigEntryState.FAILED_UNLOAD,
    ConfigEntryState.MIGRATION_ERROR,
})


class EvidenceCollector:
    def __init__(self) -> None:
        # HA does not expose when an entry started failing; remember our first sighting.
        self._entry_failing_since: dict[str, int] = {}

    @callback
    def collect(self, hass: HomeAssistant, now_us: int) -> dict[str, CandidateEvidence]:
        out: dict[str, CandidateEvidence] = {}
        failing: set[str] = set()
        for entry in hass.config_entries.async_entries():
            if entry.disabled_by is not None or entry.state not in _FAILED_ENTRY_STATES:
                continue
            failing.add(entry.entry_id)
            since = self._entry_failing_since.setdefault(entry.entry_id, now_us)
            out[f"config_entry:{entry.entry_id}"] = CandidateEvidence(
                1.0, since, f"config_entry_state:{entry.state.value}")
        for entry_id in list(self._entry_failing_since):
            if entry_id not in failing:
                del self._entry_failing_since[entry_id]

        for reg in er.async_get(hass).entities.values():
            if reg.domain != "binary_sensor" or reg.device_id is None:
                continue
            if (reg.device_class or reg.original_device_class) != "connectivity":
                continue
            state = hass.states.get(reg.entity_id)
            if state is None:
                continue
            uid = f"device:{reg.device_id}"
            if state.state == STATE_OFF:
                ev = CandidateEvidence(1.0, datetime_to_us(state.last_changed), "connectivity_off")
            elif state.state == STATE_UNAVAILABLE:
                ev = CandidateEvidence(0.5, datetime_to_us(state.last_changed),
                                       "status_entity_unavailable")
            else:
                continue
            if uid not in out or ev.e > out[uid].e:
                out[uid] = ev
        return out
