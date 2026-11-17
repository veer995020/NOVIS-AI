"""In-memory conversation state. Temporary by design: nothing is persisted, state is
lost on restart, and only one process can see it. See README for limits.

Safety rules
- One asyncio.Lock guards every operation, so each call sees and changes a
  consistent state.
- A turn holds a lease on its conversation from start to finish. While the
  lease is held, no other turn can start and clear is refused.
- A turn's question and answer are written together, in one commit, only when a
  reply was produced. A failed, stopped-without-text, or abandoned turn writes
  nothing, so no unanswered question ever reaches a later prompt.
- Only the current lease holder can commit. A turn whose lease expired cannot
  write into a conversation that another turn now holds.
- Every change bumps `version`. A title update or clear must name the version it
  read, so a stale request is refused rather than overwriting newer data.
- Memory is bounded: at most `max_conversations`, conversations idle longer than
  `idle_seconds` are dropped, and each conversation keeps `max_messages` messages,
  oldest first. Busy conversations are never dropped.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from backend.schemas.response import Message
from backend.services.errors import (
    CapacityExceededError,
    ConversationBusyError,
    ConversationNotFoundError,
    VersionConflictError,
)
from backend.utils.clock import utcnow
from backend.utils.ids import new_conversation_id, new_id


@dataclass(frozen=True)
class ConversationSnapshot:
    id: str
    title: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    messages: tuple[Message, ...]


@dataclass
class _Record:
    id: str
    title: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    last_access: float
    messages: list[Message] = field(default_factory=list)
    lease_token: str | None = None
    lease_expires: float = 0.0


class ConversationStore:
    def __init__(
        self,
        *,
        max_messages: int,
        max_conversations: int,
        idle_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_messages = max_messages
        self._max_conversations = max_conversations
        self._idle_seconds = idle_seconds
        self._clock = clock
        # Least recently used first.
        self._records: OrderedDict[str, _Record] = OrderedDict()
        self._lock = asyncio.Lock()

    # The in-memory store holds nothing outside the process, so there is nothing to open or close.
    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    # ---- helpers. Call only while holding the lock. ----

    def _is_busy(self, record: _Record, now: float) -> bool:
        return record.lease_token is not None and record.lease_expires > now

    def _expire_idle(self, now: float) -> None:
        """Drop idle conversations. Records are kept least recently used first, so every
        expired record sits before the first fresh one. Scanning stops there, which makes
        the cost proportional to what expires, not to the number of conversations."""
        cutoff = now - self._idle_seconds
        expired: list[str] = []
        for conversation_id, record in self._records.items():
            if record.last_access > cutoff:
                break
            if not self._is_busy(record, now):
                expired.append(conversation_id)
        for conversation_id in expired:
            del self._records[conversation_id]

    def _evict_least_recent_idle(self, now: float) -> bool:
        for conversation_id, record in self._records.items():
            if not self._is_busy(record, now):
                del self._records[conversation_id]
                return True
        return False

    def _find(self, conversation_id: str, now: float) -> _Record:
        record = self._records.get(conversation_id)
        if record is None:
            raise ConversationNotFoundError()
        record.last_access = now
        self._records.move_to_end(conversation_id)
        return record

    def _require_lease(self, record: _Record, token: str, now: float) -> None:
        if record.lease_token != token or record.lease_expires <= now:
            raise ConversationBusyError(
                "This turn no longer holds the conversation, so its reply was not saved."
            )

    @staticmethod
    def _touch(record: _Record) -> None:
        record.version += 1
        record.updated_at = utcnow()

    @staticmethod
    def _snapshot(record: _Record) -> ConversationSnapshot:
        return ConversationSnapshot(
            id=record.id,
            title=record.title,
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            messages=tuple(record.messages),
        )

    # ---- create, read, update, clear ----

    async def create(self, title: str | None = None) -> ConversationSnapshot:
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            while len(self._records) >= self._max_conversations:
                if not self._evict_least_recent_idle(now):
                    raise CapacityExceededError()
            stamp = utcnow()
            record = _Record(
                id=new_conversation_id(),
                title=title,
                version=1,
                created_at=stamp,
                updated_at=stamp,
                last_access=now,
            )
            self._records[record.id] = record
            return self._snapshot(record)

    async def ensure(self, conversation_id: str) -> ConversationSnapshot:
        """Return the conversation, creating it under the given id if it does not exist yet.

        The id is checked by the request model. Creation obeys the same capacity limit as create().
        Conversation ids are names, not secrets: access is controlled by the access key.
        """
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            if conversation_id in self._records:
                return self._snapshot(self._find(conversation_id, now))
            while len(self._records) >= self._max_conversations:
                if not self._evict_least_recent_idle(now):
                    raise CapacityExceededError()
            stamp = utcnow()
            record = _Record(
                id=conversation_id,
                title=None,
                version=1,
                created_at=stamp,
                updated_at=stamp,
                last_access=now,
            )
            self._records[record.id] = record
            return self._snapshot(record)

    async def get(self, conversation_id: str) -> ConversationSnapshot:
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            return self._snapshot(self._find(conversation_id, now))

    async def update_title(
        self, conversation_id: str, title: str | None, expected_version: int
    ) -> ConversationSnapshot:
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            record = self._find(conversation_id, now)
            if record.version != expected_version:
                raise VersionConflictError(record.version)
            record.title = title
            self._touch(record)
            return self._snapshot(record)

    async def clear(self, conversation_id: str, expected_version: int) -> ConversationSnapshot:
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            record = self._find(conversation_id, now)
            if self._is_busy(record, now):
                raise ConversationBusyError(
                    "A turn is running on this conversation. Clear it after the reply finishes."
                )
            if record.version != expected_version:
                raise VersionConflictError(record.version)
            record.messages.clear()
            self._touch(record)
            return self._snapshot(record)

    # ---- turns ----

    async def acquire_turn(self, conversation_id: str, lease_seconds: float) -> str:
        """The lease alone. Same rules as start_turn, which does the work."""
        token, _ = await self.start_turn(conversation_id, lease_seconds, history_limit=0)
        return token

    async def release_turn(self, conversation_id: str, token: str) -> None:
        async with self._lock:
            record = self._records.get(conversation_id)
            if record is not None and record.lease_token == token:
                record.lease_token = None
                record.lease_expires = 0.0

    async def commit_turn(
        self, conversation_id: str, token: str, messages: Sequence[Message]
    ) -> int:
        """Save a finished turn's messages together. Requires the lease. Returns the new version."""
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            record = self._find(conversation_id, now)
            self._require_lease(record, token, now)
            record.messages.extend(messages)
            overflow = len(record.messages) - self._max_messages
            if overflow > 0:
                del record.messages[:overflow]
            self._touch(record)
            return record.version

    async def history(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            messages = self._find(conversation_id, now).messages
            if limit is None:
                return list(messages)
            return list(messages[-limit:]) if limit > 0 else []

    async def ensure_id(self, conversation_id: str) -> str:
        return (await self.ensure(conversation_id)).id

    async def start_turn(
        self, conversation_id: str, lease_seconds: float, history_limit: int
    ) -> tuple[str, list[Message]]:
        """acquire_turn and the newest history_limit messages, in one step under the lock."""
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            record = self._find(conversation_id, now)
            if self._is_busy(record, now):
                raise ConversationBusyError()
            token = new_id()
            record.lease_token = token
            record.lease_expires = now + lease_seconds
            recent = list(record.messages[-history_limit:]) if history_limit > 0 else []
            return token, recent

    async def version(self, conversation_id: str) -> int:
        async with self._lock:
            now = self._clock()
            self._expire_idle(now)
            return self._find(conversation_id, now).version

    async def count(self) -> int:
        """Number of live conversations. Used by the health check."""
        async with self._lock:
            self._expire_idle(self._clock())
            return len(self._records)
