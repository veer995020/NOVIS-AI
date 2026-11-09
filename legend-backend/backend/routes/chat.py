"""Chat endpoints. Request validation happens in the schemas. Failures are raised and
turned into responses by backend.error_handlers, so routes hold no error logic."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import DEFAULT_MODE, DEFAULT_MODEL
from backend.routes.responses import CHAT_RESPONSES, chat_json_response
from backend.schemas.errors import ErrorEnvelope
from backend.schemas.options import allowed_models, allowed_modes
from backend.schemas.request import ChatRequest
from backend.schemas.response import ChatOptions, ChatResponse, StopResponse
from backend.services.chat import ChatService
from backend.utils.sse import format_sse

router = APIRouter(prefix="/api/chat", tags=["chat"])

RequestIdPath = Annotated[
    str,
    Path(pattern=r"^[0-9a-f]{32}$", description="Request id from the stream's `start` event."),
]


def _service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.get(
    "/options",
    response_model=ChatOptions,
    summary="Model and mode options for this deployment",
    description="Model ids, which of them are available here, the default model, and the modes.",
)
async def options(request: Request) -> ChatOptions:
    return ChatOptions(
        models=list(allowed_models()),
        available_models=list(request.app.state.model_router.available_models()),
        default_model=DEFAULT_MODEL,
        modes=list(allowed_modes()),
        default_mode=DEFAULT_MODE,
    )


@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a message and wait for the reply",
    description=(
        "Returns the whole reply as a ChatResponse. The HTTP status matches the outcome: 200 for completed "
        "or stopped. For a failure to produce a reply (generation_failed, generation_timeout, model_unavailable) "
        "the body is a ChatResponse with status `error`. A request that cannot start (unknown conversation, "
        "busy conversation, server at its turn limit) returns an ErrorEnvelope. A failure is never returned as success."
    ),
    responses={
        **CHAT_RESPONSES,
        404: {"model": ErrorEnvelope, "description": "conversation_not_found: the request cannot start."},
        409: {"model": ErrorEnvelope, "description": "conversation_busy: the request cannot start."},
        503: {"model": ErrorEnvelope, "description": "capacity_exceeded: the server is at its turn limit."},
    },
)
async def chat(payload: ChatRequest, request: Request) -> JSONResponse:
    response = await _service(request).complete(payload)
    return chat_json_response(response)


@router.post(
    "/stream",
    summary="Send a message and stream the reply as Server-Sent Events",
    description=(
        "Events: `start` (carries request_id), zero or more `delta`, then one `done` whose `response` "
        "is a ChatResponse. Generation failures arrive inside `done` with status `error`, because the "
        "HTTP status is sent before generation starts. Conversation and capacity errors are returned "
        "before the stream starts, as normal HTTP errors."
    ),
    responses={
        404: {"model": ErrorEnvelope, "description": "CONVERSATION_NOT_FOUND"},
        409: {"model": ErrorEnvelope, "description": "CONVERSATION_BUSY"},
        503: {"model": ErrorEnvelope, "description": "CAPACITY_EXCEEDED"},
    },
)
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    service = _service(request)
    # Leasing the conversation happens before the stream starts, so a busy or
    # missing conversation is an HTTP error rather than a broken stream.
    turn = await service.begin(payload)

    async def events() -> AsyncIterator[str]:
        async for event in service.stream_turn(turn):
            yield format_sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


@router.post(
    "/{request_id}/stop",
    response_model=StopResponse,
    summary="Stop an in-flight stream",
    description="Keeps the partial reply. Returns 404 when no active turn has this id.",
    responses={404: {"model": ErrorEnvelope, "description": "NOT_FOUND"}},
)
async def stop(request_id: RequestIdPath, request: Request) -> StopResponse:
    if not _service(request).stop(request_id):
        raise HTTPException(status_code=404, detail="No active request with that id.")
    return StopResponse(request_id=request_id, stopped=True)
