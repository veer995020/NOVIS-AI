"""QA matrix for the six required endpoints. Each runs a success case and failure cases.

  POST /chat          -> POST /api/chat
  POST /research      -> POST /api/research
  POST /code          -> POST /api/code
  POST /write         -> POST /api/write
  GET  /models        -> GET  /api/models
  GET  /health        -> GET  /health

Success cases use a scripted provider that lives only in the test package. The
application never registers it. Failure cases use real errors from the real app.
"""
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.tests.fakes import app_with, crashing, replying, timing_out

CHAT_POSTS = ["/api/chat", "/api/research", "/api/code", "/api/write"]
EXPECTED_MODE = {"/api/chat": "standard", "/api/research": "research", "/api/code": "code", "/api/write": "write"}


@pytest.mark.parametrize("path", CHAT_POSTS)
def test_post_success_returns_200_with_completed_reply(path: str) -> None:
    body = TestClient(app_with(replying("the reply"))).post(path, json={"message": "hello"}).json()
    assert body["status"] == "completed"
    assert body["content"] == "the reply"
    assert body["model"] == "rapid"
    assert body["mode"] == EXPECTED_MODE[path]
    assert body["error"] is None
    assert body["conversation_id"]


@pytest.mark.parametrize("path", CHAT_POSTS)
def test_post_without_a_provider_is_503_and_never_a_success(path: str) -> None:
    response = TestClient(create_app(Settings())).post(path, json={"message": "hello"})
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["content"] == ""
    assert body["error"]["code"] == "model_unavailable"


@pytest.mark.parametrize("path", CHAT_POSTS)
def test_post_provider_crash_is_502_and_hides_the_cause(path: str) -> None:
    response = TestClient(app_with(crashing())).post(path, json={"message": "hello"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "generation_failed"
    assert "internal-detail-99" not in response.text


@pytest.mark.parametrize("path", CHAT_POSTS)
def test_post_timeout_is_504(path: str) -> None:
    response = TestClient(app_with(timing_out(), generation_timeout_seconds=1)).post(
        path, json={"message": "hello"}
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "generation_timeout"


@pytest.mark.parametrize("path", CHAT_POSTS)
def test_post_validation_failures_are_422_with_field_paths(path: str) -> None:
    client = TestClient(app_with(replying("ok")))
    for body, field in [
        ({"model": "rapid"}, "body.message"),
        ({"message": "", "model": "rapid"}, "body.message"),
        ({"message": "hi", "model": "gpt-9"}, "body.model"),
        ({"message": "hi", "unknown": 1}, "body.unknown"),
    ]:
        response = client.post(path, json=body)
        assert response.status_code == 422, body
        fields = [issue["field"] for issue in response.json()["error"]["details"]]
        assert field in fields, body


@pytest.mark.parametrize("path", CHAT_POSTS)
def test_post_rejects_non_json_bodies(path: str) -> None:
    response = TestClient(app_with(replying("ok"))).post(
        path, content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_task_endpoints_reject_the_mode_field() -> None:
    client = TestClient(app_with(replying("ok")))
    for path in CHAT_POSTS[1:]:
        response = client.post(path, json={"message": "hi", "mode": "code"})
        assert response.status_code == 422, path


def test_models_success_lists_all_models_and_availability() -> None:
    from backend.config import DEFAULT_MODELS

    with TestClient(app_with(replying("ok"))) as client:
        body = client.get("/api/models").json()
    assert [m["id"] for m in body["models"]] == list(DEFAULT_MODELS)
    assert all(m["available"] for m in body["models"])
    assert body["default_model"] == "rapid"


def test_models_without_routes_reports_every_model_unavailable() -> None:
    with TestClient(create_app(Settings())) as client:
        body = client.get("/api/models").json()
    assert all(m["available"] is False for m in body["models"])


def test_health_success_is_ok_with_routes_and_started() -> None:
    with TestClient(app_with(replying("ok"))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_is_degraded_without_routes_and_still_200() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_health_failure_before_startup_is_503_down() -> None:
    response = TestClient(app_with(replying("ok"))).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_wrong_method_on_an_endpoint_is_405_envelope() -> None:
    response = TestClient(app_with(replying("ok"))).get("/api/chat")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_unknown_path_is_404_envelope() -> None:
    response = TestClient(app_with(replying("ok"))).get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["request_id"]
