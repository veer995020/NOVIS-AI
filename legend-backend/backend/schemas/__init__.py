"""Pydantic models that define the frontend/backend contract. Import from here."""
from backend.schemas.conversations import (
    Conversation,
    ConversationClear,
    ConversationCreate,
    ConversationUpdate,
)
from backend.schemas.errors import ErrorCode, ErrorDetail, ErrorEnvelope, FieldIssue
from backend.schemas.events import (
    StreamDeltaEvent,
    StreamDoneEvent,
    StreamEvent,
    StreamStartEvent,
)
from backend.schemas.options import configure_allowed_options
from backend.schemas.request import ChatRequest, TaskRequest
from backend.schemas.response import (
    ChatOptions,
    ChatResponse,
    ChatStatus,
    Message,
    StopResponse,
)

__all__ = [
    "ChatOptions",
    "Conversation",
    "ConversationClear",
    "ConversationCreate",
    "ConversationUpdate",
    "ChatRequest",
    "ChatResponse",
    "ChatStatus",
    "ErrorCode",
    "ErrorDetail",
    "ErrorEnvelope",
    "FieldIssue",
    "Message",
    "StopResponse",
    "TaskRequest",
    "StreamDeltaEvent",
    "StreamDoneEvent",
    "StreamEvent",
    "StreamStartEvent",
    "configure_allowed_options",
]
