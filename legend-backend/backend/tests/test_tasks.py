"""Task endpoints: each one is the shared chat flow with a fixed mode."""
import pytest

from backend.schemas.contract import build_contract
from backend.services.tasks import TASK_SPECS
from backend.tests.fakes import client_with, crashing, replying, timing_out

TASK_PATHS = [spec.path for spec in TASK_SPECS]
CHAT_PATH = "/api/chat"
CONTRACT_KEYS = {"conversation_id", "model", "mode", "content", "status", "error"}


def test_each_task_runs_with_its_fixed_mode() -> None:
    provider = replying("ok")
    client = client_with(provider)
    for spec in TASK_SPECS:
        response = client.post(spec.path, json={"message": "hi"})
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == spec.mode
        assert body["status"] == "completed"
        assert f"Mode: {spec.mode}." in provider.calls[-1][0]["content"]


@pytest.mark.parametrize("path", TASK_PATHS)
def test_task_endpoints_reject_a_mode_field(path: str) -> None:
    response = client_with(replying("ok")).post(path, json={"message": "hi", "mode": "code"})
    assert response.status_code == 422
    assert any(issue["field"] == "body.mode" for issue in response.json()["error"]["details"])


@pytest.mark.parametrize("path", TASK_PATHS)
def test_task_endpoints_validate_like_chat(path: str) -> None:
    client = client_with(replying("ok"))
    unknown_model = client.post(path, json={"message": "hi", "model": "gpt-9"})
    blank_message = client.post(path, json={"message": "   "})
    assert unknown_model.status_code == 422
    assert blank_message.status_code == 422
    assert unknown_model.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("path", TASK_PATHS)
def test_task_timeout_is_504(path: str) -> None:
    response = client_with(timing_out(), generation_timeout_seconds=1).post(path, json={"message": "hi"})
    assert response.status_code == 504
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "generation_timeout"
    assert body["content"] == ""


@pytest.mark.parametrize("path", TASK_PATHS)
def test_task_failure_is_502_and_hides_internal_detail(path: str) -> None:
    response = client_with(crashing()).post(path, json={"message": "hi"})
    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert "internal-detail-99" not in response.text


@pytest.mark.parametrize("path", TASK_PATHS + [CHAT_PATH])
def test_every_chat_style_endpoint_returns_the_same_shape(path: str) -> None:
    response = client_with(replying("ok")).post(path, json={"message": "hi"})
    assert response.status_code == 200
    assert set(response.json()) == CONTRACT_KEYS


def test_tasks_and_chat_share_conversation_context() -> None:
    provider = replying("ok")
    client = client_with(provider)
    first = client.post("/api/write", json={"message": "draft intro"}).json()
    client.post(
        CHAT_PATH,
        json={"conversation_id": first["conversation_id"], "message": "now shorter"},
    )
    roles = [m["role"] for m in provider.calls[1]]
    assert roles == ["system", "user", "assistant", "user"]
    assert provider.calls[1][-1]["content"] == "now shorter"


def test_task_model_is_optional_and_defaults_to_rapid() -> None:
    body = client_with(replying("ok")).post("/api/code", json={"message": "fix this"}).json()
    assert body["model"] == "rapid"


def test_tasks_are_listed_in_the_contract() -> None:
    endpoints = build_contract()["endpoints"]
    for spec in TASK_SPECS:
        assert endpoints[f"POST {spec.path}"]["mode"] == spec.mode
