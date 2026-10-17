"""HA state adapter: turns HA states into Sentinel observations (spec 5.1).

Only an allowlist of fields is copied: availability, and for batteries the numeric value
with unit and device_class. Attributes are never copied wholesale.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import State

from ..const import PRIVACY_EXCLUDED_DOMAINS
from ..core.models import Availability

SOURCE_ID = "ha.states"


def is_observed(entity_id: str) -> bool:
    return entity_id.split(".", 1)[0] not in PRIVACY_EXCLUDED_DOMAINS


def availability_of(state: State | None) -> Availability | None:
    if state is None:
        return None
    if state.state == STATE_UNAVAILABLE:
        return Availability.UNAVAILABLE
    if state.state == STATE_UNKNOWN:
        return Availability.UNKNOWN
    return Availability.AVAILABLE


def battery_payload(state: State | None) -> dict[str, Any] | None:
    """Battery samples are selected by device_class and unit, never by entity name."""
    if state is None:
        return None
    attrs = state.attributes
    if attrs.get(ATTR_DEVICE_CLASS) != "battery" or attrs.get(ATTR_UNIT_OF_MEASUREMENT) != "%":
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return {"value": value, "unit": "%", "device_class": "battery"}


def update_payload(state: State | None) -> dict[str, Any] | None:
    """update.* entities: only whether an update is offered and the version string."""
    if state is None or not state.entity_id.startswith("update."):
        return None
    if state.state not in ("on", "off"):
        return None
    return {"available": state.state == "on",
            "latest_version": str(state.attributes.get("latest_version") or "")[:40]}
