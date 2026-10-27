"""Chat flow shared by the JSON, streaming, and task endpoints.

  begin()  resolves the conversation (creating one if no id was given) and takes
           its lease. Failures here are raised as conversation errors, which the
           HTTP layer turns into 404 or 409 responses before any stream starts.
  run()    generates the reply under a timeout and streams the events. It always
           ends with exactly one `done` event. The question and answer are saved
           together in one commit, and only when a reply was produced.
  The lease is released on every exit path.

Outcomes are never reported as "completed" unless text was generated and
committed under the lease.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from backend.config import Settings
from backend.prompts.system import build_messages
from backend.schemas.chat import ChatRequest, ChatResponse, ChatStatus, Message
from backend.schemas.errors import ErrorCode, ErrorDetail
from backend.schemas.events import StreamDeltaEvent, StreamDoneEvent, StreamStartEvent
from backend.services.conversation_store import ConversationStore
from backend.services.errors import CapacityExceededError, ConversationError
from backend.services.model_router import (
    ModelRouter,
    ModelUnavailableError,
    resolve_model_id,
)
from backend.services.providers import ProviderError
from backend.utils.ids import new_id

logger = logging.getLogger(__name__)

GENERATION_FAILED_MESSAGE = "The reply could not be generated. Please try again."
GENERATION_TIMEOUT_MESSAGE = "The reply took too long to generate. Please try again."
EMPTY_REPLY_MESSAGE = "The model returned an empty reply. Please try again."
MODEL_UNAVAILABLE_MESSAGE = "The requested model is not available. Choose another model or try later."

# Extra time a lease outlives the generation timeout, to cover commit and cleanup.
LEASE_MARGIN_SECONDS = 30.0


@dataclass(frozen=True)
class Turn:
    request: ChatRequest
    model_id: str
    conversation_id: str
    request_id: str
    token: str
    user_message: Message
    cancel: asyncio.Event


@dataclass
class _ActiveTurn:
    cancel: asyncio.Event
    deadline: float  # monotonic time after which the turn's lease has certainly expired


class ChatService:
    def __init__(self, store: ConversationStore, settings: Settings, models: ModelRouter) -> None:
        self._store = store
        self._settings = settings
        self._models = models
        self._active: dict[str, _ActiveTurn] = {}

    @property
    def active_request_count(self) -> int:
        self._prune_active()
        return len(self._active)

    def stop(self, request_id: str) -> bool:
        active = self._active.get(request_id)
        if active is None:
            return False
        active.cancel.set()
        return True

    def _lease_seconds(self) -> float:
        return self._settings.generation_timeout_seconds + LEASE_MARGIN_SECONDS

    def _prune_active(self) -> None:
        now = time.monotonic()
        for request_id in [rid for rid, a in self._active.items() if a.deadline <= now]:
            del self._active[request_id]

    async def begin(self, req: ChatRequest) -> Turn:
        """Resolve the conversation and take its lease. Raises ConversationError.

        The turn slot is reserved before the first await. Between the capacity
        check and the reservation nothing yields, so concurrent requests cannot
        overshoot the limit.
        """
        request_id = new_id()
        cancel = asyncio.Event()
        lease = self._lease_seconds()
        self._prune_active()
        if len(self._active) >= self._settings.max_concurrent_turns:
            raise CapacityExceededError(
                "The server is busy with its maximum number of replies. Try again shortly."
            )
        self._active[request_id] = _ActiveTurn(cancel, deadline=time.monotonic() + lease)
        try:
            if req.conversation_id is None:
                conversation_id = (await self._store.create()).id
            else:
                conversation_id = req.conversation_id
            token = await self._store.acquire_turn(conversation_id, lease)
        except BaseException:
            self._active.pop(request_id, None)
            raise
        return Turn(
            request=req,
            model_id=resolve_model_id(req.model),
            conversation_id=conversation_id,
            request_id=request_id,
            token=token,
            user_message=Message(role="user", content=req.message),
            cancel=cancel,
        )

    async def run(self, turn: Turn) -> AsyncGenerator[BaseModel, None]:
        req = turn.request
        try:
            yield StreamStartEvent(request_id=turn.request_id, conversation_id=turn.conversation_id)

            parts: list[str] = []
            error: ErrorDetail | None = None
            try:
                history = await self._store.history(turn.conversation_id)
            except ConversationError as exc:
                # The conversation was dropped or taken over mid-turn: report it, do not raise.
                logger.warning("Conversation unavailable (request %s): %s", turn.request_id, exc)
                error = ErrorDetail(code=exc.code, message=str(exc))
                history = []

            # The current question is part of the prompt but is not saved yet.
            messages = build_messages(
                mode=req.mode,
                history=[*history, turn.user_message],
                max_context=self._settings.max_context_messages,
            )

            route = None
            if error is None:
                try:
                    route = self._models.route_for(turn.model_id)
                except ModelUnavailableError:
                    logger.warning("Model has no route (request %s)", turn.request_id)
                    error = ErrorDetail(
                        code=ErrorCode.MODEL_UNAVAILABLE, message=MODEL_UNAVAILABLE_MESSAGE
                    )

            if route is not None:
                try:
                    async with asyncio.timeout(self._settings.generation_timeout_seconds):
                        async for piece in route.provider.generate(
                            model=route.provider_model,
                            messages=messages,
                            cancel=turn.cancel,
                        ):
                            parts.append(piece)
                            yield StreamDeltaEvent(text=piece)
                except TimeoutError:
                    logger.warning("Reply generation timed out (request %s)", turn.request_id)
                    error = ErrorDetail(
                        code=ErrorCode.GENERATION_TIMEOUT, message=GENERATION_TIMEOUT_MESSAGE
                    )
                except ProviderError as exc:
                    logger.warning("Provider failed (request %s): %s", turn.request_id, exc)
                    error = ErrorDetail(
                        code=ErrorCode.GENERATION_FAILED, message=GENERATION_FAILED_MESSAGE
                    )
                except Exception:
                    logger.exception("Reply generation failed (request %s)", turn.request_id)
                    error = ErrorDetail(
                        code=ErrorCode.GENERATION_FAILED, message=GENERATION_FAILED_MESSAGE
                    )

            content = "".join(parts)
            if error is None and not content and not turn.cancel.is_set():
                logger.warning("Model returned an empty reply (request %s)", turn.request_id)
                error = ErrorDetail(code=ErrorCode.GENERATION_FAILED, message=EMPTY_REPLY_MESSAGE)

            if error is not None:
                status = ChatStatus.ERROR
                content = ""
            elif turn.cancel.is_set():
                status = ChatStatus.STOPPED
            else:
                status = ChatStatus.COMPLETED

            # Commit the question and answer together, under the lease.
            # A turn with no reply saves nothing.
            if content:
                try:
                    await self._store.commit_turn(
                        turn.conversation_id,
                        turn.token,
                        [turn.user_message, Message(role="assistant", content=content)],
                    )
                except ConversationError as exc:
                    # Nothing was saved. Keep an earlier error if there was one.
                    logger.warning("Could not save the turn (request %s): %s", turn.request_id, exc)
                    status = ChatStatus.ERROR
                    content = ""
                    if error is None:
                        error = ErrorDetail(code=exc.code, message=str(exc))

            response = ChatResponse(
                conversation_id=turn.conversation_id,
                model=turn.model_id,
                mode=req.mode,
                content=content,
                status=status,
                error=error,
            )
            yield StreamDoneEvent(request_id=turn.request_id, response=response)
        finally:
            await self._store.release_turn(turn.conversation_id, turn.token)
            self._active.pop(turn.request_id, None)

    async def stream_turn(self, turn: Turn) -> AsyncIterator[dict[str, Any]]:
        """Yields JSON-ready event dicts that match the StreamEvent schema."""
        async for event in self.run(turn):
            yield event.model_dump(mode="json")

    async def stream(self, req: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        turn = await self.begin(req)
        async for event in self.stream_turn(turn):
            yield event

    async def complete(self, req: ChatRequest) -> ChatResponse:
        turn = await self.begin(req)
        final: ChatResponse | None = None
        async for event in self.run(turn):
            if isinstance(event, StreamDoneEvent):
                final = event.response
        if final is None:
            raise RuntimeError("Chat flow ended without a final event.")
        return final
