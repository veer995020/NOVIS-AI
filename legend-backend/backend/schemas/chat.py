"""Chat request and response schemas. These define the frontend/backend contract.

Validation rules:
  conversation_id  optional; 1-64 characters of letters, digits, '_' or '-'
  message          required; 1-MAX_MESSAGE_CHARS characters after trimming; no control characters
  model            required; lowercase name in the configured allowlist
  mode             optional, default "standard"; lowercase name in the configured allowlist
All models are strict: a number is not accepted where a string is expected, and unknown fields are rejected.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from backend.config import DEFAULT_MODE, MAX_MESSAGE_CHARS, MAX_REPLY_CHARS
from backend.schemas.errors import ErrorDetail
from backend.schemas.options import require_allowed_mode, require_allowed_model
from backend.utils.clock import utcnow
from backend.utils.ids import new_id

_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
_ALLOWED_CONTROL_CHARACTERS = frozenset("\n\t\r")


def reject_unsafe_characters(value: str) -> str:
    for char in value:
        if char in _ALLOWED_CONTROL_CHARACTERS:
            continue
        if unicodedata.category(char) in ("Cc", "Cs"):
            raise PydanticCustomError(
                "unsafe_character",
                "Message contains a control or unpaired surrogate character.",
            )
    return value


ConversationId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=_CONVERSATION_ID_PATTERN),
]
ModelName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=_NAME_PATTERN),
    AfterValidator(require_allowed_model),
]
ModeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=_NAME_PATTERN),
    AfterValidator(require_allowed_mode),
]
MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS),
    AfterValidator(reject_unsafe_characters),
]
ReplyText = Annotated[str, StringConstraints(max_length=MAX_REPLY_CHARS)]


class TaskRequest(BaseModel):
    """Input shared by every chat-style endpoint. A task endpoint fixes the mode itself."""

    model_config = ConfigDict(strict=True, extra="forbid")

    conversation_id: ConversationId | None = Field(
        default=None, description="Omit to start a new conversation."
    )
    message: MessageText = Field(description="The user's message.")
    model: ModelName | None = Field(
        default=None, description="Model id from GET /api/chat/options. Omit to use rapid."
    )


class ChatRequest(TaskRequest):
    """Input for POST /api/chat, where the caller chooses the mode."""

    mode: ModeName = Field(
        default=DEFAULT_MODE, description="Mode name from GET /api/chat/options."
    )


class ChatStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


class ChatResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    conversation_id: ConversationId
    model: str = Field(min_length=1, max_length=64)
    mode: str = Field(min_length=1, max_length=64)
    content: ReplyText
    status: ChatStatus
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def _status_matches_error(self) -> ChatResponse:
        if self.status is ChatStatus.ERROR:
            if self.error is None:
                raise ValueError("error is required when status is 'error'")
            if self.content:
                raise ValueError("content must be empty when status is 'error'")
        elif self.error is not None:
            raise ValueError("error must be null unless status is 'error'")
        return self


class ChatOptions(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    models: list[str] = Field(description="Every model id the API accepts.")
    available_models: list[str] = Field(description="Model ids with a configured route.")
    default_model: str
    modes: list[str]
    default_mode: str


class StopResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    request_id: str
    stopped: bool


class Message(BaseModel):
    """One turn in a conversation."""

    id: str = Field(default_factory=new_id)
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=utcnow)
