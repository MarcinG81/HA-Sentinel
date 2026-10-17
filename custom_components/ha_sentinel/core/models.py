"""Domain models (spec 6). Must not import homeassistant.

Conventions: persistent IDs are UUID v4 stored as TEXT, times are UTC microseconds
(`*_us`), JSON has no NaN/Infinity, observation payloads are at most 8 KiB.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..const import MAX_DTO_BYTES


class ModuleState(StrEnum):
    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPING = "stopping"


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    DISABLED = "disabled"


class ObservationKind(StrEnum):
    AVAILABILITY_CHANGED = "availability.changed"
    STATE_SNAPSHOT = "state.snapshot"
    NUMERIC_SAMPLE = "numeric.sample"
    REGISTRY_CHANGED = "registry.changed"
    COVERAGE_GAP = "coverage.gap"
    UPDATE_STATE = "update.state"

    @property
    def is_control(self) -> bool:
        return self is not ObservationKind.NUMERIC_SAMPLE


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    INFO = "info"


class FindingStatus(StrEnum):
    PENDING = "pending"  # condition met, threshold not yet reached
    ACTIVE = "active"
    RESOLVED = "resolved"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED = "suppressed"
    RESOLVED = "resolved"


class CoverageStatus(StrEnum):
    MISSING = "missing"
    PARTIAL = "partial"
    COMPLETE = "complete"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_JOB_STATES


_TERMINAL_JOB_STATES = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}
)


def new_uuid() -> str:
    return str(uuid.uuid4())


def is_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 4
    except (ValueError, AttributeError, TypeError):
        return False


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and Infinity are not allowed")
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def dumps_strict(value: Any) -> str:
    """Canonical UTF-8 JSON without NaN/Infinity."""
    _reject_non_finite(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


@dataclass(frozen=True, slots=True)
class Quality:
    coverage: str = "live"  # live | historical | resync
    time_trusted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"coverage": self.coverage, "time_trusted": self.time_trusted}


@dataclass(frozen=True, slots=True)
class Observation:
    """Immutable DTO built in the callback in O(1) (spec 5.2)."""

    id: str
    ingest_seq: int
    boot_id: str
    source_id: str
    kind: ObservationKind
    received_at_us: int
    payload: dict[str, Any]
    subject_uid: str | None = None
    occurred_at_us: int | None = None
    source_event_id: str | None = None
    quality: Quality = field(default_factory=Quality)
    schema_version: int = 1

    def payload_json(self) -> str:
        encoded = dumps_strict(self.payload)
        if len(encoded.encode()) > MAX_DTO_BYTES:
            raise ValueError("observation payload exceeds 8 KiB")
        return encoded

    def to_api(self) -> dict[str, Any]:
        from .clock import us_to_rfc3339

        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "boot_id": self.boot_id,
            "ingest_seq": self.ingest_seq,
            "source_id": self.source_id,
            "kind": str(self.kind),
            "received_at": us_to_rfc3339(self.received_at_us),
            "occurred_at": (
                us_to_rfc3339(self.occurred_at_us) if self.occurred_at_us is not None else None
            ),
            "subject_uid": self.subject_uid,
            "payload": self.payload,
            "quality": self.quality.as_dict(),
        }
        return out


@dataclass(slots=True)
class Finding:
    id: str
    rule_id: str
    rule_version: int
    subject_uid: str
    status: FindingStatus
    severity: Severity
    first_seen_us: int
    last_seen_us: int
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    coverage: CoverageStatus = CoverageStatus.COMPLETE
    resolved_at_us: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return f"{self.rule_id}:{self.subject_uid}"


@dataclass(frozen=True, slots=True)
class CoverageSegment:
    source_id: str
    scope: str
    from_us: int
    to_us: int | None
    status: CoverageStatus
    reason: str
