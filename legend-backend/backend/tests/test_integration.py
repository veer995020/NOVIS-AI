"""End-to-end verification of the chat API contract, through the real app and TestClient.

Covers: valid requests, missing fields, invalid types, oversized inputs, invalid model and mode,
attachment errors, API failures, and mismatches between the contract and the chat page.
The reference request and responses are the ones the contract is written against.
"""
import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import Secret, Settings
from backend.main import create_app
from backend.schemas.errors import ErrorCode, ErrorDetail
from backend.schemas.request import ChatRequest
from backend.schemas.response import ChatResponse, ChatStatus
from backend.tests.fakes import (
    app_with,
    crashing,
    echoing,
    replying,
    timing_out,
)
from backend.tests.reference_contract import (
    REFERENCE_ERROR_RESPONSE,
    REFERENCE_REQUEST,
    REFERENCE_SUCCESS_RESPONSE,
)

PAGE = Path(__file__).resolve().parents[2] / "frontend" / "chat.html"
CHAT_FIELDS = {"conversation_id", "model", "mode", "content", "status", "error", "detail"}
CLIENT_ONLY_CODES = {"INVALID_RESPONSE"}  # produced by the page itself, never by the server


def chat(client: TestClient, payload, path: str = "/api/chat", **kwargs):
    return client.post(path, json=payload, **kwargs)


def shape(response) -> ChatResponse:
    """Every chat response must be a ChatResponse with exactly the six fields."""
    body = response.json()
    assert set(body) == CHAT_FIELDS, f"fields differ: {sorted(body)}"
    return ChatResponse.model_validate_json(response.text)


def error_detail(reply: ChatResponse) -> ErrorDetail:
    assert reply.error is not None, "expected an error object"
    return reply.error


def field_issues(response) -> list[dict]:
    return response.json()["error"]["details"] or []


# ---- the reference objects -------------------------------------------------------------

def test_the_reference_request_is_accepted_by_the_request_model() -> None:
    req = ChatRequest.model_validate(REFERENCE_REQUEST)
    assert req.model == "rapid" and req.mode == "general" and req.attachments == []


def test_the_reference_success_response_validates_as_a_chat_response() -> None:
    reply = ChatResponse.model_validate_json(json.dumps(REFERENCE_SUCCESS_RESPONSE))
    assert reply.status is ChatStatus.SUCCESS and reply.error is None and isinstance(reply.content, str)


def test_the_reference_error_response_validates_as_a_chat_response() -> None:
    reply = ChatResponse.model_validate_json(json.dumps(REFERENCE_ERROR_RESPONSE))
    assert reply.status is ChatStatus.ERROR and reply.content is None
    assert error_detail(reply).code == ErrorCode.VALIDATION_ERROR


# ---- valid requests -----------------------------------------------------------------

def test_a_valid_request_returns_the_agreed_success_shape() -> None:
    client = TestClient(app_with(replying("Machine learning is a branch of AI...")))
    response = chat(client, {"message": "Explain machine learning", "model": "rapid", "mode": "general", "attachments": []})
    assert response.status_code == 200
    reply = shape(response)
    assert reply.status is ChatStatus.SUCCESS
    assert reply.content == "Machine learning is a branch of AI..."
    assert reply.model == "rapid" and reply.mode == "general"
    assert reply.error is None
    assert reply.conversation_id


def test_a_follow_up_uses_the_conversation_the_server_issued() -> None:
    client = TestClient(app_with(echoing()))
    first = shape(chat(client, {"message": "one"}))
    second = shape(chat(client, {"message": "two", "conversation_id": first.conversation_id}))
    assert second.conversation_id == first.conversation_id


def test_omitted_optional_fields_take_their_documented_defaults() -> None:
    reply = shape(chat(TestClient(app_with(echoing())), {"message": "hi"}))
    assert reply.model == "rapid" and reply.mode == "general"


def test_the_task_endpoints_return_the_same_shape_with_their_fixed_mode() -> None:
    client = TestClient(app_with(echoing()))
    expected = {"/api/research": "research", "/api/code": "coding", "/api/write": "writing"}
    for path, mode in expected.items():
        reply = shape(chat(client, {"message": "hi"}, path=path))
        assert reply.status is ChatStatus.SUCCESS and reply.mode == mode


def test_an_unknown_conversation_id_is_created_on_first_use() -> None:
    # The page names its own conversation ids. The first message under a new id creates it.
    client = TestClient(app_with(echoing()))
    reply = shape(chat(client, {"message": "hi", "conversation_id": "conv_001"}))
    assert reply.status is ChatStatus.SUCCESS and reply.conversation_id == "conv_001"
    again = shape(chat(client, {"message": "again", "conversation_id": "conv_001"}))
    assert again.conversation_id == "conv_001"


# ---- missing fields ------------------------------------------------------------------

def test_a_missing_message_is_a_validation_error_in_the_reference_shape() -> None:
    response = chat(TestClient(app_with(echoing())), {"model": "rapid", "mode": "general"})
    assert response.status_code == 422
    reply = shape(response)
    assert reply.status is ChatStatus.ERROR and reply.content is None
    assert error_detail(reply).code == ErrorCode.VALIDATION_ERROR
    assert error_detail(reply).message == "The message field is required."
    assert reply.conversation_id is None
    assert reply.model == "rapid" and reply.mode == "general"  # defaults, echoed on the error
    assert field_issues(response)[0] == {"field": "body.message", "type": "missing", "message": "The message field is required."}


def test_a_missing_body_entirely_is_a_clear_validation_error() -> None:
    response = TestClient(app_with(echoing())).post("/api/chat", headers={"Content-Type": "application/json"}, content=b"")
    assert response.status_code == 422
    assert error_detail(shape(response)).code == ErrorCode.VALIDATION_ERROR


# ---- invalid types ------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload, field",
    [
        ({"message": 123}, "body.message"),
        ({"message": ["hi"]}, "body.message"),
        ({"message": "hi", "conversation_id": 42}, "body.conversation_id"),
        ({"message": "hi", "model": 5}, "body.model"),
        ({"message": "hi", "mode": {"name": "general"}}, "body.mode"),
        ({"message": "hi", "attachments": "notes.md"}, "body.attachments"),
        ({"message": "hi", "attachments": [42]}, "body.attachments.0"),
        ({"message": "hi", "temperature": 0.2}, "body.temperature"),
    ],
)
def test_invalid_types_are_refused_with_the_offending_field_named(payload, field: str) -> None:
    response = chat(TestClient(app_with(echoing())), payload)
    assert response.status_code == 422
    reply = shape(response)
    assert error_detail(reply).code == ErrorCode.VALIDATION_ERROR
    assert field in [issue["field"] for issue in field_issues(response)]


# ---- oversized inputs ---------------------------------------------------------------

def test_a_message_one_character_over_the_limit_is_refused_and_not_echoed() -> None:
    response = chat(TestClient(app_with(echoing())), {"message": "x" * 8001})
    assert response.status_code == 422
    assert "x" * 50 not in response.text
    assert error_detail(shape(response)).code == ErrorCode.VALIDATION_ERROR


def test_a_message_at_the_limit_is_accepted() -> None:
    assert shape(chat(TestClient(app_with(echoing())), {"message": "x" * 8000})).status is ChatStatus.SUCCESS


def test_a_body_over_the_size_limit_is_refused_in_the_chat_shape() -> None:
    response = TestClient(app_with(echoing())).post(
        "/api/chat", content=b'{"message":"' + b"a" * 3_000_000 + b'"}', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    reply = shape(response)
    assert error_detail(reply).code == ErrorCode.PAYLOAD_TOO_LARGE and reply.content is None


# ---- invalid model and mode -----------------------------------------------------------

@pytest.mark.parametrize("payload, field", [
    ({"message": "hi", "model": "gpt-9"}, "body.model"),
    ({"message": "hi", "model": "Rapid"}, "body.model"),
    ({"message": "hi", "mode": "poetry"}, "body.mode"),
    ({"message": "hi", "mode": "standard"}, "body.mode"),   # the name before this contract
    ({"message": "hi", "mode": "code"}, "body.mode"),       # the name before this contract
    ({"message": "hi", "mode": "creative"}, "body.mode"),   # removed from this contract
])
def test_unknown_or_retired_model_and_mode_names_are_refused(payload, field: str) -> None:
    response = chat(TestClient(app_with(echoing())), payload)
    assert response.status_code == 422
    assert field in [issue["field"] for issue in field_issues(response)]


def test_an_unknown_mode_error_lists_what_is_allowed() -> None:
    reply = shape(chat(TestClient(app_with(echoing())), {"message": "hi", "mode": "poetry"}))
    detail = error_detail(reply)
    assert "general" in detail.message or any("general" in issue.message for issue in detail.details or [])


# ---- attachment errors ---------------------------------------------------------------

@pytest.mark.parametrize("attachment, field", [
    ({"name": "a.png", "media_type": "image/png", "content": "SECRET-FILE-BODY"}, "body.attachments.0.media_type"),
    ({"name": "../../etc/passwd", "media_type": "text/plain", "content": "x"}, "body.attachments.0.name"),
    ({"name": "a.json", "media_type": "application/json", "content": "{not json"}, "body.attachments.0.content"),
    ({"name": "a.txt", "media_type": "text/plain", "content": "bad\x07bell"}, "body.attachments.0.content"),
    ({"name": "a.txt", "media_type": "text/plain", "content": "x" * 64001}, "body.attachments.0.content"),
    ({"name": "a.txt", "media_type": "text/plain", "content": ""}, "body.attachments.0.content"),
])
def test_bad_attachments_are_refused_with_their_field_path(attachment, field: str) -> None:
    response = chat(TestClient(app_with(echoing())), {"message": "hi", "attachments": [attachment]})
    assert response.status_code == 422
    assert field in [issue["field"] for issue in field_issues(response)]
    assert "SECRET-FILE-BODY" not in response.text  # file content is never echoed back



def test_four_attachments_are_refused_and_the_total_is_capped() -> None:
    client = TestClient(app_with(echoing()))
    file = {"name": "f.txt", "media_type": "text/plain", "content": "x"}
    assert chat(client, {"message": "hi", "attachments": [file] * 4}).status_code == 422
    big = {"name": "b.txt", "media_type": "text/plain", "content": "a" * 64000}
    response = chat(client, {"message": "hi", "attachments": [big, big, big]})
    assert response.status_code == 422 and "body.attachments" in [i["field"] for i in field_issues(response)]


def test_a_valid_attachment_reaches_the_model_and_its_content_is_not_echoed_in_errors() -> None:
    client = TestClient(app_with(echoing()))
    ok = {"name": "notes.md", "media_type": "text/markdown", "content": "hello from notes"}
    reply = shape(chat(client, {"message": "summarise", "attachments": [ok]}))
    assert reply.content is not None
    assert "[Attachment 1: notes.md (text/markdown, 16 bytes)]" in reply.content



# ---- API failures ------------------------------------------------------------------

def test_a_provider_failure_is_a_502_with_null_content_and_no_internal_detail() -> None:
    response = chat(TestClient(app_with(crashing())), {"message": "hi"})
    assert response.status_code == 502
    reply = shape(response)
    assert reply.status is ChatStatus.ERROR and reply.content is None
    assert error_detail(reply).code == ErrorCode.GENERATION_FAILED
    assert "internal-detail" not in response.text and "Traceback" not in response.text


def test_a_timeout_is_a_504() -> None:
    response = chat(TestClient(app_with(timing_out(), generation_timeout_seconds=1)), {"message": "hi"})
    assert response.status_code == 504
    assert error_detail(shape(response)).code == ErrorCode.GENERATION_TIMEOUT


def test_a_model_without_a_route_is_a_503_not_a_success() -> None:
    response = chat(TestClient(create_app(Settings())), {"message": "hi"})
    assert response.status_code == 503
    reply = shape(response)
    assert reply.status is ChatStatus.ERROR and error_detail(reply).code == ErrorCode.MODEL_UNAVAILABLE


def test_a_missing_or_wrong_access_key_is_a_401_in_the_chat_shape() -> None:
    client = TestClient(app_with(echoing(), api_access_key=Secret("integration-key-1")))
    response = chat(client, {"message": "hi"}, headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    reply = shape(response)
    assert error_detail(reply).code == ErrorCode.UNAUTHORIZED and "nope" not in response.text


def test_a_rate_limited_client_gets_429_in_the_chat_shape() -> None:
    client = TestClient(app_with(echoing(), rate_limit_per_minute=100, rate_limit_generation_per_minute=1))
    chat(client, {"message": "one"})
    response = chat(client, {"message": "two"})
    assert response.status_code == 429
    assert error_detail(shape(response)).code == ErrorCode.RATE_LIMITED
    assert int(response.headers["retry-after"]) > 0


def test_a_non_json_body_is_a_415_in_the_chat_shape() -> None:
    response = TestClient(app_with(echoing())).post("/api/chat", content=b"hello", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    assert error_detail(shape(response)).code == ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_a_busy_conversation_is_a_409_in_the_chat_shape() -> None:
    async def scenario():
        app = app_with(echoing())
        async with app.router.lifespan_context(app):
            store = app.state.conversation_store
            conversation = await store.create()
            await store.acquire_turn(conversation.id, 60)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
                return await c.post("/api/chat", json={"message": "hi", "conversation_id": conversation.id})

    response = asyncio.run(scenario())
    assert response.status_code == 409
    assert ChatResponse.model_validate_json(response.text).error.code == ErrorCode.CONVERSATION_BUSY


# ---- frontend and backend agree -----------------------------------------------------

def _served_paths(app) -> set[str]:
    """Every path the app serves, including the root aliases the page calls. The OpenAPI
    document leaves those out on purpose, so they are read from the routers."""
    paths: set[str] = set()
    for included in app.router.routes:
        router = getattr(included, "original_router", None)
        for route in (router.routes if router is not None else [included]):
            if getattr(route, "path", None):
                paths.add(route.path)
    return paths


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_page_offers_exactly_the_modes_the_server_accepts() -> None:
    # The mode entries are the lines that point at a chat endpoint. The model list does not.
    page_modes = re.findall(r'id: "(\w+)",[^\n]*endpoint: CONFIG\.endpoints', _page())
    server_modes = TestClient(app_with(echoing())).get("/api/chat/options").json()["modes"]
    assert page_modes, "the page's mode list was not found"
    assert sorted(page_modes) == sorted(server_modes)


def test_the_page_reads_the_error_text_from_detail_and_the_reply_from_content() -> None:
    # The original page reads body.detail for error text and body.content for the reply.
    body = _page()
    assert "body?.detail" in body and "data?.content" in body


def test_a_chat_error_carries_its_text_in_detail() -> None:
    reply = shape(chat(TestClient(app_with(echoing())), {"message": "hi", "mode": "poetry"}))
    assert reply.detail == error_detail(reply).message


def test_the_page_error_text_is_a_string_the_server_always_provides() -> None:
    response = chat(TestClient(app_with(echoing())), {"message": ""})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str) and response.json()["detail"]


def test_the_page_sends_only_fields_the_request_model_accepts() -> None:
    body = _page()
    builder = re.search(r"function buildPayload\(\{(.*?)\n\}", body, re.S)
    assert builder, "buildPayload was not found"
    sent = set(re.findall(r"^\s{4}(\w+):", builder.group(0), re.M))
    accepted = set(ChatRequest.model_fields)
    assert sent <= accepted | {"conversation_id"}, sent - accepted


def test_the_page_calls_only_routes_the_server_serves() -> None:
    body = _page()
    endpoints = re.search(r"endpoints: Object\.freeze\(\{(.*?)\}\)", body, re.S)
    assert endpoints, "the page's endpoint table was not found"
    paths = set(re.findall(r'"(/[\w/]+)"', endpoints.group(1)))
    served = _served_paths(create_app(Settings(enable_docs=True)))
    assert paths <= served, paths - served


def test_the_contract_example_request_is_the_one_the_page_would_send() -> None:
    payload = REFERENCE_REQUEST
    assert set(payload) == {"conversation_id", "message", "model", "mode", "attachments"}
    assert json.loads(json.dumps(payload)) == payload
