from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


def _app(**overrides):
    settings = Settings(**overrides)
    return create_app(settings)


def _client(**overrides) -> TestClient:
    return TestClient(_app(**overrides))


def test_request_id_is_server_generated_and_never_the_clients_value() -> None:
    # The server makes every request id. A client's X-Request-ID is not used, so it cannot collide
    # with another turn's id.
    import re
    client = _client()
    generated = client.get("/health").headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    sent = client.get("/health", headers={"X-Request-ID": "trace-42"}).headers["X-Request-ID"]
    assert sent != "trace-42" and re.fullmatch(r"[0-9a-f]{32}", sent)


def test_security_headers_present() -> None:
    headers = _client().get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store"


def test_validation_error_uses_envelope_without_echoing_input() -> None:
    response = _client().post("/api/chat", json={"message": "", "model": "rapid"})
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]
    assert "input" not in str(body)


def test_unknown_option_uses_envelope_with_field_path() -> None:
    response = _client().post("/api/chat", json={"message": "hi", "model": "nope"})
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert any(issue["field"] == "body.model" for issue in body["error"]["details"])


def test_not_found_uses_envelope() -> None:
    response = _client().get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_oversized_body_is_rejected() -> None:
    client = _client(max_request_bytes=1024)
    response = client.post(
        "/api/chat", content=b"x" * 2048, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_docs_disabled_by_default_and_enabled_on_request() -> None:
    assert _client().get("/docs").status_code == 404
    assert _client(enable_docs=True).get("/docs").status_code == 200


def test_cors_allows_only_configured_origin() -> None:
    client = _client(cors_origins=("https://app.example.com",))
    allowed = client.get("/health", headers={"Origin": "https://app.example.com"})
    assert allowed.headers.get("access-control-allow-origin") == "https://app.example.com"
    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_no_cors_headers_without_configured_origins() -> None:
    response = _client().get("/health", headers={"Origin": "https://anything.example"})
    assert "access-control-allow-origin" not in response.headers


def test_readiness_is_ready_only_while_running() -> None:
    app = _app()
    assert TestClient(app).get("/health/ready").status_code == 503
    with TestClient(app) as client:
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    assert app.state.ready is False


def test_liveness_reports_uptime() -> None:
    app = _app()
    with TestClient(app) as client:
        body = client.get("/health/live").json()
    assert body["status"] == "ok"
    assert body["uptime_seconds"] >= 0


def test_unhandled_error_returns_generic_500() -> None:
    app = _app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internals" not in response.text
