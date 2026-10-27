"""Conversation reads: the most recent `limit` messages, with the total count."""

import pytest
from fastapi.testclient import TestClient

from backend.tests.fakes import app_with, replying


def _seeded_conversation(turns: int) -> tuple[TestClient, str]:
    client = TestClient(app_with(replying("answer")))
    cid = client.post("/api/conversations", json={}).json()["id"]
    for index in range(turns):
        client.post("/api/chat", json={"conversation_id": cid, "message": f"question {index}"})
    return client, cid


def test_default_read_returns_all_messages_and_their_count() -> None:
    client, cid = _seeded_conversation(3)
    body = client.get(f"/api/conversations/{cid}").json()
    assert body["message_count"] == 6
    assert len(body["messages"]) == 6


def test_limit_returns_the_most_recent_messages_oldest_first() -> None:
    client, cid = _seeded_conversation(3)
    body = client.get(f"/api/conversations/{cid}", params={"limit": 2}).json()
    assert body["message_count"] == 6
    assert [m["content"] for m in body["messages"]] == ["question 2", "answer"]


def test_limit_larger_than_history_returns_everything() -> None:
    client, cid = _seeded_conversation(1)
    body = client.get(f"/api/conversations/{cid}", params={"limit": 50}).json()
    assert len(body["messages"]) == 2


@pytest.mark.parametrize("limit", [0, -1, 201, "abc"])
def test_invalid_limits_are_validation_errors(limit) -> None:
    client, cid = _seeded_conversation(1)
    response = client.get(f"/api/conversations/{cid}", params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_empty_conversation_reports_zero() -> None:
    client = TestClient(app_with(replying("answer")))
    cid = client.post("/api/conversations", json={}).json()["id"]
    body = client.get(f"/api/conversations/{cid}", params={"limit": 5}).json()
    assert body["message_count"] == 0 and body["messages"] == []
