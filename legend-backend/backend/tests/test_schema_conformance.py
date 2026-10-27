"""Every real response is parsed with the strict contract models, from the JSON text
a client would receive. A field the contract does not describe fails here."""
import asyncio
import json

import httpx
from pydantic import TypeAdapter

from backend.config import Settings
from backend.main import create_app
from backend.schemas.chat import ChatOptions, ChatResponse, ChatStatus
from backend.schemas.conversations import Conversation
from backend.schemas.errors import ErrorEnvelope
from backend.schemas.events import StreamEvent
from backend.schemas.health import HealthReport, ProbeReport
from backend.schemas.models import ModelList
from backend.tests.fakes import app_with, crashing, replying

_EVENT_ADAPTER = TypeAdapter(StreamEvent)


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _run(coro):
    return asyncio.run(coro)


def test_success_chat_response_matches_contract() -> None:
    async def scenario():
        async with _client(app_with(replying("hello"))) as c:
            return await c.post("/api/chat", json={"message": "hi"})

    response = _run(scenario())
    parsed = ChatResponse.model_validate_json(response.text)
    assert parsed.status is ChatStatus.COMPLETED


def test_every_error_body_matches_its_documented_shape() -> None:
    """Request-level errors are ErrorEnvelope. Chat outcomes are ChatResponse with status error."""

    async def scenarios():
        async with _client(app_with(crashing())) as c:
            return [
                (await c.post("/api/chat", json={"message": "hi", "model": "gpt-9"}), "envelope"),
                (await c.post("/api/chat", json={"message": "hi"}), "chat"),
                (await c.get("/api/chat"), "envelope"),
                (await c.get("/api/missing"), "envelope"),
                (await c.post("/api/conversations/bad%20id/clear", json={"expected_version": 1}), "envelope"),
            ]

    for response, shape in _run(scenarios()):
        assert response.status_code >= 400
        if shape == "envelope":
            ErrorEnvelope.model_validate_json(response.text)
        else:
            assert ChatResponse.model_validate_json(response.text).status is ChatStatus.ERROR


def test_chat_stream_frames_match_stream_event_schema() -> None:
    async def scenario():
        async with _client(app_with(replying("streamed"))) as c:
            async with c.stream("POST", "/api/chat/stream", json={"message": "hi"}) as response:
                return [line async for line in response.aiter_lines()]

    lines = _run(scenario())
    payloads = [line[len("data: "):] for line in lines if line.startswith("data: ")]
    assert payloads, "the stream produced no events"
    for payload in payloads:
        _EVENT_ADAPTER.validate_json(payload)
    assert json.loads(payloads[-1])["type"] == "done"
    done_response = json.dumps(json.loads(payloads[-1])["response"])
    assert ChatResponse.model_validate_json(done_response).status is ChatStatus.COMPLETED


def test_models_health_and_options_match_their_contracts() -> None:
    async def scenario():
        app = app_with(replying("ok"))
        async with app.router.lifespan_context(app):
            async with _client(app) as c:
                return (
                    await c.get("/api/models"),
                    await c.get("/health"),
                    await c.get("/health/live"),
                    await c.get("/health/ready"),
                    await c.get("/api/chat/options"),
                )

    models, health, live, ready, options = _run(scenario())
    ModelList.model_validate_json(models.text)
    HealthReport.model_validate_json(health.text)
    ProbeReport.model_validate_json(live.text)
    ProbeReport.model_validate_json(ready.text)
    ChatOptions.model_validate_json(options.text)


def test_conversation_body_matches_contract() -> None:
    async def scenario():
        async with _client(app_with(replying("ok"))) as c:
            created = await c.post("/api/conversations", json={"title": "t"})
            cid = created.json()["id"]
            await c.post("/api/chat", json={"conversation_id": cid, "message": "q"})
            return created, await c.get(f"/api/conversations/{cid}")

    created, read = _run(scenario())
    Conversation.model_validate_json(created.text)
    parsed = Conversation.model_validate_json(read.text)
    assert [m.role for m in parsed.messages] == ["user", "assistant"]


def test_unrouted_default_app_responses_match_contract() -> None:
    async def scenario():
        async with _client(create_app(Settings())) as c:
            return await c.post("/api/chat", json={"message": "hi"})

    response = _run(scenario())
    ChatResponse.model_validate_json(response.text)
    assert ChatResponse.model_validate_json(response.text).status is ChatStatus.ERROR
