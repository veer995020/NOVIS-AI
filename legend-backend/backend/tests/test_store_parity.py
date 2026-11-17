"""The in-memory and PostgreSQL conversation stores must behave identically.

Every scenario runs against both. PostgreSQL scenarios need TEST_DATABASE_URL (a database that may be
reset). Without it they are skipped, and the skip says so. Nothing here reads the production database.
"""
import asyncio
import os

import pytest

from backend.schemas.attachments import AttachmentMeta
from backend.schemas.response import Message
from backend.services.conversation_store import ConversationStore
from backend.services.errors import (
    CapacityExceededError,
    ConversationBusyError,
    ConversationNotFoundError,
    VersionConflictError,
)

TEST_DSN = os.environ.get("TEST_DATABASE_URL")


def _dsn() -> str:
    """Only called from tests that the skip marker keeps away from a missing database."""
    assert TEST_DSN is not None
    return TEST_DSN
requires_db = pytest.mark.skipif(TEST_DSN is None, reason="TEST_DATABASE_URL not set; PostgreSQL parity skipped")
KINDS = [
    "memory",
    pytest.param("postgres", marks=requires_db),
]


def _reset_database() -> None:
    import psycopg

    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS messages, conversations CASCADE")


def _make(kind: str, *, max_messages: int = 200, max_conversations: int = 1000, idle_seconds: float = 3600.0):
    if kind == "memory":
        return ConversationStore(
            max_messages=max_messages, max_conversations=max_conversations, idle_seconds=idle_seconds
        )
    from backend.services.postgres_store import PostgresConversationStore

    return PostgresConversationStore(
        dsn=_dsn(), max_messages=max_messages, max_conversations=max_conversations, idle_seconds=idle_seconds
    )


def run(kind: str, scenario, **options) -> None:
    async def main() -> None:
        if kind == "postgres":
            _reset_database()
        store = _make(kind, **options)
        await store.open()
        try:
            await scenario(store)
        finally:
            await store.close()

    asyncio.run(main())


def user(text: str, attachments: list[AttachmentMeta] | None = None) -> Message:
    return Message(role="user", content=text, attachments=attachments or [])


def assistant(text: str) -> Message:
    return Message(role="assistant", content=text)


@pytest.mark.parametrize("kind", KINDS)
def test_create_then_get_is_empty_at_version_one(kind: str) -> None:
    async def scenario(store):
        created = await store.create(title="Plans")
        got = await store.get(created.id)
        assert got.title == "Plans" and got.version == 1 and got.messages == ()

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_ensure_creates_once_and_then_returns_the_same_conversation(kind: str) -> None:
    async def scenario(store):
        first = await store.ensure("page-made-1")
        again = await store.ensure("page-made-1")
        assert first.id == again.id == "page-made-1" and again.version == 1

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_conversation_is_not_found(kind: str) -> None:
    async def scenario(store):
        with pytest.raises(ConversationNotFoundError):
            await store.get("nothing-here")

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_a_committed_turn_is_saved_in_order_and_bumps_the_version(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        await store.commit_turn(conv.id, token, [user("question"), assistant("answer")])
        await store.release_turn(conv.id, token)
        history = await store.history(conv.id)
        assert [(m.role, m.content) for m in history] == [("user", "question"), ("assistant", "answer")]
        assert (await store.get(conv.id)).version == 2

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_only_the_lease_holder_can_commit(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        with pytest.raises(ConversationBusyError):
            await store.commit_turn(conv.id, "not-the-token", [user("sneaky")])
        await store.release_turn(conv.id, token)
        with pytest.raises(ConversationBusyError):
            await store.commit_turn(conv.id, token, [user("after release")])
        assert await store.history(conv.id) == []

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_turn_cannot_start_while_one_is_running(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        with pytest.raises(ConversationBusyError):
            await store.acquire_turn(conv.id, 30)
        await store.release_turn(conv.id, token)
        await store.acquire_turn(conv.id, 30)  # free again

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_a_lease_that_expires_stops_being_held(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 0.5)
        await asyncio.sleep(0.8)
        with pytest.raises(ConversationBusyError):
            await store.commit_turn(conv.id, token, [user("too late")])
        await store.acquire_turn(conv.id, 30)  # the expired lease no longer blocks

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_title_update_refuses_a_stale_version(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        updated = await store.update_title(conv.id, "New title", expected_version=1)
        assert updated.title == "New title" and updated.version == 2
        with pytest.raises(VersionConflictError) as info:
            await store.update_title(conv.id, "Stale", expected_version=1)
        assert info.value.current_version == 2

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_clear_empties_the_conversation_but_not_while_busy(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        await store.commit_turn(conv.id, token, [user("hi")])
        with pytest.raises(ConversationBusyError):
            await store.clear(conv.id, expected_version=2)
        await store.release_turn(conv.id, token)
        cleared = await store.clear(conv.id, expected_version=2)
        assert cleared.messages == () and cleared.version == 3
        with pytest.raises(VersionConflictError):
            await store.clear(conv.id, expected_version=2)

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_only_the_newest_messages_are_kept(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        for turn in range(3):
            token = await store.acquire_turn(conv.id, 30)
            await store.commit_turn(conv.id, token, [user(f"q{turn}"), assistant(f"a{turn}")])
            await store.release_turn(conv.id, token)
        history = await store.history(conv.id)
        assert [m.content for m in history] == ["q1", "a1", "q2", "a2"]

    run(kind, scenario, max_messages=4)


@pytest.mark.parametrize("kind", KINDS)
def test_attachment_metadata_survives_a_round_trip(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        meta = AttachmentMeta(name="notes.md", media_type="text/markdown", size_bytes=42)
        await store.commit_turn(conv.id, token, [user("with a file", [meta])])
        await store.release_turn(conv.id, token)
        (message,) = await store.history(conv.id)
        assert message.attachments == [meta]

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_creation_evicts_the_least_recent_idle_conversation(kind: str) -> None:
    async def scenario(store):
        first = await store.create()
        second = await store.create()
        await store.get(first.id)  # first is now the more recently used
        third = await store.create()
        assert await store.count() == 2
        with pytest.raises(ConversationNotFoundError):
            await store.get(second.id)
        assert (await store.get(first.id)).id == first.id
        assert (await store.get(third.id)).id == third.id

    run(kind, scenario, max_conversations=2)


@pytest.mark.parametrize("kind", KINDS)
def test_creation_refuses_when_every_conversation_is_busy(kind: str) -> None:
    async def scenario(store):
        first = await store.create()
        second = await store.create()
        await store.acquire_turn(first.id, 30)
        await store.acquire_turn(second.id, 30)
        with pytest.raises(CapacityExceededError):
            await store.create()

    run(kind, scenario, max_conversations=2)


@pytest.mark.parametrize("kind", KINDS)
def test_idle_conversations_expire_but_busy_ones_do_not(kind: str) -> None:
    async def scenario(store):
        idle = await store.create()
        busy = await store.create()
        token = await store.acquire_turn(busy.id, 30)
        await asyncio.sleep(1.3)
        assert await store.count() == 1
        with pytest.raises(ConversationNotFoundError):
            await store.get(idle.id)
        await store.release_turn(busy.id, token)

    run(kind, scenario, idle_seconds=1.0)


@pytest.mark.parametrize("kind", KINDS)
def test_a_limited_history_is_the_newest_messages_in_order(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        for turn in range(4):
            token = await store.acquire_turn(conv.id, 30)
            await store.commit_turn(conv.id, token, [user(f"q{turn}"), assistant(f"a{turn}")])
            await store.release_turn(conv.id, token)
        assert [m.content for m in await store.history(conv.id, limit=3)] == ["a2", "q3", "a3"]
        assert await store.history(conv.id, limit=0) == []
        assert len(await store.history(conv.id)) == 8

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_commit_returns_the_version_it_produced_and_version_reads_it(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        new_version = await store.commit_turn(conv.id, token, [user("hi"), assistant("hello")])
        await store.release_turn(conv.id, token)
        assert new_version == 2
        assert await store.version(conv.id) == 2
        with pytest.raises(ConversationNotFoundError):
            await store.version("nothing-here")

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_the_position_order_survives_trimming_across_many_turns(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        for turn in range(25):
            token = await store.acquire_turn(conv.id, 30)
            await store.commit_turn(conv.id, token, [user(f"u{turn}"), assistant(f"a{turn}")])
            await store.release_turn(conv.id, token)
        history = await store.history(conv.id)
        assert [m.content for m in history] == [f"{role}{turn}" for turn in range(20, 25) for role in ("u", "a")]

    run(kind, scenario, max_messages=10)


@pytest.mark.parametrize("kind", KINDS)
def test_one_turn_longer_than_the_limit_keeps_only_its_newest_messages(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        await store.commit_turn(conv.id, token, [user("first"), assistant("second"), user("third")])
        await store.release_turn(conv.id, token)
        assert [m.content for m in await store.history(conv.id)] == ["second", "third"]
        assert [m.content for m in await store.history(conv.id, limit=1)] == ["third"]

    run(kind, scenario, max_messages=2)


@pytest.mark.parametrize("kind", KINDS)
def test_ensure_id_creates_once_and_returns_the_id(kind: str) -> None:
    async def scenario(store):
        assert await store.ensure_id("named-1") == "named-1"
        assert await store.ensure_id("named-1") == "named-1"
        assert await store.count() == 1

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_start_turn_takes_the_lease_and_returns_the_newest_context(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token = await store.acquire_turn(conv.id, 30)
        await store.commit_turn(conv.id, token, [user("q0"), assistant("a0"), user("q1"), assistant("a1")])
        await store.release_turn(conv.id, token)
        new_token, context = await store.start_turn(conv.id, 30, history_limit=2)
        assert [m.content for m in context] == ["q1", "a1"]
        with pytest.raises(ConversationBusyError):
            await store.start_turn(conv.id, 30, history_limit=2)
        await store.commit_turn(conv.id, new_token, [user("q2"), assistant("a2")])
        await store.release_turn(conv.id, new_token)
        assert (await store.get(conv.id)).version == 3

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_start_turn_on_a_missing_conversation_is_not_found(kind: str) -> None:
    async def scenario(store):
        with pytest.raises(ConversationNotFoundError):
            await store.start_turn("nothing-here", 30, history_limit=5)

    run(kind, scenario)


@pytest.mark.parametrize("kind", KINDS)
def test_start_turn_with_no_history_returns_no_context_and_still_takes_the_lease(kind: str) -> None:
    async def scenario(store):
        conv = await store.create()
        token, context = await store.start_turn(conv.id, 30, history_limit=5)
        assert context == []
        await store.release_turn(conv.id, token)

    run(kind, scenario)
