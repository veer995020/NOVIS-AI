"""Error payloads. Used for HTTP error bodies and for the `error` field of a chat response."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    BAD_REQUEST = "BAD_REQUEST"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    GENERATION_FAILED = "GENERATION_FAILED"
    GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    CONVERSATION_BUSY = "CONVERSATION_BUSY"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HTTP_ERROR = "HTTP_ERROR"


class FieldIssue(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    field: str = Field(max_length=200, description="Dotted location, e.g. body.message")
    type: str = Field(
        max_length=100,
        description="Stable machine-readable error type, e.g. string_too_short or missing.",
    )
    message: str = Field(max_length=500, description="Human-readable reason. Never the submitted value.")


class ErrorDetail(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    details: list[FieldIssue] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    error: ErrorDetail
    request_id: str | None = Field(default=None, max_length=64)
