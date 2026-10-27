"""Shared response rules for every chat-style endpoint, so they behave the same way."""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from backend.schemas.chat import ChatResponse, ChatStatus
from backend.schemas.errors import ErrorCode

# OpenAPI documentation for the error statuses a chat-style endpoint can return.
CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    502: {"model": ChatResponse, "description": "Reply generation failed."},
    409: {"model": ChatResponse, "description": "The conversation changed during the turn."},
    503: {"model": ChatResponse, "description": "The requested model is not available."},
    504: {"model": ChatResponse, "description": "Reply generation timed out."},
}

_STATUS_BY_CODE = {
    ErrorCode.GENERATION_TIMEOUT: 504,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.CONVERSATION_BUSY: 409,
    ErrorCode.CONVERSATION_NOT_FOUND: 404,
}


def http_status_for(response: ChatResponse) -> int:
    """A failed reply is never reported with a success status code."""
    if response.status is not ChatStatus.ERROR:
        return 200
    if response.error is not None:
        return _STATUS_BY_CODE.get(response.error.code, 502)
    return 502


def chat_json_response(response: ChatResponse) -> JSONResponse:
    return JSONResponse(response.model_dump(mode="json"), status_code=http_status_for(response))
