"""Builders for domain tests."""

from __future__ import annotations

import itertools

from custom_components.ha_sentinel.core.models import Observation, ObservationKind, new_uuid

BOOT = "4f0c3c1e-6c55-4a43-9a8e-2f7d3a0b9c11"
S = 1_000_000
_seq = itertools.count(1)


def avail(uid: str, to: str, at_us: int, frm: str | None = None, obs_id: str | None = None):
    seq = next(_seq)
    return Observation(
        id=obs_id or new_uuid(),
        ingest_seq=seq,
        boot_id=BOOT,
        source_id="ha.states",
        source_event_id=str(seq),
        kind=ObservationKind.AVAILABILITY_CHANGED,
        received_at_us=at_us,
        occurred_at_us=at_us,
        subject_uid=uid,
        payload={"from": frm, "to": to},
    )


def snapshot(uid: str, availability: str, at_us: int):
    seq = next(_seq)
    return Observation(
        id=new_uuid(),
        ingest_seq=seq,
        boot_id=BOOT,
        source_id="ha.states",
        source_event_id=str(seq),
        kind=ObservationKind.STATE_SNAPSHOT,
        received_at_us=at_us,
        subject_uid=uid,
        payload={"availability": availability},
    )


def battery(uid: str, value: float, at_us: int, unit: str = "%"):
    seq = next(_seq)
    return Observation(
        id=new_uuid(),
        ingest_seq=seq,
        boot_id=BOOT,
        source_id="ha.states",
        source_event_id=str(seq),
        kind=ObservationKind.NUMERIC_SAMPLE,
        received_at_us=at_us,
        occurred_at_us=at_us,
        subject_uid=uid,
        payload={"value": value, "unit": unit, "device_class": "battery"},
    )


def heartbeat(uid: str, at_us: int):
    seq = next(_seq)
    return Observation(
        id=new_uuid(),
        ingest_seq=seq,
        boot_id=BOOT,
        source_id="ha.states",
        source_event_id=str(seq),
        kind=ObservationKind.HEARTBEAT,
        received_at_us=at_us,
        occurred_at_us=at_us,
        subject_uid=uid,
        payload={},
    )
