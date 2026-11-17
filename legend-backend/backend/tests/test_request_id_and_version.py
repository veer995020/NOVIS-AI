"""The request id and the conversation version, as the release defines them, ported into the agreed contract.

request_id: the id of the request, the same as the X-Request-ID header, on every chat response.
conversation_version: the conversation's version after the turn. A saved reply increases it by one;
a failed or stopped turn that saves nothing leaves it unchanged.
"""
import re

from fastapi.testclient import TestClient

from backend.config import Secret
from backend.tests.fakes import app_with, echoing, flaky_then_echo

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_request_id_matches_the_header_on_a_success() -> None:
    response = TestClient(app_with(echoing())).post("/api/chat", json={"message": "hi"})
    body = response.json()
    assert HEX32.match(body["request_id"])
    assert body["request_id"] == response.headers["x-request-id"]


def test_request_id_is_present_on_a_refusal_before_any_turn() -> None:
    client = TestClient(app_with(echoing(), api_access_key=Secret("version-test-key-1")))
    response = client.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 401
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert response.json()["conversation_version"] is None


def test_a_validation_failure_has_a_request_id_and_no_version() -> None:
    response = TestClient(app_with(echoing())).post("/api/chat", json={"message": ""})
    assert response.status_code == 422
    assert HEX32.match(response.json()["request_id"])
    assert response.json()["conversation_version"] is None


def test_a_saved_reply_increases_the_conversation_version_by_one() -> None:
    client = TestClient(app_with(echoing()))
    created = client.post("/api/conversations", json={}).json()
    assert created["version"] == 1
    reply = client.post("/api/chat", json={"message": "hi", "conversation_id": created["id"]}).json()
    assert reply["conversation_version"] == 2
    assert client.get(f"/api/conversations/{created['id']}").json()["version"] == 2


def test_an_unknown_conversation_created_by_a_reply_reports_its_version() -> None:
    client = TestClient(app_with(echoing()))
    reply = client.post("/api/chat", json={"message": "hi", "conversation_id": "page-made-1"}).json()
    # Creation counts as version 1; the saved reply makes it 2.
    assert reply["conversation_version"] == 2


def test_a_failed_turn_saves_nothing_and_leaves_the_version_unchanged() -> None:
    client = TestClient(app_with(flaky_then_echo()))
    created = client.post("/api/conversations", json={}).json()
    failed = client.post("/api/chat", json={"message": "first", "conversation_id": created["id"]})
    assert failed.status_code == 502
    assert failed.json()["conversation_version"] == 1
    assert client.get(f"/api/conversations/{created['id']}").json()["version"] == 1
    ok = client.post("/api/chat", json={"message": "second", "conversation_id": created["id"]}).json()
    assert ok["conversation_version"] == 2
