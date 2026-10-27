"""Regression tests for the ordered idle-expiry scan."""
import asyncio

from backend.services.conversation_store import ConversationStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_expiry_drops_only_idle_conversations_and_stops_at_the_first_fresh_one() -> None:
    async def scenario():
        clock = Clock()
        store = ConversationStore(max_messages=10, max_conversations=10, idle_seconds=100, clock=clock)
        old = [await store.create() for _ in range(3)]
        clock.now += 60
        fresh = await store.create()
        clock.now += 60  # old ones are now 120s idle, the fresh one 60s
        return await store.count(), old, fresh, store

    count, old, fresh, store = asyncio.run(scenario())
    assert count == 1
    assert asyncio.run(store.get(fresh.id)).id == fresh.id


def test_a_read_moves_a_conversation_out_of_the_expiry_path() -> None:
    async def scenario():
        clock = Clock()
        store = ConversationStore(max_messages=10, max_conversations=10, idle_seconds=100, clock=clock)
        first = await store.create()
        second = await store.create()
        clock.now += 90
        await store.get(first.id)  # refresh the older one
        clock.now += 20  # second is now 110s idle, first is 20s
        return await store.count(), first, second, store

    count, first, second, store = asyncio.run(scenario())
    assert count == 1
    assert asyncio.run(store.get(first.id)).id == first.id


def test_a_busy_conversation_is_kept_even_when_its_idle_time_has_passed() -> None:
    async def scenario():
        clock = Clock()
        store = ConversationStore(max_messages=10, max_conversations=10, idle_seconds=100, clock=clock)
        busy = await store.create()
        await store.create()  # idle, will expire
        token = await store.acquire_turn(busy.id, 500)
        clock.now += 200
        while_leased = await store.count()
        await store.release_turn(busy.id, token)
        after_release = await store.count()  # now idle past the limit, so it goes
        return while_leased, after_release

    while_leased, after_release = asyncio.run(scenario())
    assert while_leased == 1  # the busy one survives expiry
    assert after_release == 0  # and expires once it is released and idle
