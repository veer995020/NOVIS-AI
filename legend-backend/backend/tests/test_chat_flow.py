"""End-to-end chat flow: validation, model routing, processing, and honest outcomes."""
import asyncio
import json

from fastapi.testclient import TestClient

from backend.config import DEFAULT_MODELS, DEFAULT_MODES, Settings
from backend.schemas.chat import ChatRequest
from backend.schemas.options import configure_allowed_options
from backend.services.chat import ChatService
from backend.services.conversation_store import ConversationStore
from backend.tests.fakes import (
    client_with,
    crashing,
    empty,
    failing_with_provider_error,
    replying,
    router_with,
    timing_out,
    word_by_word,
)


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def _send(client: TestClient, **body):
    payload = {"message": "hi", "model": "rapid"}
    payload.update(body)
    return client.post("/api/chat", json=payload)


def test_success_returns_200_completed_with_selected_mode() -> None:
    response = _send(client_with(replying("hello there")), mode="research")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["content"] == "hello there"
    assert body["mode"] == "research"
    assert body["error"] is None


def test_context_carries_prior_turns_and_selected_mode() -> None:
    provider = replying("ok")
    client = client_with(provider)
    first = _send(client, message="first").json()
    _send(client, conversation_id=first["conversation_id"], message="second", mode="code")

    messages = provider.calls[1]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "second"
    assert "Mode: code" in messages[0]["content"]


def test_timeout_returns_504_with_error_status() -> None:
    response = _send(client_with(timing_out(), generation_timeout_seconds=1))
    assert response.status_code == 504
    body = response.json()
    assert body["status"] == "error"
    assert body["content"] == ""
    assert body["error"]["code"] == "generation_timeout"


def test_provider_error_returns_502_and_hides_detail() -> None:
    response = _send(client_with(failing_with_provider_error()))
    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "generation_failed"
    assert "rejected the request" not in response.text


def test_unexpected_crash_returns_502_and_hides_secret_detail() -> None:
    response = _send(client_with(crashing()))
    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert "internal-detail-99" not in response.text


def test_empty_reply_is_an_error_not_a_success() -> None:
    response = _send(client_with(empty()))
    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "generation_failed"


def test_failed_turn_is_dropped_from_later_context() -> None:
    attempts = {"n": 0}
    provider = replying("recovered")

    async def flaky(messages, cancel):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("boom")
        yield "recovered"

    provider._script = flaky  # the first call fails, the second succeeds
    client = client_with(provider)
    failed = _send(client, message="unanswered question")
    assert failed.status_code == 502
    recovered = _send(client, conversation_id=failed.json()["conversation_id"], message="second try")
    assert recovered.status_code == 200
    assert [(m["role"], m["content"]) for m in provider.calls[1][1:]] == [("user", "second try")]


def test_stream_reports_failure_inside_done_event() -> None:
    client = client_with(crashing())
    with client.stream("POST", "/api/chat/stream", json={"message": "hi", "model": "rapid"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    frames = [f for f in text.split("\n\n") if f.strip()]
    final = json.loads(frames[-1].split("data: ", 1)[1])
    assert final["type"] == "done"
    assert final["response"]["status"] == "error"
    assert "internal-detail-99" not in text


def test_missing_model_uses_rapid_route() -> None:
    provider = replying("ok")
    client = client_with(provider)
    body = client.post("/api/chat", json={"message": "hi"}).json()
    assert body["model"] == "rapid"


def test_disconnect_before_reply_drops_the_unanswered_turn() -> None:
    async def scenario() -> list:
        store = ConversationStore(max_messages=50, max_conversations=50)
        service = ChatService(store, _settings(), router_with(replying("x")))
        conversation = await store.create()
        turn = await service.begin(
            ChatRequest(message="pending", model="rapid", conversation_id=conversation.id)
        )
        events = service.run(turn)
        await events.__anext__()  # start event
        await events.aclose()  # client went away
        return await store.history(conversation.id)

    assert asyncio.run(scenario()) == []


def test_concurrent_requests_on_separate_conversations_stay_isolated() -> None:
    configure_allowed_options(DEFAULT_MODELS, DEFAULT_MODES)

    async def scenario():
        store = ConversationStore(max_messages=50, max_conversations=50)
        service = ChatService(store, _settings(), router_with(word_by_word("answer")))
        conversations = [await store.create() for _ in range(5)]
        requests = [
            ChatRequest(message=f"message {i}", model="rapid", conversation_id=c.id)
            for i, c in enumerate(conversations)
        ]
        responses = await asyncio.gather(*(service.complete(r) for r in requests))
        return conversations, responses

    conversations, responses = asyncio.run(scenario())
    for conversation, response in zip(conversations, responses, strict=True):
        assert response.status.value == "completed"
        assert response.conversation_id == conversation.id
        assert response.content.strip() == "answer"
