"""Recorder history adapter for replay (spec 2.1 "Historia stanów HA", 11.4).

Class A adapter: used only if the recorder is loaded and its API matches the qualified
signature; otherwise replay reports unsupported. History may be incomplete because of
recorder exclusions and retention, which the report states explicitly.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant

from ..core.clock import datetime_to_us
from ..core.errors import ErrorCode, SentinelError
from ..modules.autopilot.replay import StateEvent

MAX_ENTITIES = 50
MAX_EVENTS = 10_000


def available(hass: HomeAssistant) -> bool:
    return "recorder" in hass.config.components


async def fetch(hass: HomeAssistant, entity_ids: list[str], start: datetime, end: datetime
                ) -> tuple[dict[str, str | None], list[StateEvent], bool]:
    if not available(hass):
        raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "recorder is not loaded",
                            reason="recorder_unavailable")
    if len(entity_ids) > MAX_ENTITIES:
        raise SentinelError(ErrorCode.INVALID_REQUEST, "too many entities",
                            reason="too_many_entities")
    from homeassistant.components.recorder import get_instance, history

    try:
        result = await get_instance(hass).async_add_executor_job(
            lambda: history.get_significant_states(
                hass, start, end, entity_ids, None,
                include_start_time_state=True, significant_changes_only=False,
                minimal_response=False, no_attributes=True))
    except TypeError as err:  # the recorder API changed: do not guess (spec 2.2)
        raise SentinelError(ErrorCode.UNSUPPORTED_CAPABILITY, "history contract changed",
                            reason="history_contract") from err
    start_us = datetime_to_us(start)
    initial: dict[str, str | None] = {}
    events: list[StateEvent] = []
    for entity_id, states in result.items():
        previous = None
        for st in states:
            when = datetime_to_us(st.last_changed)
            if when <= start_us and entity_id not in initial:
                initial[entity_id] = st.state
                previous = st.state
                continue
            if st.state == previous:
                continue  # attribute-only update
            events.append(StateEvent(when, entity_id, st.state))
            previous = st.state
    events.sort(key=lambda e: (e.time_us, e.entity_id))
    truncated = len(events) > MAX_EVENTS
    return initial, events[:MAX_EVENTS], truncated
