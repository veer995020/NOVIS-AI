"""Request schemas: what the frontend may send.

Every rule here is checked by Pydantic before any service runs. A rejected request
gets a 422 with one FieldIssue per problem, each with a stable `type`.

Rules at a glance:
  conversation_id  optional. 1-64 characters of letters, digits, '_' or '-'.
  message          required. 1-8000 characters after trimming. Not blank.
  model            optional, default "rapid". An allowed model id.
  mode             optional, default "general" (chat only). An allowed mode name.
  attachments      optional, default []. At most 3 text files, 128000 characters in total.

All models are strict and reject unknown fields.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from pydantic_core import PydanticCustomError

from backend.config import DEFAULT_MODE, MAX_MESSAGE_CHARS
from backend.schemas.attachments import (
    ATTACHMENT_MAX_COUNT,
    Attachment,
    check_attachment_list,
)
from backend.schemas.common import ConversationId, reject_unsafe_characters
from backend.schemas.options import require_allowed_mode, require_allowed_model

NAME_MAX_LENGTH = 64
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _check_name_shape(value: object) -> object:
    """Model and mode names: say plainly what is wrong, rather than printing a pattern."""
    if isinstance(value, str) and not NAME_PATTERN.match(value):
        raise PydanticCustomError(
            "invalid_name",
            "Use 1 to 64 lowercase letters, digits, '.', '_' or '-', starting with a letter or digit.",
        )
    return value


def _reject_blank_message(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        raise PydanticCustomError("empty_message", "Message must not be empty.")
    return value


# Order matters: the BeforeValidator goes last so that Pydantic's standard length
# checks run first and report string_too_short / string_too_long.
ModelName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=NAME_MAX_LENGTH),
    AfterValidator(require_allowed_model),
    BeforeValidator(_check_name_shape),
]
ModeName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=NAME_MAX_LENGTH),
    AfterValidator(require_allowed_mode),
    BeforeValidator(_check_name_shape),
]
MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS),
    AfterValidator(reject_unsafe_characters),
    BeforeValidator(_reject_blank_message),
]


class ToolFlags(BaseModel):
    """The page's tool switches. Web search is not available here and is refused when on.
    The other switches do not change the answer, so they are accepted and not acted on."""

    model_config = ConfigDict(strict=True, extra="forbid")

    webSearch: bool = False  # noqa: N815 - the page's own name
    files: bool = False
    images: bool = False
    voice: bool = False


class TaskRequest(BaseModel):
    """Input for the chat and task endpoints. A task endpoint fixes the mode itself.

    `mode` is optional on a task endpoint. If sent, it must be that endpoint's own mode.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    conversation_id: ConversationId | None = Field(
        default=None, description="Omit to start a new conversation."
    )
    message: MessageText = Field(description="The user's message.")
    model: ModelName | None = Field(
        default=None, description="Model id from GET /api/chat/options. Omit to use rapid."
    )
    attachments: list[Attachment] = Field(
        default_factory=list,
        max_length=ATTACHMENT_MAX_COUNT,
        description="Text files for this turn. At most 3, and 128000 characters in total.",
    )
    mode: str | None = Field(
        default=None,
        max_length=64,
        description="Optional on a task endpoint. If sent, it must be that endpoint's mode.",
    )
    tools: ToolFlags | None = Field(default=None, description="The page's tool switches.")

    @field_validator("attachments")
    @classmethod
    def _total_within_limit(cls, value: list[Attachment]) -> list[Attachment]:
        return check_attachment_list(value)

    @field_validator("tools")
    @classmethod
    def _web_search_unavailable(cls, value: ToolFlags | None) -> ToolFlags | None:
        if value is not None and value.webSearch:
            raise PydanticCustomError("unsupported_tool", "Web search is not available on this server.")
        return value


class ChatRequest(TaskRequest):
    """Input for POST /api/chat, where the caller chooses the mode."""

    mode: ModeName = Field(
        default=DEFAULT_MODE, description="Mode name from GET /api/chat/options."
    )
