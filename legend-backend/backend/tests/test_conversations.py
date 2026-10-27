"""Conversation endpoints and the chat endpoints' use of conversation ids."""
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.tests.fakes import client_with, replying


def _client() -> TestClient:
    return client_with(replying("reply"))


def test_create_read_and_chat_round_trip() -> None:
    client = _client()
    created = client.post("/api/conversations", json={"title": "Trip"})
    assert created.status_code == 201
    body = created.json()
    assert body["version"] == 1 and body["title"] == "Trip" and body["messages"] == []

    chat = client.post("/api/chat", json={"conversation_id": body["id"], "message": "hello"})
    assert chat.status_code == 200

    read = client.get(f"/api/conversations/{body['id']}").json()
    assert [m["role"] for m in read["messages"]] == ["user", "assistant"]
    assert read["messages"][0]["content"] == "hello"


def test_chat_with_unknown_conversation_id_is_not_found() -> None:
    response = _client().post("/api/chat", json={"conversation_id": "made-up-id", "message": "hi"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


def test_read_unknown_conversation_is_404_and_bad_id_is_422() -> None:
    client = _client()
    assert client.get("/api/conversations/not-real").status_code == 404
    bad = client.get("/api/conversations/has%20space")
    assert bad.status_code == 422


def test_update_title_requires_the_current_version() -> None:
    client = _client()
    cid = client.post("/api/conversations", json={}).json()["id"]
    ok = client.patch(f"/api/conversations/{cid}", json={"title": "Renamed", "expected_version": 1})
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    stale = client.patch(f"/api/conversations/{cid}", json={"title": "Stale", "expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_update_rejects_control_characters_and_long_titles() -> None:
    client = _client()
    cid = client.post("/api/conversations", json={}).json()["id"]
    bad = client.patch(f"/api/conversations/{cid}", json={"title": "a\x00b", "expected_version": 1})
    long = client.patch(f"/api/conversations/{cid}", json={"title": "x" * 81, "expected_version": 1})
    assert bad.status_code == 422
    assert long.status_code == 422


def test_clear_removes_messages_and_refuses_stale_versions() -> None:
    client = _client()
    cid = client.post("/api/conversations", json={}).json()["id"]
    client.post("/api/chat", json={"conversation_id": cid, "message": "hello"})
    before = client.get(f"/api/conversations/{cid}").json()
    stale = client.post(f"/api/conversations/{cid}/clear", json={"expected_version": 1})
    assert stale.status_code == 409

    cleared = client.post(
        f"/api/conversations/{cid}/clear", json={"expected_version": before["version"]}
    )
    assert cleared.status_code == 200
    assert cleared.json()["messages"] == []
    assert cleared.json()["version"] > before["version"]


def test_unknown_fields_are_rejected() -> None:
    client = _client()
    assert client.post("/api/conversations", json={"owner": "someone"}).status_code == 422


def test_conversation_limits_are_enforced_over_http() -> None:
    app = create_app(Settings(max_conversations=1, model_routes=()))
    client = TestClient(app)
    assert client.post("/api/conversations", json={}).status_code == 201
    # The only conversation is idle, so it is evicted rather than refusing the new one.
    assert client.post("/api/conversations", json={}).status_code == 201
