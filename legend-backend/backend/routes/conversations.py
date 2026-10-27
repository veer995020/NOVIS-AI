"""Temporary conversation state: create, read, update, and clear.

Conversations live in server memory only. They are lost on restart, and there
is no authentication, so the conversation id is the only access control.
Ids are random and unguessable, and must be kept private.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from backend.schemas.conversations import (
    Conversation,
    ConversationClear,
    ConversationCreate,
    ConversationUpdate,
)
from backend.schemas.errors import ErrorEnvelope
from backend.services.conversation_store import ConversationSnapshot

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

ConversationPath = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", description="Conversation id."),
]

_NOT_FOUND = {"model": ErrorEnvelope, "description": "conversation_not_found: no conversation has this id."}
_CONFLICT = {"model": ErrorEnvelope, "description": "version_conflict: the version sent is stale. Reload and retry."}
_BUSY = {"model": ErrorEnvelope, "description": "conversation_busy: a turn is running on this conversation."}


def _store(request: Request):
    return request.app.state.conversation_store


MESSAGE_LIMIT_DEFAULT = 200


def _to_schema(snapshot: ConversationSnapshot, limit: int = MESSAGE_LIMIT_DEFAULT) -> Conversation:
    return Conversation(
        id=snapshot.id,
        title=snapshot.title,
        version=snapshot.version,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        message_count=len(snapshot.messages),
        messages=list(snapshot.messages[-limit:]),
    )


@router.post(
    "",
    status_code=201,
    response_model=Conversation,
    summary="Create a conversation",
    description="Returns the new conversation, including its id. Send that id as `conversation_id` in chat requests.",
    responses={503: {"model": ErrorEnvelope, "description": "capacity_exceeded: every conversation is busy."}},
)
async def create_conversation(payload: ConversationCreate, request: Request) -> Conversation:
    return _to_schema(await _store(request).create(title=payload.title))


@router.get(
    "/{conversation_id}",
    response_model=Conversation,
    summary="Read a conversation",
    description=(
        "Returns the title, version, and the most recent `limit` messages, oldest first. "
        "`message_count` gives the total, so a client can tell when messages were omitted."
    ),
    responses={404: _NOT_FOUND},
)
async def read_conversation(
    conversation_id: ConversationPath,
    request: Request,
    limit: Annotated[
        int, Query(ge=1, le=MESSAGE_LIMIT_DEFAULT, description="Most recent messages to return.")
    ] = MESSAGE_LIMIT_DEFAULT,
) -> Conversation:
    return _to_schema(await _store(request).get(conversation_id), limit)


@router.patch(
    "/{conversation_id}",
    response_model=Conversation,
    summary="Change the title",
    description="Send the `version` you read as `expected_version`. A stale version returns 409.",
    responses={404: _NOT_FOUND, 409: _CONFLICT},
)
async def update_conversation(
    conversation_id: ConversationPath, payload: ConversationUpdate, request: Request
) -> Conversation:
    snapshot = await _store(request).update_title(
        conversation_id, payload.title, payload.expected_version
    )
    return _to_schema(snapshot)


@router.post(
    "/{conversation_id}/clear",
    response_model=Conversation,
    summary="Remove all messages and keep the conversation",
    description=(
        "Send the `version` you read as `expected_version`. Refused with 409 when the version is stale, "
        "or when a turn is running on the conversation."
    ),
    responses={404: _NOT_FOUND, 409: {"model": ErrorEnvelope, "description": "version_conflict or conversation_busy."}},
)
async def clear_conversation(
    conversation_id: ConversationPath, payload: ConversationClear, request: Request
) -> Conversation:
    return _to_schema(await _store(request).clear(conversation_id, payload.expected_version))
