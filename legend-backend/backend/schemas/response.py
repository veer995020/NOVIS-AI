"""Response schemas: what the backend returns.

A chat outcome is a ChatResponse whether or not a reply was produced:
  status "success" or "stopped" -> content holds the reply, error is null.
  status "error"                -> content is null, error describes the failure.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from backend.config import MAX_REPLY_CHARS
from backend.schemas.attachments import ATTACHMENT_MAX_COUNT, AttachmentMeta
from backend.schemas.common import ConversationId
from backend.schemas.errors import ErrorDetail
from backend.utils.clock import utcnow
from backend.utils.ids import new_id

ReplyText = Annotated[str, StringConstraints(max_length=MAX_REPLY_CHARS)]


class ChatStatus(StrEnum):
    """success: a complete reply. stopped: the user stopped it; content is the partial reply.
    error: no reply; see error."""

    SUCCESS = "success"
    STOPPED = "stopped"
    ERROR = "error"


# Paths whose every outcome is a ChatResponse. All other endpoints use the ErrorEnvelope.
# The /api paths are the documented ones. The root paths are the ones the chat page calls.
CHAT_PATHS = frozenset({
    "/api/chat", "/api/chat/stream", "/api/research", "/api/code", "/api/write",
    "/chat", "/research", "/code", "/write",
})


class Reliability(BaseModel):
    """What the server checked about one reply. It never says a fact is verified.

    `traceable_claims` counts sentences whose specific details appear in the user's own messages.
    `unverified_claims` counts sentences with specific details that appear nowhere in the conversation.
    `sources_checked` is always false: the server does not fetch or check sources.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    sources_checked: Literal[False] = False
    traceable_claims: int = Field(ge=0)
    unverified_claims: int = Field(ge=0)
    injection_suspected: bool
    content_redacted: bool
    flags: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """The one shape every chat outcome takes, including validation failures and refusals.

    Every field is always present. A field with no value is null, never omitted.
      success or stopped: content is the reply (a string), error is null.
      error:              content is null, error is an ErrorDetail.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    conversation_id: ConversationId | None
    model: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None
    mode: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None
    content: ReplyText | None
    status: ChatStatus
    error: ErrorDetail | None
    detail: str | None = Field(
        default=None,
        description="The error message as plain text, equal to error.message. Null unless status is error. "
        "Clients that read a top-level detail string (the chat page does) show it without parsing error.",
    )
    request_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
        description="The id of the request that produced this response. The same value as the X-Request-ID header. "
        "Quote it when reporting a problem.",
    )
    reliability: Reliability | None = Field(
        default=None,
        description="What the server checked about the reply. Null when there is no reply. "
        "Not a verification: sources_checked is always false.",
    )
    conversation_version: int | None = Field(
        default=None,
        ge=1,
        description="The conversation's version after this turn. A turn that saves a reply increases it by one. "
        "Null when the request did not reach a conversation. Use it for optimistic updates (expected_version).",
    )

    @model_validator(mode="after")
    def _outcome_is_consistent(self) -> ChatResponse:
        if self.status is ChatStatus.ERROR:
            if self.error is None:
                raise ValueError("error is required when status is 'error'")
            if self.content is not None:
                raise ValueError("content must be null when status is 'error'")
            self.detail = self.error.message
        else:
            if self.content is None:
                raise ValueError("content is required when status is 'success' or 'stopped'")
            if self.error is not None:
                raise ValueError("error must be null unless status is 'error'")
            self.detail = None
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
    """One turn in a conversation. Attachment content is never stored, only its metadata."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(default_factory=new_id)
    role: Literal["user", "assistant"]
    content: str
    attachments: list[AttachmentMeta] = Field(default_factory=list, max_length=ATTACHMENT_MAX_COUNT)
    created_at: datetime = Field(default_factory=utcnow)
