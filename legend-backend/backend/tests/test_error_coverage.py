"""Every error code is produced by a real request through the real app.

The scenario table must cover the whole ErrorCode enum. Adding a code without a
scenario fails the test, so no error code can exist untested.
"""
import asyncio

import httpx
import pytest
from fastapi import HTTPException

from backend.config import Secret, Settings
from backend.main import create_app
from backend.schemas.errors import ErrorCode
from backend.schemas.response import ChatResponse, ChatStatus
from backend.tests._bodies import parse_error_body
from backend.tests.fakes import app_with, crashing, replying, sleeping, timing_out

MAX_BODY = 1024


def _async_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _scenario_validation_error():
    async with _async_client(app_with(replying("ok"))) as c:
        return await c.post("/api/chat", json={"message": "hi", "model": "gpt-9"})


async def _scenario_not_found():
    async with _async_client(app_with(replying("ok"))) as c:
        return await c.get("/api/does-not-exist")


async def _scenario_method_not_allowed():
    async with _async_client(app_with(replying("ok"))) as c:
        return await c.get("/api/chat")


async def _scenario_payload_too_large():
    async with _async_client(app_with(replying("ok"), max_request_bytes=MAX_BODY)) as c:
        return await c.post("/api/chat", content=b"x" * (MAX_BODY * 2), headers={"Content-Type": "application/json"})


async def _scenario_generation_failed():
    async with _async_client(app_with(crashing())) as c:
        return await c.post("/api/chat", json={"message": "hi"})


async def _scenario_generation_timeout():
    async with _async_client(app_with(timing_out(), generation_timeout_seconds=1)) as c:
        return await c.post("/api/chat", json={"message": "hi"})


async def _scenario_model_unavailable():
    async with _async_client(create_app(Settings())) as c:
        return await c.post("/api/chat", json={"message": "hi"})


async def _scenario_conversation_not_found():
    async with _async_client(app_with(replying("ok"))) as c:
        return await c.get("/api/conversations/no-such-conversation")


async def _scenario_version_conflict():
    async with _async_client(app_with(replying("ok"))) as c:
        cid = (await c.post("/api/conversations", json={})).json()["id"]
        return await c.patch(f"/api/conversations/{cid}", json={"title": "x", "expected_version": 99})


async def _scenario_conversation_busy():
    app = app_with(sleeping("reply", 0.5))
    async with _async_client(app) as c:
        cid = (await c.post("/api/conversations", json={})).json()["id"]
        slow = asyncio.create_task(c.post("/api/chat", json={"conversation_id": cid, "message": "first"}))
        await asyncio.sleep(0.05)
        second = await c.post("/api/chat", json={"conversation_id": cid, "message": "second"})
        await slow
        return second


async def _scenario_capacity_exceeded():
    app = app_with(sleeping("reply", 0.5), max_concurrent_turns=1)
    async with _async_client(app) as c:
        slow = asyncio.create_task(c.post("/api/chat", json={"message": "first"}))
        await asyncio.sleep(0.05)
        second = await c.post("/api/chat", json={"message": "second"})
        await slow
        return second


async def _scenario_internal_error():
    app = app_with(replying("ok"))

    @app.get("/qa-internal-error")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
        return await c.get("/qa-internal-error")


async def _scenario_http_error():
    app = app_with(replying("ok"))

    @app.get("/qa-teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="short and stout")

    async with _async_client(app) as c:
        return await c.get("/qa-teapot")


# Each scenario names the body shape the contract promises for that outcome.
ENVELOPE = "envelope"  # ErrorEnvelope: the request could not be processed at all
CHAT = "chat"          # ChatResponse with status "error": a chat outcome without a reply

async def _scenario_bad_request_host():
    app = app_with(replying("ok"), allowed_hosts=("api.example.com",))
    async with _async_client(app) as c:
        return await c.get("/api/models", headers={"Host": "evil.example"})


async def _scenario_unauthorized():
    app = app_with(replying("ok"), api_access_key=Secret("qa-access-key-123"))
    async with _async_client(app) as c:
        return await c.post("/api/chat", json={"message": "hi"})


async def _scenario_rate_limited():
    app = app_with(replying("ok"), rate_limit_per_minute=1)
    async with _async_client(app) as c:
        await c.get("/api/models")
        return await c.get("/api/models")


async def _scenario_unsupported_media_type():
    async with _async_client(app_with(replying("ok"))) as c:
        return await c.post("/api/chat", content=b"hello", headers={"Content-Type": "text/plain"})


async def _scenario_forbidden_origin():
    app = app_with(replying("ok"), cors_origins=("https://app.example.com",))
    async with _async_client(app) as c:
        return await c.options(
            "/api/chat",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )


SCENARIOS = {
    ErrorCode.VALIDATION_ERROR: (_scenario_validation_error, 422, ENVELOPE),
    ErrorCode.NOT_FOUND: (_scenario_not_found, 404, ENVELOPE),
    ErrorCode.METHOD_NOT_ALLOWED: (_scenario_method_not_allowed, 405, ENVELOPE),
    ErrorCode.PAYLOAD_TOO_LARGE: (_scenario_payload_too_large, 413, ENVELOPE),
    ErrorCode.GENERATION_FAILED: (_scenario_generation_failed, 502, CHAT),
    ErrorCode.GENERATION_TIMEOUT: (_scenario_generation_timeout, 504, CHAT),
    ErrorCode.MODEL_UNAVAILABLE: (_scenario_model_unavailable, 503, CHAT),
    ErrorCode.CONVERSATION_NOT_FOUND: (_scenario_conversation_not_found, 404, ENVELOPE),
    ErrorCode.VERSION_CONFLICT: (_scenario_version_conflict, 409, ENVELOPE),
    ErrorCode.CONVERSATION_BUSY: (_scenario_conversation_busy, 409, ENVELOPE),
    ErrorCode.CAPACITY_EXCEEDED: (_scenario_capacity_exceeded, 503, ENVELOPE),
    ErrorCode.INTERNAL_ERROR: (_scenario_internal_error, 500, ENVELOPE),
    ErrorCode.HTTP_ERROR: (_scenario_http_error, 418, ENVELOPE),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: (_scenario_unsupported_media_type, 415, ENVELOPE),
    ErrorCode.BAD_REQUEST: (_scenario_bad_request_host, 400, ENVELOPE),
    ErrorCode.FORBIDDEN: (_scenario_forbidden_origin, 403, ENVELOPE),
    ErrorCode.UNAUTHORIZED: (_scenario_unauthorized, 401, ENVELOPE),
    ErrorCode.RATE_LIMITED: (_scenario_rate_limited, 429, ENVELOPE),
}


def test_scenario_table_covers_every_error_code() -> None:
    assert set(SCENARIOS) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode), ids=lambda c: c.value)
def test_error_code_is_produced_by_a_real_request(code: ErrorCode) -> None:
    scenario, expected_status, shape = SCENARIOS[code]
    response = asyncio.run(scenario())
    assert response.status_code == expected_status
    assert "Traceback" not in response.text
    assert "internal detail that must not leak" not in response.text

    if shape == ENVELOPE:
        envelope = parse_error_body(response.text)
        assert envelope.error.code.value == code.value
        assert response.headers.get("x-request-id")
    else:
        reply = ChatResponse.model_validate_json(response.text)
        assert reply.status is ChatStatus.ERROR
        assert reply.error is not None and reply.error.code.value == code.value
        assert reply.content is None
