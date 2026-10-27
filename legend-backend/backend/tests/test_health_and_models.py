"""GET /api/models and the health endpoints."""
from fastapi.testclient import TestClient

from backend.config import DEFAULT_MODELS, Settings
from backend.main import create_app
from backend.tests.fakes import app_with, replying


def test_models_lists_every_model_with_availability() -> None:
    body = TestClient(app_with(replying("ok"))).get("/api/models").json()
    assert [m["id"] for m in body["models"]] == list(DEFAULT_MODELS)
    assert all(m["available"] for m in body["models"])
    assert body["default_model"] == "rapid"
    rapid = next(m for m in body["models"] if m["id"] == "rapid")
    assert rapid["default"] is True and rapid["name"] == "Rapid"


def test_models_reports_unrouted_models_as_unavailable() -> None:
    body = TestClient(create_app(Settings())).get("/api/models").json()
    assert all(m["available"] is False for m in body["models"])
    assert sum(m["default"] for m in body["models"]) == 1


def test_models_response_has_no_provider_details() -> None:
    body = TestClient(app_with(replying("ok"))).get("/api/models").json()
    assert set(body) == {"models", "default_model"}
    assert all(set(m) == {"id", "name", "available", "default"} for m in body["models"])


def test_health_ok_when_models_and_conversations_are_fine() -> None:
    with TestClient(app_with(replying("ok"))) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["checks"] == {"server": "ok", "models": "ok", "conversations": "ok"}
    assert set(body) == {"status", "version", "uptime_seconds", "checks"}


def test_health_is_down_before_startup() -> None:
    response = TestClient(app_with(replying("ok"))).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_health_reports_degraded_at_ninety_percent_of_conversation_limit() -> None:
    app = app_with(replying("ok"), max_conversations=10)
    store = app.state.conversation_store

    async def fill() -> None:
        for _ in range(9):
            await store.create()

    import asyncio

    asyncio.run(fill())
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["checks"]["conversations"] == "degraded"
    assert body["status"] == "degraded"


def test_liveness_and_readiness_have_safe_fields_only() -> None:
    with TestClient(app_with(replying("ok"))) as client:
        live = client.get("/health/live").json()
        ready = client.get("/health/ready").json()
    assert set(live) == {"status", "version", "uptime_seconds"}
    assert set(ready) == {"status", "version", "uptime_seconds"}
    assert live["status"] == "ok" and ready["status"] == "ready"
    assert "environment" not in ready and "active_requests" not in ready


def test_readiness_is_not_ready_before_startup() -> None:
    response = TestClient(app_with(replying("ok"))).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
