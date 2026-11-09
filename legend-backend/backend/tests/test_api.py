import json

from fastapi.testclient import TestClient

from backend.config import DEFAULT_MODEL, DEFAULT_MODELS, DEFAULT_MODES, Settings
from backend.main import create_app
from backend.tests.fakes import client_with, replying

CONTRACT_KEYS = {"conversation_id", "model", "mode", "content", "status", "error", "detail"}


def test_health_reports_version_and_component_checks() -> None:
    with TestClient(create_app(Settings())) as client:
        body = client.get("/health").json()
    assert body["version"] == "0.1.0"
    assert body["status"] == "degraded"  # no model has a route in this configuration
    assert body["checks"]["models"] == "degraded"
    assert body["checks"]["server"] == "ok"


def test_chat_returns_contract_shaped_reply_from_provider() -> None:
    body = client_with(replying("hello from the provider")).post(
        "/api/chat", json={"message": "  hello  ", "model": "sonet", "mode": "general"}
    ).json()
    assert set(body) == CONTRACT_KEYS
    assert body["status"] == "success"
    assert body["model"] == "sonet"
    assert body["content"] == "hello from the provider"
    assert body["error"] is None


def test_missing_model_resolves_to_rapid() -> None:
    provider = replying("ok")
    body = client_with(provider).post("/api/chat", json={"message": "hi"}).json()
    assert body["model"] == DEFAULT_MODEL == "rapid"
    assert body["mode"] == "general"


def test_model_routing_selects_the_provider_model() -> None:
    provider = replying("ok")
    client_with(provider).post("/api/chat", json={"message": "hi", "model": "mythical"})
    assert provider.models_seen == ["fake-model"]


def test_chat_keeps_conversation_id() -> None:
    client = client_with(replying("ok"))
    first = client.post("/api/chat", json={"message": "one"}).json()
    second = client.post(
        "/api/chat", json={"conversation_id": first["conversation_id"], "message": "two"}
    ).json()
    assert second["conversation_id"] == first["conversation_id"]


def test_unknown_model_and_mode_are_validation_errors() -> None:
    client = client_with(replying("ok"))
    unknown_model = client.post("/api/chat", json={"message": "hi", "model": "gpt-9"})
    unknown_mode = client.post("/api/chat", json={"message": "hi", "mode": "nope"})
    assert unknown_model.status_code == 422
    assert unknown_mode.status_code == 422
    assert "Allowed values" in unknown_model.json()["error"]["details"][0]["message"]


def test_rejects_blank_message_and_extra_fields() -> None:
    client = client_with(replying("ok"))
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", json={"message": "hi", "admin": True}).status_code == 422


def test_unrouted_model_is_503_and_never_success() -> None:
    response = TestClient(create_app(Settings())).post("/api/chat", json={"message": "hi"})
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "error"
    assert body["error"]["code"] == "MODEL_UNAVAILABLE"
    assert body["content"] is None


def test_stream_emits_start_delta_done_with_contract_response() -> None:
    client = client_with(replying("streamed"))
    with client.stream("POST", "/api/chat/stream", json={"message": "hi there"}) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())
    assert "event: start" in text
    assert "event: delta" in text
    frames = [frame for frame in text.split("\n\n") if frame.strip()]
    final = json.loads(frames[-1].split("data: ", 1)[1])
    assert final["type"] == "done"
    assert set(final["response"]) == CONTRACT_KEYS
    assert final["response"]["status"] == "success"


def test_stream_rejects_unknown_model_before_streaming() -> None:
    response = client_with(replying("ok")).post(
        "/api/chat/stream", json={"message": "hi", "model": "nope"}
    )
    assert response.status_code == 422


def test_stop_unknown_request_returns_404() -> None:
    assert client_with(replying("ok")).post(f"/api/chat/{'0' * 32}/stop").status_code == 404


def test_options_lists_catalog_and_configured_routes() -> None:
    body = client_with(replying("ok")).get("/api/chat/options").json()
    assert body["models"] == list(DEFAULT_MODELS)
    assert body["available_models"] == list(DEFAULT_MODELS)
    assert body["default_model"] == "rapid"
    assert body["modes"] == list(DEFAULT_MODES)
    assert body["default_mode"] == "general"


def test_options_reports_no_available_models_without_routes() -> None:
    body = TestClient(create_app(Settings())).get("/api/chat/options").json()
    assert body["models"] == list(DEFAULT_MODELS)
    assert body["available_models"] == []
