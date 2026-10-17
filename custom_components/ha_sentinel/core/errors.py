"""Domain error codes exposed by the Sentinel API (spec 15.2)."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    FORBIDDEN = "forbidden"
    NOT_READY = "not_ready"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PLAN_EXPIRED = "plan_expired"
    VALIDATION_FAILED = "validation_failed"
    RATE_LIMITED = "rate_limited"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class SentinelError(Exception):
    """Error with a stable code; `details` must only contain safe fields."""

    def __init__(self, code: ErrorCode, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
