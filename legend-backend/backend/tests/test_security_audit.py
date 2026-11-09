"""Security audit of the API contract. Each test is one control, exercised with a hostile input.

Client-visible rules: input limits, attachment rules, model and mode allowlists, safe error messages,
request validation, CORS, rate limits. Operator-visible rules: no credential or stack trace in logs
or responses.
"""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend.config import Secret, Settings
from backend.main import create_app
from backend.tests.fakes import ScriptedProvider, app_with, echoing
from backend.utils.logging_setup import JsonFormatter, PlainFormatter
from backend.utils.redaction import redact_text, register_secret

KEY = "audit-key-8f2c6e1a9b4d7053"
AUTH = {"Authorization": f"Bearer {KEY}"}
SECRET_INTERNAL = "INTERNAL-PROVIDER-SECRET-77"


def _keyed(provider=None, **overrides) -> TestClient:
    settings = dict(api_access_key=Secret(KEY), allowed_hosts=("testserver",))
    settings.update(overrides)
    return TestClient(app_with(provider or echoing(), **settings))


def _no_secret_leaked(response, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in response.text, "a credential reached the client"
    assert "Traceback" not in response.text
    assert 'File "' not in response.text


# ---- authentication: checked before the body is read ------------------------------------

def test_an_unauthenticated_oversized_body_is_refused_before_it_is_read() -> None:
    client = _keyed()
    response = client.post("/api/chat", content=b'{"message":"' + b"a" * 3_000_000 + b'"}')
    assert response.status_code == 401, "auth must come before the size check"


def test_wrong_and_missing_keys_get_the_same_generic_refusal_without_echo() -> None:
    client = _keyed()
    missing = client.post("/api/chat", json={"message": "hi"})
    wrong = client.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer nope-123"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["error"]["message"] == wrong.json()["error"]["message"]
    _no_secret_leaked(wrong, KEY, "nope-123")


def test_unauthenticated_callers_cannot_tell_real_routes_from_missing_ones() -> None:
    client = _keyed()
    assert client.get("/api/does-not-exist").status_code == 401


def test_the_key_is_not_accepted_in_the_query_string() -> None:
    client = _keyed()
    assert client.post(f"/api/chat?key={KEY}", json={"message": "hi"}).status_code == 401


# ---- host and origin --------------------------------------------------------------------

def test_a_foreign_host_header_is_refused() -> None:
    client = _keyed()
    response = client.post("/api/chat", json={"message": "hi"}, headers={**AUTH, "Host": "evil.example"})
    assert response.status_code == 400


def test_a_browser_request_from_a_foreign_origin_is_refused_even_with_a_valid_key() -> None:
    provider = echoing()
    client = _keyed(provider)
    response = client.post("/api/chat", json={"message": "hi"}, headers={**AUTH, "Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert provider.calls == [], "a refused request must not reach the model"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-request-id")


def test_origin_null_is_refused() -> None:
    assert _keyed().post("/api/chat", json={"message": "hi"}, headers={**AUTH, "Origin": "null"}).status_code == 403


def test_the_same_site_origin_is_allowed_so_the_served_page_works() -> None:
    client = _keyed()
    response = client.post("/api/chat", json={"message": "hi"}, headers={**AUTH, "Origin": "http://testserver"})
    assert response.status_code == 200


def test_a_listed_origin_gets_cors_headers_and_credentials_are_never_allowed() -> None:
    app = app_with(echoing(), api_access_key=Secret(KEY), cors_origins=("https://app.example.com",))
    client = TestClient(app)
    response = client.options(
        "/api/chat",
        headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert response.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "access-control-allow-credentials" not in response.headers


def test_a_foreign_preflight_is_refused_in_the_error_envelope() -> None:
    app = app_with(echoing(), cors_origins=("https://app.example.com",))
    response = TestClient(app).options(
        "/api/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert "access-control-allow-origin" not in response.headers


def test_without_cors_configured_a_cross_origin_browser_request_is_refused() -> None:
    client = TestClient(app_with(echoing()))
    assert client.post("/api/chat", json={"message": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_non_browser_clients_without_an_origin_are_not_affected_by_origin_rules() -> None:
    assert _keyed().post("/api/chat", json={"message": "hi"}, headers=AUTH).status_code == 200


# ---- input size and request validation --------------------------------------------------

def test_a_message_over_the_limit_is_refused_and_the_value_is_not_echoed() -> None:
    client = _keyed()
    response = client.post("/api/chat", json={"message": "x" * 8001}, headers=AUTH)
    assert response.status_code == 422
    assert "x" * 100 not in response.text


def test_oversized_bodies_are_refused_with_413() -> None:
    response = _keyed().post("/api/chat", content=b'{"message":"' + b"a" * 3_000_000 + b'"}', headers=AUTH)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"message":"\xff\xfe"}',                  # invalid UTF-8
        b'{"message":"bad \\ud800"}',               # lone surrogate escape
        b'{"message":"a\\u0000b"}',                 # NUL character
        b'{"message":' ,                            # truncated JSON
        b'[' * 5000 + b']' * 5000,                  # deep nesting
        b'{"message":"x","attachments":' + b'[' * 3000 + b']' * 3000 + b'}',
    ],
)
def test_malformed_bodies_are_rejected_with_422_never_500(raw: bytes) -> None:
    response = _keyed().post("/api/chat", content=raw, headers={**AUTH, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_a_body_that_is_not_json_is_refused_with_415() -> None:
    response = _keyed().post("/api/chat", content=b"hello", headers={**AUTH, "Content-Type": "text/plain"})
    assert response.status_code == 415


def test_unknown_fields_are_rejected_not_ignored() -> None:
    response = _keyed().post("/api/chat", json={"message": "hi", "is_admin": True}, headers=AUTH)
    assert response.status_code == 422


# ---- model and mode allowlists ------------------------------------------------------------

@pytest.mark.parametrize(
    "field, value",
    [("model", "<script>alert(1)</script>"), ("model", "../../etc/passwd"), ("mode", "../../secret"), ("mode", "poetry")],
)
def test_unlisted_models_and_modes_are_refused_without_echoing_the_value(field: str, value: str) -> None:
    response = _keyed().post("/api/chat", json={"message": "hi", field: value}, headers=AUTH)
    assert response.status_code == 422
    assert value not in response.text
    assert response.json()["error"]["details"][0]["field"] == f"body.{field}"


# ---- attachment rules -----------------------------------------------------------------

def _attachment(**overrides):
    data = {"name": "notes.md", "media_type": "text/markdown", "content": "x"}
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "attachments, field",
    [
        ([_attachment()] * 4, "body.attachments"),                                        # too many
        ([_attachment(content="a" * 64_001)], "body.attachments.0.content"),              # too large
        ([_attachment(content="a" * 64_000)] * 3, "body.attachments"),                    # total too large
        ([_attachment(media_type="image/png")], "body.attachments.0.media_type"),         # image
        ([_attachment(media_type="application/pdf")], "body.attachments.0.media_type"),   # binary type
        ([_attachment(name="../../etc/passwd")], "body.attachments.0.name"),              # traversal
        ([_attachment(name="a\x00.txt")], "body.attachments.0.name"),                     # NUL in name
        ([_attachment(media_type="application/json", content="{not json")], "body.attachments.0.content"),
        ([_attachment(content="bad\x07bell")], "body.attachments.0.content"),
    ],
)
def test_attachment_rules_are_enforced_on_the_server(attachments, field: str) -> None:
    response = _keyed().post("/api/chat", json={"message": "hi", "attachments": attachments}, headers=AUTH)
    assert response.status_code == 422
    fields = [issue["field"] for issue in response.json()["error"]["details"]]
    assert field in fields


# ---- safe error messages and credentials ---------------------------------------------------

def test_an_internal_failure_returns_a_fixed_message_and_no_stack_trace() -> None:
    async def script(messages, cancel):
        raise RuntimeError(f"provider failed with {SECRET_INTERNAL}")
        yield  # pragma: no cover

    client = _keyed(ScriptedProvider(script))
    response = client.post("/api/chat", json={"message": "hi"}, headers=AUTH)
    assert response.status_code == 502
    _no_secret_leaked(response, SECRET_INTERNAL, KEY)


def test_the_access_key_is_redacted_from_log_output_in_both_formats() -> None:
    register_secret(KEY)
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, f"auth header was Bearer {KEY}", None, None)
    assert KEY not in JsonFormatter().format(record)
    assert KEY not in PlainFormatter("%(message)s").format(record)


def test_the_key_is_redacted_even_inside_an_exception_message_in_logs() -> None:
    register_secret(KEY)
    try:
        raise RuntimeError(f"upstream rejected {KEY}")
    except RuntimeError:
        import sys
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    assert KEY not in JsonFormatter().format(record)


def test_redaction_also_covers_the_json_escaped_form_of_a_secret() -> None:
    weird = 'key"with\\quote'
    register_secret(weird)
    assert weird not in redact_text(json.dumps({"m": weird}))
    assert json.dumps(weird)[1:-1] not in redact_text(json.dumps({"m": weird}))


def test_security_headers_are_on_every_kind_of_response() -> None:
    client = _keyed()
    for response in [
        client.get("/api/models", headers=AUTH),
        client.get("/api/models"),
        client.post("/api/chat", json={"message": ""}, headers=AUTH),
        client.get("/api/does-not-exist"),
    ]:
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("cache-control") == "no-store"
        assert response.headers.get("x-request-id")
        assert "server" not in response.headers


# ---- rate limits -----------------------------------------------------------------------

def test_generation_is_rate_limited_with_retry_after() -> None:
    client = TestClient(app_with(echoing(), rate_limit_per_minute=100, rate_limit_generation_per_minute=2))
    codes = [client.post("/api/chat", json={"message": "hi"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    limited = client.post("/api/chat", json={"message": "hi"})
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) > 0


# ---- configuration -------------------------------------------------------------------

def test_a_wildcard_origin_is_refused_at_startup() -> None:
    from backend.config import ConfigError, validate_settings
    with pytest.raises(ConfigError):
        validate_settings(Settings(cors_origins=("*",)))


def test_production_refuses_to_start_without_the_controls_it_depends_on() -> None:
    from backend.config import ConfigError, validate_settings
    with pytest.raises(ConfigError):
        validate_settings(Settings(environment="production"))


def test_docs_are_not_served_in_production() -> None:
    client = TestClient(create_app(Settings(enable_docs=False)))
    assert client.get("/docs").status_code == 404
