"""Chat flow shared by the JSON, streaming, and task endpoints.

  begin()  resolves the conversation (creating one if no id was given) and takes
           its lease. Failures here are raised as conversation errors, which the
           HTTP layer turns into 404 or 409 responses before any stream starts.
  run()    generates the reply under a timeout and streams the events. It always
           ends with exactly one `done` event. The question and answer are saved
           together in one commit, and only when a reply was produced.
  The lease is released on every exit path.

Outcomes are never reported as "success" unless text was generated and
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

from backend.config import MAX_REPLY_CHARS, Settings
from backend.prompts.system import (
    SYSTEM_PROMPT,
    build_messages,
    estimate_depth,
    render_turn,
)
from backend.reliability.classify import RETRYABLE, ErrorCategory, classify_error
from backend.reliability.input_guard import inspect_input
from backend.reliability.output_guard import analyse_reply
from backend.schemas.attachments import AttachmentMeta
from backend.schemas.errors import ErrorCode, ErrorDetail
from backend.schemas.events import StreamDeltaEvent, StreamDoneEvent, StreamStartEvent
from backend.schemas.request import ChatRequest
from backend.schemas.response import ChatResponse, ChatStatus, Message, Reliability
from backend.services.errors import CapacityExceededError, ConversationError
from backend.services.model_router import (
    ModelRouter,
    ModelUnavailableError,
    resolve_model_id,
)
from backend.services.providers import ProviderError, ProviderRateLimitedError
from backend.services.storage import ConversationStorage
from backend.utils.ids import new_id

logger = logging.getLogger(__name__)

GENERATION_FAILED_MESSAGE = "The reply could not be generated. Please try again."
GENERATION_TIMEOUT_MESSAGE = "The reply took too long to generate. Please try again."
UPSTREAM_RATE_LIMIT_MESSAGE = "The model is busy right now. Please try again shortly."
# Backoff between provider attempts: doubles from the base, capped. A provider Retry-After is honoured within the cap.
RETRY_BASE_SECONDS = 0.2
RETRY_CAP_SECONDS = 2.0
EMPTY_REPLY_MESSAGE = "The model returned an empty reply. Please try again."
MODEL_UNAVAILABLE_MESSAGE = "The requested model is not available. Choose another model or try later."

REPLY_TOO_LONG_MESSAGE = "The reply was longer than the server allows, so it was not saved. Please try again."


class _ReplyTooLongError(Exception):
    """Raised inside the generation loop when a model returns more than the contract allows."""


# Extra time a lease outlives the generation timeout, to cover commit and cleanup.
LEASE_MARGIN_SECONDS = 30.0


def _retry_delay(attempt: int, exc: ProviderError) -> float:
    if isinstance(exc, ProviderRateLimitedError) and exc.retry_after:
        return min(max(float(exc.retry_after), RETRY_BASE_SECONDS), RETRY_CAP_SECONDS)
    return min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2 ** (attempt - 1)))


@dataclass(frozen=True)
class Turn:
    request: ChatRequest
    model_id: str
    conversation_id: str
    request_id: str
    token: str
    user_message: Message
    cancel: asyncio.Event
    history: tuple[Message, ...] = ()


@dataclass
class _ActiveTurn:
    cancel: asyncio.Event
    deadline: float  # monotonic time after which the turn's lease has certainly expired


class ChatService:
    def __init__(self, store: ConversationStorage, settings: Settings, models: ModelRouter) -> None:
        self._store = store
        self._settings = settings
        self._models = models
        self._active: dict[str, _ActiveTurn] = {}

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

    async def begin(self, req: ChatRequest, request_id: str | None = None) -> Turn:
        """Resolve the conversation and take its lease. Raises ConversationError.

        The turn slot is reserved before the first await. Between the capacity
        check and the reservation nothing yields, so concurrent requests cannot
        overshoot the limit.
        """
        request_id = request_id or new_id()
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
                # Created on first use if the page names a conversation it has not used yet.
                conversation_id = await self._store.ensure_id(req.conversation_id)
            # The lease and the newest messages the prompt can hold, read in one step.
            token, history = await self._store.start_turn(
                conversation_id, lease, self._settings.max_context_messages
            )
        except BaseException:
            self._active.pop(request_id, None)
            raise
        return Turn(
            request=req,
            model_id=resolve_model_id(req.model),
            conversation_id=conversation_id,
            request_id=request_id,
            token=token,
            history=tuple(history),
            user_message=Message(
                role="user",
                content=req.message,
                attachments=[AttachmentMeta.from_attachment(a) for a in req.attachments],
            ),
            cancel=cancel,
        )

    async def run(self, turn: Turn) -> AsyncGenerator[BaseModel, None]:
        req = turn.request
        input_report = inspect_input(req.message, req.attachments)
        depth = estimate_depth(req.message, req.attachments)
        try:
            yield StreamStartEvent(request_id=turn.request_id, conversation_id=turn.conversation_id)

            parts: list[str] = []
            error: ErrorDetail | None = None
            total_chars = 0
            # Read when the lease was taken: only the newest messages the prompt can hold.
            history = list(turn.history)

            # The current question is part of the prompt but is not saved yet. Attachment
            # content is included here only; history keeps the metadata.
            prompt_message = Message(
                role="user",
                content=render_turn(req.message, req.attachments),
            )
            messages = build_messages(
                mode=req.mode,
                history=[*history, prompt_message],
                max_context=self._settings.max_context_messages,
                depth=depth,
            )
            # The conversation the user has written, used to tell traceable details from unverified ones.
            context = "\n".join(
                [m.content for m in history if m.role == "user"]
                + [req.message]
                + [a.content for a in req.attachments]
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
                    # One overall deadline covers every attempt, so retries cannot outlast the turn's lease.
                    async with asyncio.timeout(self._settings.generation_timeout_seconds):
                        attempts = self._settings.provider_max_attempts
                        for attempt in range(1, attempts + 1):
                            emitted = False
                            try:
                                async for piece in route.provider.generate(
                                    model=route.provider_model,
                                    messages=messages,
                                    cancel=turn.cancel,
                                ):
                                    total_chars += len(piece)
                                    if total_chars > MAX_REPLY_CHARS:
                                        raise _ReplyTooLongError()
                                    parts.append(piece)
                                    emitted = True
                                    yield StreamDeltaEvent(text=piece)
                            except ProviderError as exc:
                                # Retry only a transient failure, and only before any text reached the client.
                                # A retry after partial output would show the client the same text twice.
                                retryable = classify_error(exc) in RETRYABLE
                                if retryable and not emitted and attempt < attempts and not turn.cancel.is_set():
                                    logger.warning(
                                        "Provider attempt %d of %d failed, retrying (request %s): %s",
                                        attempt, attempts, turn.request_id, exc,
                                        extra={"event": "chat", "category": classify_error(exc).value},
                                    )
                                    await asyncio.sleep(_retry_delay(attempt, exc))
                                    continue
                                raise
                            break
                except _ReplyTooLongError:
                    logger.warning("Model reply exceeded %d characters (request %s)", MAX_REPLY_CHARS, turn.request_id)
                    error = ErrorDetail(code=ErrorCode.GENERATION_FAILED, message=REPLY_TOO_LONG_MESSAGE)
                except TimeoutError:
                    logger.warning("Reply generation timed out (request %s)", turn.request_id)
                    error = ErrorDetail(
                        code=ErrorCode.GENERATION_TIMEOUT, message=GENERATION_TIMEOUT_MESSAGE
                    )
                except ProviderError as exc:
                    logger.warning("Provider failed (request %s): %s", turn.request_id, exc,
                                   extra={"event": "chat", "category": classify_error(exc).value})
                    if isinstance(exc, ProviderRateLimitedError):
                        # The upstream limit outlasted the retries. The client is told to wait, not that generation failed.
                        error = ErrorDetail(code=ErrorCode.RATE_LIMITED, message=UPSTREAM_RATE_LIMIT_MESSAGE)
                    else:
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

            reliability: Reliability | None = None
            if error is None and content:
                try:
                    analysis = analyse_reply(
                        content,
                        context=context,
                        policy=SYSTEM_PROMPT,
                        mode=req.mode,
                        depth=depth,
                        input_report=input_report,
                    )
                except Exception:
                    # A reply that cannot be checked is not delivered. Fail closed.
                    logger.exception("Reply analysis failed (request %s)", turn.request_id,
                                     extra={"event": "chat", "category": ErrorCategory.INTERNAL.value})
                    error = ErrorDetail(code=ErrorCode.GENERATION_FAILED, message=GENERATION_FAILED_MESSAGE)
                    content = ""
                else:
                    content = analysis.content
                    reliability = Reliability(
                        traceable_claims=analysis.traceable_claims,
                        unverified_claims=analysis.unverified_claims,
                        injection_suspected=analysis.injection_suspected,
                        content_redacted=analysis.content_redacted,
                        flags=list(analysis.flags),
                    )

            if error is not None:
                status = ChatStatus.ERROR
                content = ""
            elif turn.cancel.is_set():
                status = ChatStatus.STOPPED
            else:
                status = ChatStatus.SUCCESS

            # Commit the question and answer together, under the lease.
            # A turn with no reply saves nothing.
            committed_version: int | None = None
            if content:
                try:
                    committed_version = await self._store.commit_turn(
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
                content=None if status is ChatStatus.ERROR else content,
                status=status,
                error=error,
                reliability=None if status is ChatStatus.ERROR else reliability,
                request_id=turn.request_id,
                conversation_version=(
                    committed_version
                    if committed_version is not None
                    else await self._version_after(turn.conversation_id)
                ),
            )
            yield StreamDoneEvent(request_id=turn.request_id, response=response)
        finally:
            await self._store.release_turn(turn.conversation_id, turn.token)
            self._active.pop(turn.request_id, None)

    async def stream_turn(self, turn: Turn) -> AsyncIterator[dict[str, Any]]:
        """Yields JSON-ready event dicts that match the StreamEvent schema."""
        async for event in self.run(turn):
            yield event.model_dump(mode="json")

    async def _version_after(self, conversation_id: str) -> int | None:
        """The conversation's current version. None if the conversation is no longer held."""
        try:
            return await self._store.version(conversation_id)
        except ConversationError:
            return None

    async def complete(self, req: ChatRequest, request_id: str | None = None) -> ChatResponse:
        turn = await self.begin(req, request_id)
        final: ChatResponse | None = None
        async for event in self.run(turn):
            if isinstance(event, StreamDoneEvent):
                final = event.response
        if final is None:
            raise RuntimeError("Chat flow ended without a final event.")
        return final
