"""Shared response rules for every chat-style endpoint, so they behave the same way."""
from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from backend.schemas.errors import ErrorCode, ErrorEnvelope
from backend.schemas.response import ChatResponse, ChatStatus

# OpenAPI documentation for the error statuses a chat-style endpoint can return.
# Each status names the body shape that is most common for it. Where a status can
# carry either shape, the description says so.
CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorEnvelope,
        "description": "conversation_not_found: the conversation id is unknown. The request did not start.",
    },
    409: {
        "model": ErrorEnvelope,
        "description": "conversation_busy: an earlier turn is still running on this conversation. "
        "The request did not start. Rarely, the reply could not be saved: then the body is a "
        "ChatResponse with status error and code conversation_busy.",
    },
    502: {
        "model": ChatResponse,
        "description": "generation_failed: the model call failed or returned no text. status is error.",
    },
    503: {
        "model": ChatResponse,
        "description": "model_unavailable: no route serves the model. status is error. "
        "Also returned as an ErrorEnvelope with code capacity_exceeded when the server is at its turn limit.",
    },
    504: {
        "model": ChatResponse,
        "description": "generation_timeout: generation took longer than the server allows. status is error.",
    },
}

# How long a client should wait after an upstream rate limit. The provider's own hint is not forwarded yet.
UPSTREAM_RETRY_AFTER_SECONDS = 5

_STATUS_BY_CODE = {
    ErrorCode.RATE_LIMITED: 429,
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


def chat_json_response(response: ChatResponse) -> Response:
    # Pydantic encodes the model in one pass. Going through a dict and then json.dumps encodes it twice.
    status_code = http_status_for(response)
    headers = None
    if status_code == 429:
        headers = {"Retry-After": str(UPSTREAM_RETRY_AFTER_SECONDS)}
    return Response(
        content=response.model_dump_json(),
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )
