import asyncio

import pytest
from pydantic import ValidationError

from backend.config import DEFAULT_MODELS, DEFAULT_MODES, Settings
from backend.schemas.events import StreamDoneEvent
from backend.schemas.options import configure_allowed_options
from backend.schemas.request import ChatRequest
from backend.schemas.response import ChatStatus
from backend.services.chat import ChatService
from backend.services.conversation_store import ConversationStore
from backend.services.errors import ConversationBusyError
from backend.tests.fakes import router_with, word_by_word


@pytest.fixture(autouse=True)
def _default_options() -> None:
    configure_allowed_options(DEFAULT_MODELS, DEFAULT_MODES)


def _service(script=None) -> tuple[ChatService, ConversationStore]:
    store = ConversationStore(max_messages=50, max_conversations=50)
    provider = script or word_by_word("one two three four five six")
    return ChatService(store, Settings(), router_with(provider)), store


def test_schema_rejects_unknown_model_at_construction() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", model="unknown-model")


def test_stop_cancels_stream_and_reports_stopped() -> None:
    async def scenario() -> list[dict]:
        service, _ = _service()
        req = ChatRequest(message="go", model="rapid")
        events: list[dict] = []
        turn = await service.begin(req)
        async for event in service.stream_turn(turn):
            events.append(event)
            if event["type"] == "start":
                assert service.stop(event["request_id"]) is True
        return events

    events = asyncio.run(scenario())
    final = events[-1]
    assert final["type"] == "done"
    assert final["response"]["status"] == "stopped"
    assert not any(e["type"] == "delta" for e in events)


def test_complete_stores_both_turns_in_the_named_conversation() -> None:
    async def scenario() -> None:
        service, store = _service()
        conversation = await store.create()
        req = ChatRequest(message="alpha beta", model="rapid", conversation_id=conversation.id)
        response = await service.complete(req)
        assert response.status is ChatStatus.SUCCESS
        assert response.error is None
        assert response.conversation_id == conversation.id
        history = await store.history(conversation.id)
        assert [m.role for m in history] == ["user", "assistant"]

    asyncio.run(scenario())


def test_missing_conversation_id_creates_one() -> None:
    async def scenario() -> None:
        service, store = _service()
        response = await service.complete(ChatRequest(message="hello", model="rapid"))
        assert response.conversation_id is not None
        assert (await store.get(response.conversation_id)).id == response.conversation_id

    asyncio.run(scenario())


def test_a_second_turn_on_a_busy_conversation_is_refused() -> None:
    async def scenario() -> None:
        service, store = _service()
        conversation = await store.create()
        req = ChatRequest(message="first", model="rapid", conversation_id=conversation.id)
        first = await service.begin(req)  # holds the lease
        with pytest.raises(ConversationBusyError):
            await service.begin(ChatRequest(message="second", model="rapid", conversation_id=conversation.id))
        # Finish the first turn; the conversation is free again.
        async for _ in service.run(first):
            pass
        await service.complete(ChatRequest(message="third", model="rapid", conversation_id=conversation.id))

    asyncio.run(scenario())


def test_reply_is_not_saved_when_the_lease_was_lost() -> None:
    async def scenario() -> None:
        clock = {"now": 1000.0}
        store = ConversationStore(
            max_messages=50, max_conversations=50, idle_seconds=10**6, clock=lambda: clock["now"]
        )
        service = ChatService(store, Settings(generation_timeout_seconds=1), router_with(word_by_word("a b c")))
        conversation = await store.create()
        turn = await service.begin(ChatRequest(message="go", model="rapid", conversation_id=conversation.id))
        clock["now"] += 10_000  # the lease expires...
        await store.acquire_turn(conversation.id, 300)  # ...and another turn takes over
        events = [e async for e in service.run(turn)]
        done = events[-1]
        assert isinstance(done, StreamDoneEvent)
        assert done.response.status is ChatStatus.ERROR
        assert done.response.error is not None
        assert done.response.error.code.value == "CONVERSATION_BUSY"
        assert done.response.content is None
        assert [m.role for m in await store.history(conversation.id)] == []

    asyncio.run(scenario())


def test_conversation_dropped_mid_turn_is_reported_not_raised() -> None:
    async def scenario() -> None:
        clock = {"now": 1000.0}
        store = ConversationStore(
            max_messages=50, max_conversations=50, idle_seconds=60, clock=lambda: clock["now"]
        )
        service = ChatService(store, Settings(), router_with(word_by_word("a b c")))
        conversation = await store.create()
        turn = await service.begin(ChatRequest(message="go", model="rapid", conversation_id=conversation.id))
        clock["now"] += 10_000  # idle past the limit: the store drops it
        events = [e async for e in service.run(turn)]
        done = events[-1]
        assert isinstance(done, StreamDoneEvent)
        assert done.response.status is ChatStatus.ERROR
        assert done.response.error is not None
        assert done.response.error.code.value == "CONVERSATION_NOT_FOUND"

    asyncio.run(scenario())
