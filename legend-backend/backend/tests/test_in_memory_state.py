"""In-memory state: lifetime and isolation."""
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.tests.fakes import app_with, replying


def test_state_is_lost_when_the_app_restarts() -> None:
    first = TestClient(app_with(replying("ok")))
    conversation_id = first.post("/api/conversations", json={}).json()["id"]
    first.post("/api/chat", json={"conversation_id": conversation_id, "message": "hello"})
    assert len(first.get(f"/api/conversations/{conversation_id}").json()["messages"]) == 2

    restarted = TestClient(app_with(replying("ok")))
    assert restarted.get(f"/api/conversations/{conversation_id}").status_code == 404


def test_two_running_apps_do_not_share_state() -> None:
    a = TestClient(app_with(replying("ok")))
    b = TestClient(app_with(replying("ok")))
    cid = a.post("/api/conversations", json={"title": "only in a"}).json()["id"]
    assert b.get(f"/api/conversations/{cid}").status_code == 404
    assert a.get(f"/api/conversations/{cid}").json()["title"] == "only in a"


def test_a_failed_turn_leaves_no_state_behind() -> None:
    from backend.tests.fakes import crashing

    client = TestClient(app_with(crashing()))
    cid = client.post("/api/conversations", json={}).json()["id"]
    failed = client.post("/api/chat", json={"conversation_id": cid, "message": "lost"})
    assert failed.status_code == 502
    assert client.get(f"/api/conversations/{cid}").json()["messages"] == []


def test_default_app_has_no_conversations_at_startup() -> None:
    with TestClient(create_app(Settings())) as client:
        health = client.get("/health").json()
    assert health["checks"]["conversations"] == "ok"
