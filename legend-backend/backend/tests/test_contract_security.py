"""Security audit of the API contract. The server enforces every rule here on its own.

Each test sends raw requests, the way an attacker would, so no frontend check can help. Limits
are read from the published contract, so the tests enforce what the contract says, not a copy.
Rate limits are off in this file unless a test turns them on, so counting does not interfere.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.config import (
    DEFAULT_MODELS,
    DEFAULT_MODES,
    ConfigError,
    Secret,
    Settings,
    validate_settings,
)
from backend.main import create_app
from backend.schemas.contract import build_contract
from backend.schemas.errors import ErrorCode
from backend.schemas.response import ChatResponse
from backend.tests._bodies import parse_error_body
from backend.tests.fakes import (
    ScriptedProvider,
    app_with,
    echoing,
)

LIMITS = build_contract()["limits"]
ALLOWED_ORIGIN = "https://app.example.com"
KEY = "audit-key-do-not-leak-7731"


def client(**overrides) -> TestClient:
    overrides.setdefault("rate_limit_per_minute", 0)
    overrides.setdefault("rate_limit_generation_per_minute", 0)
    return TestClient(app_with(echoing(), **overrides), raise_server_exceptions=False)


def attachment(name="notes.md", media_type="text/markdown", content="# notes") -> dict:
    return {"name": name, "media_type": media_type, "content": content}


def fields(response) -> list[tuple[str, str]]:
    return [(issue["field"], issue["type"]) for issue in (response.json()["error"].get("details") or [])]


# ---- A. input-size limits, from the contract ------------------------------------------------

def test_message_length_limit_is_exact() -> None:
    c = client()
    limit = LIMITS["message_max_chars"]
    assert c.post("/api/chat", json={"message": "a" * limit}).status_code == 200
    rejected = c.post("/api/chat", json={"message": "a" * (limit + 1)})
    assert rejected.status_code == 422
    assert fields(rejected) == [("body.message", "string_too_long")]


def test_conversation_id_length_limit_is_exact() -> None:
    c = client()
    limit = LIMITS["conversation_id_max_length"]
    assert c.post("/api/chat", json={"message": "x", "conversation_id": "a" * limit}).status_code == 200
    assert c.post("/api/chat", json={"message": "x", "conversation_id": "a" * (limit + 1)}).status_code == 422


def test_conversation_title_limit_is_exact() -> None:
    c = client()
    assert c.post("/api/conversations", json={"title": "t" * 80}).status_code == 201
    assert c.post("/api/conversations", json={"title": "t" * 81}).status_code == 422


def test_model_and_mode_name_length_limit_is_exact() -> None:
    c = client()
    limit = LIMITS["name_max_length"]
    assert c.post("/api/chat", json={"message": "x", "model": "a" * (limit + 1)}).status_code == 422


def test_attachment_count_limit_is_exact() -> None:
    c = client()
    count = LIMITS["attachments_max_count"]
    assert c.post("/api/chat", json={"message": "x", "attachments": [attachment()] * count}).status_code == 200
    over = c.post("/api/chat", json={"message": "x", "attachments": [attachment()] * (count + 1)})
    assert over.status_code == 422 and fields(over) == [("body.attachments", "too_long")]


def test_attachment_content_limit_is_exact() -> None:
    c = client()
    limit = LIMITS["attachment_max_chars"]
    assert c.post("/api/chat", json={"message": "x", "attachments": [attachment(content="a" * limit)]}).status_code == 200
    over = c.post("/api/chat", json={"message": "x", "attachments": [attachment(content="a" * (limit + 1))]})
    assert over.status_code == 422 and fields(over) == [("body.attachments.0.content", "string_too_long")]


def test_attachment_total_limit_is_exact() -> None:
    c = client()
    total = LIMITS["attachments_max_total_chars"]
    per = LIMITS["attachment_max_chars"]
    # three files, each within the per-file limit, together one character over the total
    files = [attachment(content="a" * per), attachment(content="a" * per), attachment(content="a" * (total - 2 * per + 1))]
    over = c.post("/api/chat", json={"message": "x", "attachments": files})
    assert over.status_code == 422 and fields(over) == [("body.attachments", "value_error")]


def test_attachment_name_length_limit_is_exact() -> None:
    c = client()
    limit = LIMITS["attachment_name_max_length"]
    assert c.post("/api/chat", json={"message": "x", "attachments": [attachment(name="n" * limit)]}).status_code == 200
    assert c.post("/api/chat", json={"message": "x", "attachments": [attachment(name="n" * (limit + 1))]}).status_code == 422


def test_conversation_title_refuses_unsafe_text() -> None:
    c = client()
    assert c.post("/api/conversations", json={"title": "bad\x00title"}).status_code == 422


def test_request_body_limit_is_enforced_on_received_bytes() -> None:
    c = client(max_request_bytes=2048)
    assert c.post("/api/chat", json={"message": "x" * 100}).status_code == 200
    big = json.dumps({"message": "x" * 4000}).encode()
    for headers in ({"Content-Type": "application/json"}, {"Content-Type": "application/json", "Transfer-Encoding": "chunked"}):
        response = c.post("/api/chat", content=iter([big]) if "Transfer-Encoding" in headers else big, headers=headers)
        assert response.status_code == 413, headers
        assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ---- B. attachment type and size ------------------------------------------------------------

@pytest.mark.parametrize("media_type", LIMITS["attachment_media_types"])
def test_every_published_media_type_is_accepted(media_type: str) -> None:
    content = '{"ok": true}' if media_type == "application/json" else "text"
    assert client().post("/api/chat", json={"message": "x", "attachments": [attachment(media_type=media_type, content=content)]}).status_code == 200


@pytest.mark.parametrize(
    "media_type",
    ["image/png", "image/svg+xml", "application/pdf", "application/javascript", "text/html", "text/x-python",
     "application/octet-stream", "", "TEXT/PLAIN", "text/plain; charset=utf-8"],
)
def test_media_types_outside_the_list_are_refused(media_type: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "attachments": [attachment(media_type=media_type)]})
    assert response.status_code == 422
    assert fields(response) == [("body.attachments.0.media_type", "literal_error")]


@pytest.mark.parametrize("char", LIMITS["attachment_name_forbidden_characters"])
def test_every_forbidden_name_character_is_refused(char: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "attachments": [attachment(name=f"a{char}b.txt")]})
    assert response.status_code == 422
    assert fields(response) == [("body.attachments.0.name", "invalid_attachment_name")]


@pytest.mark.parametrize("name", [".", "..", "a\u200bb.txt", "report\u202egnp.txt", "tab\tname.txt", "\u2066x"])
def test_dot_names_and_invisible_or_bidi_characters_in_names_are_refused(name: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "attachments": [attachment(name=name)]})
    assert response.status_code == 422
    assert fields(response)[0][1] == "invalid_attachment_name"


@pytest.mark.parametrize("content", ["bad\x00", "x\u202eevil", "bell\x07"])
def test_unsafe_characters_in_attachment_content_are_refused(content: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "attachments": [attachment(content=content)]})
    assert response.status_code == 422 and fields(response)[0][1] == "unsafe_character"


@pytest.mark.parametrize("content", ["{", "[1,", '{"a": 1} trailing', "[" * 50000])
def test_json_attachments_must_parse_however_deep(content: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "attachments": [attachment(name="d.json", media_type="application/json", content=content)]})
    assert response.status_code == 422 and fields(response)[0][1] == "invalid_json"


@pytest.mark.parametrize("message", ["x\u202ey", "x\u2067y", "x\x00y"])
def test_bidi_and_control_characters_in_messages_are_refused(message: str) -> None:
    response = client().post("/api/chat", json={"message": message})
    assert response.status_code == 422 and fields(response) == [("body.message", "unsafe_character")]


# ---- C. allowed model and mode values --------------------------------------------------------

@pytest.mark.parametrize("model", DEFAULT_MODELS)
def test_every_allowed_model_is_accepted(model: str) -> None:
    assert client().post("/api/chat", json={"message": "x", "model": model}).status_code == 200


@pytest.mark.parametrize(
    "model", ["Rapid", "RAPID", "rapid ", " rapid", "", "rapid\n", "rаpid", "rapid;drop", "gpt-4", "../rapid", "rapid\x00"]
)
def test_unlisted_or_lookalike_models_are_refused(model: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "model": model})
    assert response.status_code == 422
    assert fields(response)[0][0] == "body.model"


@pytest.mark.parametrize("mode", DEFAULT_MODES)
def test_every_allowed_mode_is_accepted(mode: str) -> None:
    assert client().post("/api/chat", json={"message": "x", "mode": mode}).status_code == 200


@pytest.mark.parametrize("mode", ["Coding", "standard", "standard ", "code", "write", "creative", "poetry", "", "coding;drop", "st\u0430ndard"])
def test_unlisted_modes_are_refused(mode: str) -> None:
    response = client().post("/api/chat", json={"message": "x", "mode": mode})
    assert response.status_code == 422 and fields(response)[0][0] == "body.mode"


@pytest.mark.parametrize("path, own", [("/api/research", "research"), ("/api/code", "coding"), ("/api/write", "writing")])
def test_task_endpoints_accept_only_their_own_mode(path: str, own: str) -> None:
    assert client().post(path, json={"message": "x", "mode": own}).status_code == 200
    assert fields(client().post(path, json={"message": "x", "mode": "general"})) == [("body.mode", "literal_error")]


def test_the_options_endpoint_and_the_validator_agree() -> None:
    c = client()
    options = c.get("/api/chat/options").json()
    assert set(options["modes"]) == set(DEFAULT_MODES)
    assert set(options["models"]) == set(DEFAULT_MODELS)
    for mode in options["modes"]:
        assert c.post("/api/chat", json={"message": "x", "mode": mode}).status_code == 200
    assert c.post("/api/chat", json={"message": "x", "mode": "not-listed"}).status_code == 422


# ---- D. safe error messages -------------------------------------------------------------------

BAD_REQUESTS = [
    ("post", "/api/chat", {"message": ""}),
    ("post", "/api/chat", {"message": "x", "model": "gpt-9"}),
    ("post", "/api/chat", {"message": "x", "attachments": [attachment(media_type="image/png")]}),
    ("post", "/api/research", {"message": "x", "mode": "coding"}),
    ("post", "/api/conversations", {"title": "t", "owner": "someone"}),
    ("patch", "/api/conversations/no-such-id", {"title": "t", "expected_version": 1}),
    ("get", "/api/conversations/bad%20id", None),
    ("get", "/api/chat", None),
    ("get", "/api/does-not-exist", None),
    ("post", f"/api/chat/{'0' * 32}/stop", None),
]


@pytest.mark.parametrize("method, path, body", BAD_REQUESTS, ids=[f"{m} {p}" for m, p, _ in BAD_REQUESTS])
def test_every_refusal_is_an_envelope_with_no_internal_detail(method: str, path: str, body) -> None:
    c = client()
    response = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
    assert response.status_code >= 400
    parse_error_body(response.text)
    assert response.headers["x-request-id"]
    for leak in ("Traceback", 'File "', "site-packages", ".py:", "pydantic_core", "RuntimeError", "ValueError"):
        assert leak not in response.text, leak


def test_an_unexpected_failure_says_nothing_about_its_cause() -> None:
    async def crash(messages, cancel):
        raise RuntimeError("database password is hunter2 at /srv/internal/db.py:41")
        yield  # pragma: no cover

    response = TestClient(app_with(ScriptedProvider(crash)), raise_server_exceptions=False).post("/api/chat", json={"message": "x"})
    assert response.status_code == 502
    assert "hunter2" not in response.text and "/srv/internal" not in response.text
    assert ChatResponse.model_validate_json(response.text).error.code.value == "GENERATION_FAILED"


def test_an_unhandled_route_failure_returns_the_generic_500() -> None:
    app = app_with(echoing())

    @app.get("/audit-crash")
    async def boom() -> None:
        raise RuntimeError("internal detail: token=abc123")

    response = TestClient(app, raise_server_exceptions=False).get("/audit-crash")
    assert response.status_code == 500
    assert response.json()["error"] == {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred.", "details": None}
    assert "abc123" not in response.text


def test_an_oversized_model_reply_is_refused_not_stored() -> None:
    async def huge(messages, cancel):
        for _ in range(LIMITS["reply_max_chars"] // 1000 + 2):
            yield "a" * 1000

    response = TestClient(app_with(ScriptedProvider(huge)), raise_server_exceptions=False).post("/api/chat", json={"message": "x"})
    assert response.status_code == 502
    reply = ChatResponse.model_validate_json(response.text)
    assert reply.status.value == "error" and reply.content is None


# ---- E. request validation, the same for every chat-style endpoint -------------------------

CHAT_PATHS = ["/api/chat", "/api/research", "/api/code", "/api/write"]
INVALID_BODIES = [
    {},
    {"message": None},
    {"message": 42},
    {"message": ["x"]},
    {"message": "x", "attachments": None},
    {"message": "x", "attachments": {"name": "a"}},
    {"message": "x", "attachments": ["notes"]},
    {"message": "x", "attachments": [{"name": "a", "media_type": "text/plain"}]},
    {"message": "x", "attachments": [attachment(), {"name": "b"}]},
    {"message": "x", "model": None, "mode": None},
    {"message": "x", "tools": {"webSearch": True}},
    {"message": "x", "is_admin": True},
]


@pytest.mark.parametrize("path", CHAT_PATHS)
@pytest.mark.parametrize("body", INVALID_BODIES, ids=[str(i) for i in range(len(INVALID_BODIES))])
def test_invalid_bodies_are_refused_the_same_way_on_every_endpoint(path: str, body: dict) -> None:
    response = client().post(path, json=body)
    assert response.status_code == 422, (path, body)
    envelope = parse_error_body(response.text)
    assert envelope.error.code is ErrorCode.VALIDATION_ERROR
    assert envelope.error.details and all(issue.field.startswith("body") for issue in envelope.error.details)


@pytest.mark.parametrize("path", CHAT_PATHS)
@pytest.mark.parametrize(
    "raw, reason",
    [
        (b"\x00" + b'{"message":"x"}', "json_invalid"),
        ('{"message":"x"}'.encode("utf-16"), "json_invalid"),
        (b'{"message":"\xff\xfe"}', "json_invalid"),
        (b'{"message":' , "json_invalid"),
        (b'{"message":"x","n":' + b"9" * 5000 + b"}", "json_invalid"),
        (b"[" * 20000, "json_invalid"),
        (b"\xef\xbb\xbf" + b'{"message":"x"}', "json_invalid"),
    ],
)
def test_malformed_bodies_are_one_consistent_error(path: str, raw: bytes, reason: str) -> None:
    response = client().post(path, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422, raw[:20]
    assert response.json()["error"]["details"][0]["type"] == reason


def test_non_json_content_types_are_refused() -> None:
    response = client().post("/api/chat", content=b"message=hi", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


# ---- F. CORS ------------------------------------------------------------------------------------

def test_an_allowed_origin_is_answered_and_a_real_request_carries_it() -> None:
    c = client(cors_origins=(ALLOWED_ORIGIN,))
    preflight = c.options("/api/chat", headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in preflight.headers
    real = c.post("/api/chat", json={"message": "x"}, headers={"Origin": ALLOWED_ORIGIN})
    assert real.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_an_unlisted_origin_is_refused_with_the_envelope_and_gets_no_cors_headers() -> None:
    c = client(cors_origins=(ALLOWED_ORIGIN,))
    preflight = c.options("/api/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 403
    assert preflight.json()["error"]["code"] == "FORBIDDEN"
    real = c.post("/api/chat", json={"message": "x"}, headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in real.headers


def test_cors_allows_only_the_methods_the_api_uses() -> None:
    c = client(cors_origins=(ALLOWED_ORIGIN,))
    allowed = c.options("/api/conversations/x", headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "PATCH"})
    assert "PATCH" in allowed.headers["access-control-allow-methods"]
    denied = c.options("/api/chat", headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "DELETE"})
    assert "DELETE" not in denied.headers.get("access-control-allow-methods", "")


@pytest.mark.parametrize("origins", [("*",), ("null",), ("https://a.example/path",), ("ftp://a.example",), ("https://a.example?x=1",)])
def test_unsafe_cors_origins_are_refused_at_startup(origins) -> None:
    with pytest.raises(ConfigError):
        validate_settings(Settings(cors_origins=origins))


# ---- G. rate limiting --------------------------------------------------------------------------

def test_general_rate_limit_answers_429_with_retry_after() -> None:
    c = client(rate_limit_per_minute=3)
    assert [c.get("/api/models").status_code for _ in range(3)] == [200, 200, 200]
    limited = c.get("/api/models")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) >= 1


def test_generation_requests_have_a_stricter_shared_limit() -> None:
    c = client(rate_limit_per_minute=100, rate_limit_generation_per_minute=2)
    assert c.post("/api/chat", json={"message": "a"}).status_code == 200
    assert c.post("/api/research", json={"message": "b"}).status_code == 200
    third = c.post("/api/code", json={"message": "c"})
    assert third.status_code == 429
    assert "Too many replies" in third.json()["error"]["message"]
    assert c.get("/api/models").status_code == 200  # reading is still allowed


def test_failed_attempts_count_towards_the_limit() -> None:
    c = client(api_access_key=Secret(KEY), rate_limit_per_minute=3)
    statuses = [c.get("/api/models", headers={"Authorization": f"Bearer guess-{i}"}).status_code for i in range(4)]
    assert statuses == [401, 401, 401, 429]


def test_health_is_never_rate_limited() -> None:
    c = client(rate_limit_per_minute=1)
    assert all(c.get("/health/live").status_code == 200 for _ in range(5))


# ---- H. keys and credentials never leave the server ------------------------------------------

def test_the_access_key_appears_in_no_response() -> None:
    c = client(api_access_key=Secret(KEY))
    responses = [
        c.get("/api/models", headers={"Authorization": f"Bearer {KEY}x"}),
        c.post("/api/chat", json={"message": ""}, headers={"Authorization": f"Bearer {KEY}"}),
        c.post("/api/chat", json={"message": "x"}, headers={"Authorization": f"Bearer {KEY}"}),
        c.get("/health"),
        c.get("/api/contract"),
        c.get("/api/does-not-exist", headers={"Authorization": f"Bearer {KEY}"}),
    ]
    for response in responses:
        assert KEY not in response.text
        assert KEY not in json.dumps(dict(response.headers))


def test_the_access_key_is_not_in_the_published_contract_or_the_schema() -> None:
    app = app_with(echoing(), api_access_key=Secret(KEY))
    assert KEY not in json.dumps(app.openapi())
    assert KEY not in json.dumps(build_contract())


def test_settings_and_configuration_errors_never_show_the_key() -> None:
    settings = Settings(api_access_key=Secret(KEY), model_routes=(("rapid", "missing-provider", "m"),))
    assert KEY not in repr(settings) and KEY not in str(settings)
    with pytest.raises(ConfigError) as info:
        create_app(settings)
    assert KEY not in str(info.value)


def test_a_wrong_key_is_a_401_that_says_only_what_is_required() -> None:
    c = client(api_access_key=Secret(KEY))
    response = c.post("/api/chat", json={"message": "x"}, headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "A valid bearer token is required."
    assert response.headers["www-authenticate"] == "Bearer"


# ---- I. response hardening ------------------------------------------------------------------

def test_api_responses_carry_the_restrictive_policy_and_no_server_header() -> None:
    response = client().get("/api/models")
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert "server" not in response.headers


def test_the_chat_page_keeps_its_own_policy_not_the_api_policy() -> None:
    response = client().get("/app/chat.html")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


def test_every_refusal_carries_the_request_id_and_the_policy() -> None:
    response = client().post("/api/chat", json={"message": ""}, headers={"X-Request-ID": "audit-trace-1"})
    assert response.headers["x-request-id"] == "audit-trace-1"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
