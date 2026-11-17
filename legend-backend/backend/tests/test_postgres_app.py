"""The whole app with conversations in PostgreSQL. Needs TEST_DATABASE_URL; skipped otherwise."""
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.config import Secret
from backend.tests.fakes import app_with, counting, echoing

TEST_DSN = os.environ.get("TEST_DATABASE_URL")


def _dsn() -> str:
    """Only called from tests that the skip marker keeps away from a missing database."""
    assert TEST_DSN is not None
    return TEST_DSN
requires_db = pytest.mark.skipif(TEST_DSN is None, reason="TEST_DATABASE_URL not set; database tests skipped")


def _reset() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS messages, conversations CASCADE")


@requires_db
def test_a_conversation_and_its_history_survive_a_restart() -> None:
    _reset()
    first_app = app_with(counting(), database_url=Secret(_dsn()))
    with TestClient(first_app) as client:
        reply = client.post("/api/chat", json={"message": "remember this"}).json()
        conversation_id = reply["conversation_id"]
        assert reply["status"] == "success" and reply["conversation_version"] == 2

    # A new process: a new app, the same database.
    second_app = app_with(counting(), database_url=Secret(_dsn()))
    with TestClient(second_app) as client:
        stored = client.get(f"/api/conversations/{conversation_id}").json()
        assert stored["version"] == 2
        assert [m["content"] for m in stored["messages"]] == ["remember this", "2 messages in context"]
        follow = client.post("/api/chat", json={"message": "and again", "conversation_id": conversation_id}).json()
        # counting() reports the messages the model received: the system prompt, the stored turn, and this message.
        assert follow["content"] == "4 messages in context"
        assert follow["conversation_version"] == 3


@requires_db
def test_health_is_ok_with_a_reachable_database() -> None:
    _reset()
    with TestClient(app_with(echoing(), database_url=Secret(_dsn()))) as client:
        body = client.get("/health").json()
        assert body["checks"]["conversations"] == "ok" and body["status"] == "ok"


@requires_db
def test_health_reports_down_when_the_database_stops_answering(monkeypatch) -> None:
    _reset()
    app = app_with(echoing(), database_url=Secret(_dsn()))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

        async def lost_connection(*_args, **_kwargs):
            # The error a dropped connection raises. The health check must map it, not fail.
            raise psycopg.OperationalError("server closed the connection unexpectedly")

        monkeypatch.setattr(app.state.conversation_store, "count", lost_connection)
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["checks"]["conversations"] == "down"


def test_an_unreachable_database_stops_startup_without_revealing_the_password() -> None:
    password = "pw-never-print-me-7741"
    dsn = f"postgresql://postgres:{password}@127.0.0.1:1/novis"  # nothing listens on port 1
    app = app_with(echoing(), database_url=Secret(dsn))
    with pytest.raises(RuntimeError) as info:
        with TestClient(app):
            pass
    assert password not in str(info.value)
    assert "postgres:" not in str(info.value)


def test_a_non_postgres_url_is_refused_at_configuration_time() -> None:
    from backend.config import ConfigError, Settings, validate_settings

    with pytest.raises(ConfigError) as info:
        validate_settings(Settings(database_url=Secret("mysql://user:secret@host/db")))
    assert "secret" not in str(info.value)
    assert "postgresql://" in str(info.value)


@requires_db
def test_the_same_app_can_be_started_again_after_a_shutdown() -> None:
    _reset()
    app = app_with(echoing(), database_url=Secret(_dsn()))
    with TestClient(app) as client:
        assert client.get("/health").json()["checks"]["conversations"] == "ok"
    with TestClient(app) as client:
        assert client.get("/health").json()["checks"]["conversations"] == "ok"
