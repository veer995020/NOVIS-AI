"""Access control: shared access key, Host allowlist, per-client rate limit."""
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from backend.config import Secret, Settings, load_settings
from backend.middleware import AccessControlMiddleware, _RateLimiter
from backend.tests.fakes import app_with, replying

KEY = "qa-access-key-123"


def _keyed(**overrides) -> TestClient:
    return TestClient(app_with(replying("ok"), api_access_key=Secret(KEY), **overrides))


# ---- shared access key --------------------------------------------------------------

def test_api_call_without_the_key_is_401_with_challenge() -> None:
    response = _keyed().post("/api/chat", json={"message": "hi"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("header", [f"Bearer {KEY[:-1]}", "Bearer ", "Basic " + KEY, KEY, ""])
def test_wrong_or_malformed_credentials_are_401(header: str) -> None:
    headers = {"Authorization": header} if header else {}
    response = _keyed().post("/api/chat", json={"message": "hi"}, headers=headers)
    assert response.status_code == 401


def test_correct_key_is_accepted_in_any_bearer_case() -> None:
    for scheme in ("Bearer", "bearer", "BEARER"):
        response = _keyed().post("/api/chat", json={"message": "hi"}, headers={"Authorization": f"{scheme} {KEY}"})
        assert response.status_code == 200, scheme


def test_401_body_never_contains_the_key() -> None:
    response = _keyed().post("/api/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer wrong-{KEY}"})
    assert KEY not in response.text
    assert KEY not in response.headers.get("www-authenticate", "")


def test_health_and_docs_stay_reachable_without_the_key() -> None:
    with _keyed(enable_docs=True) as client:
        health = client.get("/health")
        assert health.status_code == 200  # reachable without the key
        assert health.json()["status"] in ("ok", "degraded")
        assert client.get("/health/live").status_code == 200
        assert client.get("/docs").status_code == 200


def test_cors_preflight_is_not_blocked_by_the_key() -> None:
    app = app_with(replying("ok"), api_access_key=Secret(KEY), cors_origins=("https://app.example.com",))
    response = TestClient(app).options(
        "/api/chat",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_access_control_rejections_carry_request_id_and_security_headers() -> None:
    response = _keyed().get("/api/models", headers={"X-Request-ID": "trace-401"})
    assert response.status_code == 401
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-request-id"] != "trace-401"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


# ---- Host allowlist -----------------------------------------------------------------

def test_allowed_host_is_accepted_with_or_without_port() -> None:
    client = TestClient(app_with(replying("ok"), allowed_hosts=("api.example.com",)))
    assert client.get("/api/models", headers={"Host": "api.example.com"}).status_code == 200
    assert client.get("/api/models", headers={"Host": "API.example.com:8443"}).status_code == 200


def test_other_hosts_are_refused_with_400() -> None:
    client = TestClient(app_with(replying("ok"), allowed_hosts=("api.example.com",)))
    for host in ("evil.example", "api.example.com.evil.example", "[::1]", "127.0.0.1"):
        response = client.get("/api/models", headers={"Host": host})
        assert response.status_code == 400, host
        assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_health_probes_ignore_the_host_allowlist() -> None:
    client = TestClient(app_with(replying("ok"), allowed_hosts=("api.example.com",)))
    assert client.get("/health/live", headers={"Host": "10.0.0.7"}).status_code == 200


def test_no_allowlist_means_no_host_check() -> None:
    assert TestClient(app_with(replying("ok"))).get("/api/models", headers={"Host": "anything"}).status_code == 200


# ---- rate limiting ------------------------------------------------------------------

def test_rate_limit_returns_429_with_retry_after() -> None:
    client = TestClient(app_with(replying("ok"), rate_limit_per_minute=3))
    statuses = [client.get("/api/models").status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429]
    limited = client.get("/api/models")
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["retry-after"]) >= 1


def test_health_is_never_rate_limited() -> None:
    client = TestClient(app_with(replying("ok"), rate_limit_per_minute=1))
    for _ in range(5):
        assert client.get("/health/live").status_code == 200


def test_rate_limit_zero_disables_limiting() -> None:
    client = TestClient(app_with(replying("ok"), rate_limit_per_minute=0))
    assert all(client.get("/api/models").status_code == 200 for _ in range(20))


def test_sliding_window_frees_slots_after_a_minute() -> None:
    now = {"t": 1000.0}
    limiter = _RateLimiter(2, clock=lambda: now["t"])
    assert limiter.take("a") is None
    assert limiter.take("a") is None
    wait = limiter.take("a")
    assert wait is not None and 0 < wait <= 60
    assert limiter.take("b") is None  # other clients are unaffected
    now["t"] += 61
    assert limiter.take("a") is None


def test_rate_limiter_memory_is_capped(monkeypatch) -> None:
    import backend.middleware as middleware

    monkeypatch.setattr(middleware, "MAX_TRACKED_CLIENTS", 5)
    limiter = _RateLimiter(10, clock=lambda: 0.0)
    for index in range(50):
        limiter.take(f"client-{index}")
    assert len(limiter._hits) == 5


def _middleware(
    *,
    api_key: str | None = None,
    allowed_hosts: tuple[str, ...] = (),
    rate_limit_per_minute: int = 0,
    trust_forwarded_for: bool = False,
) -> AccessControlMiddleware:
    return AccessControlMiddleware(
        app=lambda scope, receive, send: None,
        api_key=api_key,
        allowed_hosts=allowed_hosts,
        rate_limit_per_minute=rate_limit_per_minute,
        trust_forwarded_for=trust_forwarded_for,
    )


def test_client_identity_uses_the_socket_address_by_default() -> None:
    scope = {"client": ("203.0.113.9", 5000)}
    headers = Headers(headers={"x-forwarded-for": "198.51.100.1"})
    assert _middleware()._client_key(scope, headers) == "203.0.113.9"


def test_client_identity_uses_forwarded_address_only_when_trusted() -> None:
    scope = {"client": ("10.0.0.2", 5000)}
    headers = Headers(headers={"x-forwarded-for": "198.51.100.1, 10.0.0.2"})
    assert _middleware(trust_forwarded_for=True)._client_key(scope, headers) == "198.51.100.1"


def test_forwarded_address_longer_than_64_characters_is_ignored() -> None:
    scope = {"client": ("10.0.0.2", 5000)}
    headers = Headers(headers={"x-forwarded-for": "x" * 65})
    assert _middleware(trust_forwarded_for=True)._client_key(scope, headers) == "10.0.0.2"


# ---- secrets never reach configuration reprs or logs ----------------------------------

def test_access_key_is_redacted_in_settings() -> None:
    settings = Settings(api_access_key=Secret(KEY))
    assert KEY not in repr(settings)
    assert KEY not in str(settings)


def test_access_key_is_not_logged_at_startup(caplog) -> None:
    import logging

    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = Capture()
    logging.getLogger("backend").addHandler(handler)
    try:
        with TestClient(app_with(replying("ok"), api_access_key=Secret(KEY))):
            pass
    finally:
        logging.getLogger("backend").removeHandler(handler)
    assert captured, "startup should log"
    assert all(KEY not in record.getMessage() for record in captured)


# ---- production rules -----------------------------------------------------------------

def test_production_requires_access_key_and_allowed_hosts() -> None:
    from backend.config import ConfigError

    with pytest.raises(ConfigError) as info:
        load_settings({"APP_ENV": "production", "CORS_ORIGINS": "https://app.example.com"})
    assert any("API_ACCESS_KEY" in p for p in info.value.problems)
    assert any("ALLOWED_HOSTS" in p for p in info.value.problems)


def test_invalid_allowed_host_names_are_rejected() -> None:
    from backend.config import ConfigError, validate_settings

    with pytest.raises(ConfigError):
        validate_settings(Settings(allowed_hosts=("https://api.example.com",)))
    with pytest.raises(ConfigError):
        validate_settings(Settings(allowed_hosts=("-bad.example",)))
