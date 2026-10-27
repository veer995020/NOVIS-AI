"""Errors raised by the conversation and chat services. Each carries the HTTP status
and error code it maps to, so the central handler can translate it without guessing."""
from __future__ import annotations

from backend.schemas.errors import ErrorCode


class ConversationError(Exception):
    code: ErrorCode
    http_status: int


class ConversationNotFoundError(ConversationError):
    code = ErrorCode.CONVERSATION_NOT_FOUND
    http_status = 404

    def __init__(self) -> None:
        super().__init__("No conversation exists with that id.")


class VersionConflictError(ConversationError):
    code = ErrorCode.VERSION_CONFLICT
    http_status = 409

    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(
            f"The conversation has changed (current version is {current_version}). "
            "Reload it and retry with that version."
        )


class ConversationBusyError(ConversationError):
    code = ErrorCode.CONVERSATION_BUSY
    http_status = 409

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "An earlier turn is still running on this conversation. Wait for it to finish."
        )


class CapacityExceededError(ConversationError):
    code = ErrorCode.CAPACITY_EXCEEDED
    http_status = 503

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "The server is holding its maximum number of conversations. Try again later."
        )
