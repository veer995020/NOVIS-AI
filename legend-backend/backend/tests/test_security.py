"""Audit regression tests. Each test pins one finding from AUDIT.md."""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message

from backend.config import Settings
from backend.middleware import RequestContextMiddleware
from backend.schemas.chat import ChatRequest
from backend.services.chat import ChatService
from backend.services.conversation_store import ConversationStore
from backend.services.errors import CapacityExceededError
from backend.tests.fakes import app_with, replying, router_with, word_by_word
from backend.utils.redaction import safe_path

ORIGIN = "https://app.example.com"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# ---- CORS -----------------------------------------------------------------------

def test_cors_preflight_allows_patch_for_configured_origin() -> None:
    client = TestClient(app_with(replying("ok"), cors_origins=(ORIGIN,)))
    response = client.options(
        "/api/conversations/abc",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "PATCH"},
    )
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_cors_rejects_methods_the_api_does_not_use() -> None:
    client = TestClient(app_with(replying("ok"), cors_origins=(ORIGIN,)))
    response = client.options(
        "/api/chat",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "DELETE"},
    )
    assert "DELETE" not in response.headers.get("access-control-allow-methods", "")


def test_cors_never_answers_an_unlisted_origin() -> None:
    client = TestClient(app_with(replying("ok"), cors_origins=(ORIGIN,)))
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


# ---- input limits: body size cannot be bypassed ----------------------------------

def test_chunked_body_over_the_limit_is_rejected_before_the_app_runs() -> None:
    client = TestClient(app_with(replying("ok"), max_request_bytes=1024))
    chunks = (b"x" * 600 for _ in range(4))  # no Content-Length: sent chunked
    response = client.post("/api/chat", content=chunks, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_chunked_body_under_the_limit_still_works() -> None:
    client = TestClient(app_with(replying("ok"), max_request_bytes=4096))
    chunks = iter([b'{"message": "hel', b'lo", "model": "rapid"}'])
    response = client.post("/api/chat", content=chunks, headers={"Content-Type": "application/json"})
    assert response.status_code == 200


def test_declared_oversize_is_rejected_without_reading_the_body() -> None:
    client = TestClient(app_with(replying("ok"), max_request_bytes=1024))
    response = client.post("/api/chat", content=b"x" * 2048, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_deeply_nested_json_is_a_validation_error_not_a_crash() -> None:
    client = TestClient(app_with(replying("ok")))
    response = client.post("/api/chat", content=b"[" * 20000, headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_stop_rejects_ids_that_are_not_request_ids() -> None:
    client = TestClient(app_with(replying("ok")))
    assert client.post("/api/chat/..%2f..%2fetc/stop").status_code in (404, 422)
    assert client.post("/api/chat/not-hex/stop").status_code == 422


# ---- error leakage ----------------------------------------------------------------

def test_unexpected_errors_return_no_traceback_or_internal_text() -> None:
    app = app_with(replying("ok"))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("db password is hunter2 at /srv/internal")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert "/srv/internal" not in response.text
    assert "Traceback" not in response.text


def test_validation_errors_do_not_echo_submitted_values() -> None:
    client = TestClient(app_with(replying("ok")))
    response = client.post("/api/chat", json={"message": "   ", "model": "rapid", "secret_field": "hunter2"})
    assert response.status_code == 422
    assert "hunter2" not in response.text


def test_health_and_models_do_not_reveal_routing_or_provider_names() -> None:
    client = TestClient(app_with(replying("ok")))
    for path in ("/health", "/health/live", "/health/ready", "/api/models", "/api/chat/options"):
        body = client.get(path).text
        assert "fake" not in body, path
        assert "fake-model" not in body, path


def test_settings_repr_has_no_credentials() -> None:
    assert "password" not in repr(Settings()).lower()


# ---- secrets -----------------------------------------------------------------------

def test_server_software_is_not_advertised() -> None:
    async def leaky_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"server", b"uvicorn/0.99"), (b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestContextMiddleware(leaky_app, max_request_bytes=1024)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/x", "headers": []}
    asyncio.run(middleware(scope, receive, send))
    headers = dict(sent[0]["headers"])
    assert b"server" not in headers


# ---- logging: conversation and request ids are never logged -----------------------

def test_safe_path_replaces_bearer_style_ids() -> None:
    assert safe_path("/api/conversations/abc123") == "/api/conversations/{id}"
    assert safe_path("/api/conversations/abc123/clear") == "/api/conversations/{id}/clear"
    assert safe_path("/api/chat/" + "a" * 32 + "/stop") == "/api/chat/{request_id}/stop"
    assert safe_path("/api/chat") == "/api/chat"


def test_request_log_never_contains_a_conversation_id() -> None:
    client = TestClient(app_with(replying("ok")))
    conversation = client.post("/api/conversations", json={}).json()
    capture = _Capture()
    logging.getLogger("backend.request").addHandler(capture)
    try:
        client.get(f"/api/conversations/{conversation['id']}")
        client.patch(
            f"/api/conversations/{conversation['id']}",
            json={"title": "t", "expected_version": 1},
        )
    finally:
        logging.getLogger("backend.request").removeHandler(capture)
    paths = [getattr(r, "path", "") for r in capture.records]
    assert paths and all(conversation["id"] not in p for p in paths)
    assert "/api/conversations/{id}" in paths


# ---- resource limits: concurrent replies are capped ---------------------------------

def test_turn_cap_refuses_extra_concurrent_replies() -> None:
    async def scenario() -> None:
        store = ConversationStore(max_messages=50, max_conversations=50)
        service = ChatService(
            store, Settings(max_concurrent_turns=1), router_with(word_by_word("a b"))
        )
        first = await service.begin(ChatRequest(message="one", model="rapid"))
        with pytest.raises(CapacityExceededError):
            await service.begin(ChatRequest(message="two", model="rapid"))
        async for _ in service.run(first):
            pass
        # The slot is free again once the first turn finishes.
        await service.begin(ChatRequest(message="three", model="rapid"))

    asyncio.run(scenario())


def test_turn_cap_is_enforced_over_http_as_503() -> None:
    app = app_with(replying("ok"), max_concurrent_turns=1)
    service = app.state.chat_service

    async def hold_one_slot() -> None:
        await service.begin(ChatRequest(message="held", model="rapid"))

    asyncio.run(hold_one_slot())  # the slot stays held after this loop ends
    response = TestClient(app).post("/api/chat", json={"message": "blocked"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_exceeded"
