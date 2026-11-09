"""The six required endpoints, each checked for every status it documents.

Every status is produced by a real request through the app. Success uses a scripted
test provider; failures come from real causes (bad input, unknown conversation, busy
conversation, provider crash, timeout, no route). Nothing here fabricates a reply.
"""
import asyncio

import httpx
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.schemas.health import HealthReport, ProbeReport
from backend.schemas.models import ModelList
from backend.schemas.response import ChatOptions, ChatResponse, ChatStatus
from backend.tests._bodies import parse_error_body
from backend.tests.fakes import app_with, crashing, replying, timing_out

CHAT_ENDPOINTS = ["/api/chat", "/api/research", "/api/code", "/api/write"]
MODE_FOR = {"/api/chat": "general", "/api/research": "research", "/api/code": "coding", "/api/write": "writing"}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _run(coro):
    return asyncio.run(coro)


# ---- chat-style endpoints: each documented status -------------------------------------

@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_200_completed_with_the_agreed_response_format(path: str) -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.post(path, json={"message": "hello"})

    response = _run(scenario())
    assert response.status_code == 200
    reply = ChatResponse.model_validate_json(response.text)
    assert reply.status is ChatStatus.SUCCESS
    assert reply.content == "answer"
    assert reply.mode == MODE_FOR[path]
    assert reply.error is None


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_422_for_invalid_input_uses_the_error_envelope(path: str) -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.post(path, json={"message": "   "})

    response = _run(scenario())
    assert response.status_code == 422
    envelope = parse_error_body(response.text)
    assert envelope.error.code.value == "VALIDATION_ERROR"
    assert envelope.error.details[0].type == "empty_message"


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_an_unknown_conversation_id_is_created_on_chat_endpoints(path: str) -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.post(path, json={"message": "hi", "conversation_id": "no-such-conversation"})

    response = _run(scenario())
    assert response.status_code == 200
    assert response.json()["conversation_id"] == "no-such-conversation"


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_409_while_the_conversation_is_busy(path: str) -> None:
    async def scenario():
        app = app_with(replying("answer"))
        async with app.router.lifespan_context(app):
            store = app.state.conversation_store
            conversation = await store.create()
            await store.acquire_turn(conversation.id, 60)  # an earlier turn holds the lease
            async with _client(app) as c:
                return await c.post(path, json={"message": "hi", "conversation_id": conversation.id})

    response = _run(scenario())
    assert response.status_code == 409
    assert parse_error_body(response.text).error.code.value == "CONVERSATION_BUSY"


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_502_when_the_provider_fails(path: str) -> None:
    async def scenario():
        async with _client(app_with(crashing())) as c:
            return await c.post(path, json={"message": "hi"})

    response = _run(scenario())
    assert response.status_code == 502
    reply = ChatResponse.model_validate_json(response.text)
    assert reply.status is ChatStatus.ERROR and reply.content is None
    assert reply.error.code.value == "GENERATION_FAILED"
    assert "unexpected failure" not in response.text


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_504_when_generation_takes_too_long(path: str) -> None:
    async def scenario():
        async with _client(app_with(timing_out(), generation_timeout_seconds=1)) as c:
            return await c.post(path, json={"message": "hi"})

    response = _run(scenario())
    assert response.status_code == 504
    assert ChatResponse.model_validate_json(response.text).error.code.value == "GENERATION_TIMEOUT"


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_503_when_no_provider_serves_the_model(path: str) -> None:
    async def scenario():
        async with _client(create_app(Settings())) as c:
            return await c.post(path, json={"message": "hi"})

    response = _run(scenario())
    assert response.status_code == 503
    reply = ChatResponse.model_validate_json(response.text)
    assert reply.error.code.value == "MODEL_UNAVAILABLE"
    assert reply.model == "rapid"  # a missing model resolves to rapid


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_405_for_the_wrong_method(path: str) -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.get(path)

    response = _run(scenario())
    assert response.status_code == 405
    assert parse_error_body(response.text).error.code.value == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_401_when_the_access_key_is_required_and_missing(path: str) -> None:
    from backend.config import Secret

    async def scenario():
        async with _client(app_with(replying("answer"), api_access_key=Secret("matrix-key-123"))) as c:
            return await c.post(path, json={"message": "hi"})

    response = _run(scenario())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# ---- GET /api/models -------------------------------------------------------------------

def test_models_200_lists_every_model_with_availability() -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.get("/api/models")

    response = _run(scenario())
    assert response.status_code == 200
    models = ModelList.model_validate_json(response.text)
    assert [m.id for m in models.models] == ["brainstormer", "mythical", "sonet", "rapid"]
    assert all(m.available for m in models.models)


def test_models_200_reports_unavailable_models_without_a_route() -> None:
    async def scenario():
        async with _client(create_app(Settings())) as c:
            return await c.get("/api/models")

    models = ModelList.model_validate_json(_run(scenario()).text)
    assert not any(m.available for m in models.models)


# ---- GET /health -------------------------------------------------------------------------

def test_health_200_ok_when_running_with_routes() -> None:
    async def scenario():
        app = app_with(replying("answer"))
        async with app.router.lifespan_context(app):
            async with _client(app) as c:
                return await c.get("/health")

    response = _run(scenario())
    assert response.status_code == 200
    report = HealthReport.model_validate_json(response.text)
    assert report.status == "ok"
    assert report.checks == {"server": "ok", "models": "ok", "conversations": "ok"}


def test_health_200_degraded_when_no_model_has_a_route() -> None:
    async def scenario():
        app = create_app(Settings())
        async with app.router.lifespan_context(app):
            async with _client(app) as c:
                return await c.get("/health")

    response = _run(scenario())
    assert response.status_code == 200
    assert HealthReport.model_validate_json(response.text).status == "degraded"


def test_health_503_down_before_the_app_starts() -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.get("/health")

    response = _run(scenario())
    assert response.status_code == 503
    assert HealthReport.model_validate_json(response.text).status == "down"


def test_health_probes_are_always_reachable_and_safe() -> None:
    async def scenario():
        app = app_with(replying("answer"))
        async with app.router.lifespan_context(app):
            async with _client(app) as c:
                return await c.get("/health/live"), await c.get("/health/ready")

    live, ready = _run(scenario())
    assert live.status_code == 200 and ProbeReport.model_validate_json(live.text).status == "ok"
    assert ready.status_code == 200 and ProbeReport.model_validate_json(ready.text).status == "ready"


# ---- the options endpoint that the chat endpoints depend on -----------------------------

def test_options_200_is_the_agreed_shape() -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.get("/api/chat/options")

    options = ChatOptions.model_validate_json(_run(scenario()).text)
    assert options.default_model == "rapid" and options.default_mode == "general"


# ---- request format: every POST endpoint refuses a non-JSON body ------------------------

@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_415_for_a_non_json_body(path: str) -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.post(path, content=b"hello", headers={"Content-Type": "text/plain"})

    response = _run(scenario())
    assert response.status_code == 415
    assert parse_error_body(response.text).error.code.value == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8"])
def test_json_content_type_with_or_without_charset_is_accepted(content_type: str) -> None:
    async def scenario():
        async with _client(app_with(replying("answer"))) as c:
            return await c.post("/api/chat", content=b'{"message": "hi"}', headers={"Content-Type": content_type})

    assert _run(scenario()).status_code == 200
