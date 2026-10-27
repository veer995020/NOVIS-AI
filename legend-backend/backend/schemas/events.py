"""Server-Sent Event payloads for POST /api/chat/stream.

A stream is: one `start`, zero or more `delta`, then exactly one `done` whose
`response` is a ChatResponse. Errors arrive inside `done` with status "error".
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.config import MAX_REPLY_CHARS
from backend.schemas.chat import ChatResponse, ConversationId


class StreamStartEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["start"] = "start"
    request_id: str = Field(min_length=1, max_length=64, description="Pass to the stop endpoint.")
    conversation_id: ConversationId


class StreamDeltaEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["delta"] = "delta"
    text: str = Field(max_length=MAX_REPLY_CHARS)


class StreamDoneEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["done"] = "done"
    request_id: str = Field(min_length=1, max_length=64)
    response: ChatResponse


StreamEvent = Annotated[
    StreamStartEvent | StreamDeltaEvent | StreamDoneEvent,
    Field(discriminator="type"),
]
