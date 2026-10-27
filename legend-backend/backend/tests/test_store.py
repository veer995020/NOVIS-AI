"""Conversation store: lifecycle, versioning, turn leases, and memory limits.
A fake clock makes time-based rules deterministic."""
import asyncio
from typing import Any

import pytest

from backend.schemas.chat import Message
from backend.services.conversation_store import ConversationStore
from backend.services.errors import (
    CapacityExceededError,
    ConversationBusyError,
    ConversationNotFoundError,
    VersionConflictError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(clock: FakeClock | None = None, **overrides) -> ConversationStore:
    options: dict[str, Any] = {"max_messages": 50, "max_conversations": 50, "idle_seconds": 600.0}
    options.update(overrides)
    return ConversationStore(clock=clock or FakeClock(), **options)


def _run(coro):
    return asyncio.run(coro)


# ---- create / read ----------------------------------------------------------------

def test_create_returns_fresh_id_and_version_one() -> None:
    async def scenario():
        store = _store()
        first = await store.create(title="Plan")
        second = await store.create()
        return first, second

    first, second = _run(scenario())
    assert first.version == 1
    assert first.title == "Plan"
    assert first.id != second.id
    assert len(first.id) >= 20


def test_read_unknown_conversation_is_not_found() -> None:
    async def scenario():
        await _store().get("does-not-exist")

    with pytest.raises(ConversationNotFoundError):
        _run(scenario())


# ---- update and clear: versioned -----------------------------------------------------

def test_update_title_bumps_version_and_checks_it() -> None:
    async def scenario():
        store = _store()
        created = await store.create()
        updated = await store.update_title(created.id, "Renamed", expected_version=1)
        assert updated.version == 2
        with pytest.raises(VersionConflictError) as info:
            await store.update_title(created.id, "Stale edit", expected_version=1)
        assert info.value.current_version == 2
        return (await store.get(created.id)).title

    assert _run(scenario()) == "Renamed"


def test_clear_removes_messages_but_keeps_the_conversation() -> None:
    async def scenario():
        store = _store()
        created = await store.create(title="keep me")
        token = await store.acquire_turn(created.id, 30)
        await store.commit_turn(created.id, token, [Message(role="user", content="hi")])
        await store.release_turn(created.id, token)
        cleared = await store.clear(created.id, expected_version=2)
        return cleared

    cleared = _run(scenario())
    assert cleared.messages == ()
    assert cleared.title == "keep me"
    assert cleared.version == 3  # create (1), commit (2), clear (3); lease changes do not bump


def test_clear_with_stale_version_is_refused() -> None:
    async def scenario():
        store = _store()
        created = await store.create()
        with pytest.raises(VersionConflictError):
            await store.clear(created.id, expected_version=99)

    _run(scenario())


# ---- turn leases: no shared-state corruption -----------------------------------------

def test_only_one_turn_can_hold_a_conversation() -> None:
    async def scenario():
        store = _store()
        created = await store.create()
        await store.acquire_turn(created.id, 30)
        with pytest.raises(ConversationBusyError):
            await store.acquire_turn(created.id, 30)
        with pytest.raises(ConversationBusyError):
            await store.clear(created.id, expected_version=2)

    _run(scenario())


def test_concurrent_acquires_have_exactly_one_winner() -> None:
    async def scenario():
        store = _store()
        created = await store.create()
        results = await asyncio.gather(
            *(store.acquire_turn(created.id, 30) for _ in range(20)),
            return_exceptions=True,
        )
        winners = [r for r in results if isinstance(r, str)]
        losers = [r for r in results if isinstance(r, ConversationBusyError)]
        return len(winners), len(losers)

    assert _run(scenario()) == (1, 19)


def test_expired_lease_cannot_write_into_a_new_holders_conversation() -> None:
    async def scenario():
        clock = FakeClock()
        store = _store(clock)
        created = await store.create()
        old_token = await store.acquire_turn(created.id, 30)
        clock.advance(31)  # the first turn's lease has run out
        new_token = await store.acquire_turn(created.id, 30)
        with pytest.raises(ConversationBusyError):
            await store.commit_turn(created.id, old_token, [Message(role="assistant", content="late")])
        await store.commit_turn(created.id, new_token, [Message(role="user", content="fresh")])
        return [m.content for m in await store.history(created.id)]

    assert _run(scenario()) == ["fresh"]


def test_release_only_works_for_the_current_holder() -> None:
    async def scenario():
        clock = FakeClock()
        store = _store(clock)
        created = await store.create()
        stale = await store.acquire_turn(created.id, 30)
        clock.advance(31)
        current = await store.acquire_turn(created.id, 30)
        await store.release_turn(created.id, stale)  # must not free the current holder
        with pytest.raises(ConversationBusyError):
            await store.acquire_turn(created.id, 30)
        await store.release_turn(created.id, current)
        await store.acquire_turn(created.id, 30)

    _run(scenario())


def test_an_uncommitted_turn_saves_nothing() -> None:
    async def scenario():
        store = _store()
        created = await store.create()
        token = await store.acquire_turn(created.id, 30)
        await store.release_turn(created.id, token)  # abandoned: no commit
        return await store.history(created.id)

    assert _run(scenario()) == []


def test_commit_saves_question_and_answer_together() -> None:
    async def scenario():
        store = _store()
        created = await store.create()
        token = await store.acquire_turn(created.id, 30)
        await store.commit_turn(
            created.id,
            token,
            [Message(role="user", content="q"), Message(role="assistant", content="a")],
        )
        return [m.role for m in await store.history(created.id)]

    assert _run(scenario()) == ["user", "assistant"]


# ---- memory limits ---------------------------------------------------------------------

def test_each_conversation_keeps_only_the_newest_messages() -> None:
    async def scenario():
        store = _store(max_messages=2)
        created = await store.create()
        token = await store.acquire_turn(created.id, 30)
        for text in ("a", "b", "c"):
            await store.commit_turn(created.id, token, [Message(role="user", content=text)])
        return [m.content for m in await store.history(created.id)]

    assert _run(scenario()) == ["b", "c"]


def test_idle_conversations_are_dropped() -> None:
    async def scenario():
        clock = FakeClock()
        store = _store(clock, idle_seconds=60)
        created = await store.create()
        clock.advance(61)
        with pytest.raises(ConversationNotFoundError):
            await store.get(created.id)

    _run(scenario())


def test_busy_conversations_are_never_dropped_as_idle() -> None:
    async def scenario():
        clock = FakeClock()
        store = _store(clock, idle_seconds=60)
        created = await store.create()
        await store.acquire_turn(created.id, 300)
        clock.advance(120)
        return (await store.get(created.id)).id == created.id

    assert _run(scenario()) is True


def test_least_recently_used_idle_conversation_is_evicted_at_capacity() -> None:
    async def scenario():
        store = _store(max_conversations=2)
        oldest = await store.create()
        newer = await store.create()
        await store.get(oldest.id)  # touch: now `newer` is least recently used
        await store.create()
        with pytest.raises(ConversationNotFoundError):
            await store.get(newer.id)
        return (await store.get(oldest.id)).id == oldest.id

    assert _run(scenario()) is True


def test_capacity_is_refused_when_every_conversation_is_busy() -> None:
    async def scenario():
        store = _store(max_conversations=1)
        only = await store.create()
        await store.acquire_turn(only.id, 300)
        with pytest.raises(CapacityExceededError):
            await store.create()

    _run(scenario())
