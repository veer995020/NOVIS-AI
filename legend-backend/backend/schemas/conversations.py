"""Conversation request and response schemas. A conversation is temporary state:
it exists only in server memory, see README for its limits."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from backend.schemas.common import ConversationId, reject_unsafe_characters
from backend.schemas.response import Message

ConversationTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
    AfterValidator(reject_unsafe_characters),
]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    title: ConversationTitle | None = Field(default=None, description="Optional display title.")


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    title: ConversationTitle | None = Field(description="New title, or null to remove it.")
    expected_version: int = Field(
        ge=1, description="The version you read. The update is refused if it has changed."
    )


class ConversationClear(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    expected_version: int = Field(
        ge=1, description="The version you read. The clear is refused if it has changed."
    )


class Conversation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: ConversationId
    title: str | None = Field(max_length=80)
    version: int = Field(ge=1, description="Increases on every change. Send it back on updates.")
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(
        ge=0, description="Messages stored, including any omitted by `limit`."
    )
    messages: list[Message] = Field(description="The most recent messages, oldest first.")
