"""Conversation storage in PostgreSQL. Same rules as ConversationStore, enforced by the database.

- Each conversation is one row. Reading and changing it happens in one statement that takes the row lock
  (UPDATE ... RETURNING), so two requests cannot both pass a check and both write.
- Creation takes one advisory lock, so the capacity limit holds when several processes create at once.
- A turn's lease is a token and an expiry on the conversation row, compared with the database clock.
- A commit reserves its message positions and bumps the version in one statement, then inserts the
  messages in one pipelined batch. Only the current lease holder can commit.
- Idle conversations are removed by the database when they are not busy. The sweep runs at most every
  few seconds per process, not on every call.

The connection string is a credential. It is held as a Secret and is never logged.
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from importlib import resources
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from backend.schemas.attachments import AttachmentMeta
from backend.schemas.response import Message
from backend.services.conversation_store import ConversationSnapshot
from backend.services.errors import (
    CapacityExceededError,
    ConversationBusyError,
    ConversationNotFoundError,
    VersionConflictError,
)
from backend.utils.ids import new_conversation_id, new_id

_CAPACITY_LOCK = 7_341_001  # fixed: serialises conversation creation across processes
_SCHEMA_LOCK = 7_341_002
_MAX_EXPIRY_INTERVAL = 5.0

_LEASE_BUSY = "This turn no longer holds the conversation, so its reply was not saved."
_CLEAR_BUSY = "A turn is running on this conversation. Clear it after the reply finishes."
_BUSY = "A turn is already running on this conversation."

_TOUCH = "UPDATE conversations SET last_access = now() WHERE id = %s RETURNING id, title, version, created_at, updated_at, lease_token, (lease_expires > now()) AS lease_live"


def _message(row: dict[str, Any]) -> Message:
    return Message(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        attachments=[AttachmentMeta.model_validate(item) for item in row["attachments"]],
        created_at=row["created_at"],
    )


class PostgresConversationStore:
    def __init__(
        self,
        *,
        dsn: str,
        max_messages: int,
        max_conversations: int,
        idle_seconds: float = 3600.0,
        pool_max: int = 10,
    ) -> None:
        self._max_messages = max_messages
        self._max_conversations = max_conversations
        self._idle_seconds = float(idle_seconds)
        # Short enough that a conversation expires close to on time, long enough that the sweep is rare.
        self._expiry_interval = min(_MAX_EXPIRY_INTERVAL, self._idle_seconds / 4)
        self._next_expiry = 0.0
        self._dsn = dsn
        self._pool_max = pool_max
        self._pool = self._build_pool()

    def _build_pool(self) -> AsyncConnectionPool[Any]:
        # Autocommit: a single statement is its own transaction, so it costs no BEGIN/COMMIT round trips.
        # Multi-statement operations open a transaction explicitly.
        return AsyncConnectionPool(
            conninfo=self._dsn,
            min_size=1,
            max_size=self._pool_max,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    # ---- lifecycle ----

    async def open(self) -> None:
        if self._pool.closed:
            # A closed pool cannot be reopened. The same store may be started again (for example after a shutdown).
            self._pool = self._build_pool()
        await self._pool.open(wait=True, timeout=15)
        schema = resources.files("backend.db").joinpath("schema.sql").read_text(encoding="utf-8")
        async with self._pool.connection() as conn, conn.transaction():
            # Two processes starting together must not apply the schema at the same time.
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            await conn.execute(schema)

    async def close(self) -> None:
        await self._pool.close()

    # ---- helpers ----

    async def _in_transaction(self, work: Callable[[AsyncConnection[Any]], Awaitable[Any]]) -> Any:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                return await work(conn)

    async def _autocommit(self, work: Callable[[AsyncConnection[Any]], Awaitable[Any]]) -> Any:
        """For work that is one statement, or a sweep followed by one statement. No BEGIN/COMMIT round trips."""
        async with self._pool.connection() as conn:
            return await work(conn)

    async def _sweep(self, conn: AsyncConnection[Any]) -> None:
        """Remove idle, not-busy conversations. Runs at most every few seconds per process."""
        now = time.monotonic()
        if now < self._next_expiry:
            return
        self._next_expiry = now + self._expiry_interval
        await conn.execute(
            "DELETE FROM conversations "
            "WHERE last_access < now() - make_interval(secs => %s) "
            "AND (lease_expires IS NULL OR lease_expires <= now())",
            (self._idle_seconds,),
        )

    async def _lock_and_count(self, conn: AsyncConnection[Any]) -> int:
        """Take the capacity lock and count the conversations, in one statement. Returns the count."""
        row = await (
            await conn.execute(
                "SELECT pg_advisory_xact_lock(%s), (SELECT count(*) FROM conversations) AS n",
                (_CAPACITY_LOCK,),
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("the database returned no row for a statement that must return one")
        return int(row["n"])

    async def _make_room(self, conn: AsyncConnection[Any], count: int) -> None:
        """Evict the least recently used idle conversations until there is room for one more.
        Caller must hold the capacity lock and pass the count it read under it."""
        while True:
            if count < self._max_conversations:
                return
            evicted = await (
                await conn.execute(
                    "DELETE FROM conversations WHERE id = ("
                    "  SELECT id FROM conversations"
                    "  WHERE lease_expires IS NULL OR lease_expires <= now()"
                    "  ORDER BY last_access LIMIT 1"
                    ") RETURNING id"
                )
            ).fetchone()
            if evicted is None:
                raise CapacityExceededError()
            count -= 1

    async def _messages(
        self, conn: AsyncConnection[Any], conversation_id: str, limit: int | None = None
    ) -> tuple[Message, ...]:
        """Stored messages in order. With a limit, the newest `limit` of them."""
        if limit is None:
            rows = await (
                await conn.execute(
                    "SELECT id, role, content, attachments, created_at FROM messages "
                    "WHERE conversation_id = %s ORDER BY position",
                    (conversation_id,),
                )
            ).fetchall()
        else:
            rows = await (
                await conn.execute(
                    "SELECT id, role, content, attachments, created_at FROM messages "
                    "WHERE conversation_id = %s ORDER BY position DESC LIMIT %s",
                    (conversation_id, limit),
                )
            ).fetchall()
            rows.reverse()
        return tuple(_message(row) for row in rows)

    @staticmethod
    def _snapshot(row: dict[str, Any], messages: tuple[Message, ...]) -> ConversationSnapshot:
        return ConversationSnapshot(
            id=row["id"],
            title=row["title"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            messages=messages,
        )

    # ---- create, read, update, clear ----

    async def create(self, title: str | None = None) -> ConversationSnapshot:
        conversation_id = new_conversation_id()

        async def work(conn: AsyncConnection[Any]) -> ConversationSnapshot:
            await self._sweep(conn)
            await self._make_room(conn, await self._lock_and_count(conn))
            row = await (
                await conn.execute(
                    "INSERT INTO conversations (id, title, version, created_at, updated_at, last_access) "
                    "VALUES (%s, %s, 1, now(), now(), now()) RETURNING id, title, version, created_at, updated_at, lease_token, (lease_expires > now()) AS lease_live",
                    (conversation_id, title),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("the database returned no row for a statement that must return one")
            return self._snapshot(row, ())

        return await self._in_transaction(work)

    async def ensure(self, conversation_id: str) -> ConversationSnapshot:
        async def work(conn: AsyncConnection[Any]) -> ConversationSnapshot:
            await self._sweep(conn)
            row = await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()
            if row is None:
                # Not there yet: create it under the capacity lock. A concurrent creator may have won.
                count = await self._lock_and_count(conn)
                row = await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()
                if row is None:
                    await self._make_room(conn, count)
                    row = await (
                        await conn.execute(
                            "INSERT INTO conversations (id, title, version, created_at, updated_at, last_access) "
                            "VALUES (%s, NULL, 1, now(), now(), now()) RETURNING id, title, version, created_at, updated_at, lease_token, (lease_expires > now()) AS lease_live",
                            (conversation_id,),
                        )
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("the database returned no row for a statement that must return one")
                    return self._snapshot(row, ())
            return self._snapshot(row, await self._messages(conn, conversation_id))

        return await self._in_transaction(work)

    async def get(self, conversation_id: str) -> ConversationSnapshot:
        async def work(conn: AsyncConnection[Any]) -> ConversationSnapshot:
            await self._sweep(conn)
            row = await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()
            if row is None:
                raise ConversationNotFoundError()
            return self._snapshot(row, await self._messages(conn, conversation_id))

        return await self._in_transaction(work)

    async def update_title(
        self, conversation_id: str, title: str | None, expected_version: int
    ) -> ConversationSnapshot:
        async def work(conn: AsyncConnection[Any]) -> ConversationSnapshot:
            await self._sweep(conn)
            # The version is checked and changed in one statement, under the row lock it takes.
            row = await (
                await conn.execute(
                    "UPDATE conversations SET title = %s, version = version + 1, "
                    "updated_at = now(), last_access = now() "
                    "WHERE id = %s AND version = %s "
                    "RETURNING id, title, version, created_at, updated_at, lease_token, (lease_expires > now()) AS lease_live",
                    (title, conversation_id, expected_version),
                )
            ).fetchone()
            if row is None:
                current = await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()
                if current is None:
                    raise ConversationNotFoundError()
                raise VersionConflictError(current["version"])
            return self._snapshot(row, await self._messages(conn, conversation_id))

        return await self._in_transaction(work)

    async def clear(self, conversation_id: str, expected_version: int) -> ConversationSnapshot:
        async def work(conn: AsyncConnection[Any]) -> ConversationSnapshot:
            await self._sweep(conn)
            row = await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()
            if row is None:
                raise ConversationNotFoundError()
            if row["lease_live"]:
                raise ConversationBusyError(_CLEAR_BUSY)
            if row["version"] != expected_version:
                raise VersionConflictError(row["version"])
            await conn.execute("DELETE FROM messages WHERE conversation_id = %s", (conversation_id,))
            row = await (
                await conn.execute(
                    "UPDATE conversations SET version = version + 1, updated_at = now() "
                    "WHERE id = %s RETURNING id, title, version, created_at, updated_at, lease_token, (lease_expires > now()) AS lease_live",
                    (conversation_id,),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("the database returned no row for a statement that must return one")
            return self._snapshot(row, ())

        return await self._in_transaction(work)

    async def ensure_id(self, conversation_id: str) -> str:
        """The conversation's id, created under the given id if it does not exist yet. No messages are read."""

        async def touch(conn: AsyncConnection[Any]) -> bool:
            await self._sweep(conn)
            return (await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()) is not None

        async with self._pool.connection() as conn:
            if await touch(conn):
                return conversation_id

        async def create(conn: AsyncConnection[Any]) -> str:
            # Not there yet: create under the capacity lock. A concurrent creator may have won.
            count = await self._lock_and_count(conn)
            if (await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()) is None:
                await self._make_room(conn, count)
                await conn.execute(
                    "INSERT INTO conversations (id, title, version, created_at, updated_at, last_access) "
                    "VALUES (%s, NULL, 1, now(), now(), now())",
                    (conversation_id,),
                )
            return conversation_id

        return await self._in_transaction(create)

    async def start_turn(
        self, conversation_id: str, lease_seconds: float, history_limit: int
    ) -> tuple[str, list[Message]]:
        """acquire_turn and the newest history_limit messages, in one statement."""
        token = new_id()

        async def work(conn: AsyncConnection[Any]) -> tuple[str, list[Message]]:
            await self._sweep(conn)
            rows = await (
                await conn.execute(
                    "WITH got AS ("
                    "  UPDATE conversations SET lease_token = %s, "
                    "    lease_expires = now() + make_interval(secs => %s), last_access = now() "
                    "  WHERE id = %s AND (lease_expires IS NULL OR lease_expires <= now()) "
                    "  RETURNING id"
                    ") SELECT m.id, m.role, m.content, m.attachments, m.created_at "
                    "FROM got g LEFT JOIN LATERAL ("
                    "  SELECT id, role, content, attachments, created_at, position FROM messages "
                    "  WHERE conversation_id = g.id ORDER BY position DESC LIMIT %s"
                    ") m ON true",
                    (token, float(lease_seconds), conversation_id, history_limit),
                )
            ).fetchall()
            if not rows:
                # No lease was taken: either the conversation is missing, or a turn holds it.
                exists = await (
                    await conn.execute("SELECT 1 FROM conversations WHERE id = %s", (conversation_id,))
                ).fetchone()
                if exists is None:
                    raise ConversationNotFoundError()
                raise ConversationBusyError()
            messages = [_message(row) for row in rows if row["id"] is not None]
            messages.reverse()
            return token, messages

        return await self._autocommit(work)

    async def version(self, conversation_id: str) -> int:
        async def work(conn: AsyncConnection[Any]) -> int:
            await self._sweep(conn)
            row = await (await conn.execute(_TOUCH, (conversation_id,))).fetchone()
            if row is None:
                raise ConversationNotFoundError()
            return int(row["version"])

        return await self._autocommit(work)

    # ---- turns ----

    async def acquire_turn(self, conversation_id: str, lease_seconds: float) -> str:
        """The lease alone. Same rules as start_turn, which does the work."""
        token, _ = await self.start_turn(conversation_id, lease_seconds, history_limit=0)
        return token

    async def release_turn(self, conversation_id: str, token: str) -> None:
        # One statement, so it runs in autocommit with no transaction of its own.
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE conversations SET lease_token = NULL, lease_expires = NULL "
                "WHERE id = %s AND lease_token = %s",
                (conversation_id, token),
            )

    async def commit_turn(self, conversation_id: str, token: str, messages: Sequence[Message]) -> int:
        """Save a finished turn's messages together. Requires the lease. Returns the new version.

        One statement, so it is atomic without a transaction of its own. Within the statement the trim
        sees the table as it was before the insert, so the insert keeps only the newest messages itself
        and the trim removes only older ones."""
        n = len(messages)
        keep_from = n - self._max_messages  # ordinals <= this are beyond the limit and are not inserted
        sql = (
            "WITH upd AS ("
            "  UPDATE conversations SET version = version + 1, updated_at = now(), last_access = now(), "
            "    next_position = next_position + %(n)s "
            "  WHERE id = %(id)s AND lease_token = %(token)s AND lease_expires > now() "
            "  RETURNING version, next_position - %(n)s AS base"
            "), ins AS ("
            "  INSERT INTO messages (conversation_id, position, id, role, content, attachments, created_at) "
            "  SELECT %(id)s, upd.base + m.ord - 1, m.mid, m.role, m.content, m.att::jsonb, m.created "
            "  FROM upd, unnest(%(ids)s::text[], %(roles)s::text[], %(contents)s::text[], "
            "    %(atts)s::text[], %(created)s::timestamptz[]) "
            "    WITH ORDINALITY AS m(mid, role, content, att, created, ord) "
            "  WHERE m.ord > %(keep_from)s "
            "  RETURNING 1"
            "), trimmed AS ("
            "  DELETE FROM messages WHERE conversation_id = %(id)s "
            "  AND position <= (SELECT base + %(n)s - 1 - %(max)s FROM upd) "
            "  RETURNING 1"
            ") SELECT version, base FROM upd"
        )
        params = {
            "n": n,
            "id": conversation_id,
            "token": token,
            "keep_from": keep_from,
            "max": self._max_messages,
            "ids": [m.id for m in messages],
            "roles": [m.role for m in messages],
            "contents": [m.content for m in messages],
            "atts": [json.dumps([a.model_dump(mode="json") for a in m.attachments]) for m in messages],
            "created": [m.created_at for m in messages],
        }
        async with self._pool.connection() as conn:
            row = await (await conn.execute(sql, params)).fetchone()
            if row is None:
                exists = await (
                    await conn.execute("SELECT 1 FROM conversations WHERE id = %s", (conversation_id,))
                ).fetchone()
                if exists is None:
                    raise ConversationNotFoundError()
                raise ConversationBusyError(_LEASE_BUSY)
            return int(row["version"])

    async def history(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        async def work(conn: AsyncConnection[Any]) -> list[Message]:
            await self._sweep(conn)
            # One statement: touch the conversation, then read its newest messages. No row means not found.
            rows = await (
                await conn.execute(
                    "WITH touched AS ("
                    "  UPDATE conversations SET last_access = now() WHERE id = %s RETURNING id"
                    ") SELECT m.id, m.role, m.content, m.attachments, m.created_at "
                    "FROM touched t LEFT JOIN LATERAL ("
                    "  SELECT id, role, content, attachments, created_at, position FROM messages "
                    "  WHERE conversation_id = t.id ORDER BY position DESC LIMIT %s"
                    ") m ON true",
                    (conversation_id, limit),
                )
            ).fetchall()
            if not rows:
                raise ConversationNotFoundError()
            # The query returns newest first; the conversation reads oldest first.
            messages = [_message(row) for row in rows if row["id"] is not None]
            messages.reverse()
            return messages

        return await self._autocommit(work)

    async def count(self) -> int:
        async def work(conn: AsyncConnection[Any]) -> int:
            await self._sweep(conn)
            row = await (await conn.execute("SELECT count(*) AS n FROM conversations")).fetchone()
            if row is None:
                raise RuntimeError("the database returned no row for a statement that must return one")
            return int(row["n"])

        return await self._in_transaction(work)
