"""Error payloads. Used for HTTP error bodies and for the `error` field of a chat response."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    GENERATION_FAILED = "generation_failed"
    GENERATION_TIMEOUT = "generation_timeout"
    MODEL_UNAVAILABLE = "model_unavailable"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    VERSION_CONFLICT = "version_conflict"
    CONVERSATION_BUSY = "conversation_busy"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    INTERNAL_ERROR = "internal_error"
    HTTP_ERROR = "http_error"


class FieldIssue(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    field: str = Field(max_length=200, description="Dotted location, e.g. body.message")
    message: str = Field(max_length=500)


class ErrorDetail(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    details: list[FieldIssue] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    error: ErrorDetail
    request_id: str | None = Field(default=None, max_length=64)
