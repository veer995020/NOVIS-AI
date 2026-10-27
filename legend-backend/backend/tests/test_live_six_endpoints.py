"""The six required endpoints over a real socket, served by Uvicorn.

Success cases use a scripted provider that lives only in the test package. The
application registers no provider, so this is the only place a reply is produced.
"""
import httpx

from backend.tests.fakes import app_with, crashing, replying
from backend.tests.test_live_server import _LiveServer


def test_all_six_endpoints_succeed_over_a_real_socket() -> None:
    app = app_with(replying("live reply"))
    with _LiveServer(app) as live, httpx.Client(base_url=live.base, timeout=15) as client:
        chat = client.post("/api/chat", json={"message": "hello"})
        research = client.post("/api/research", json={"message": "compare A and B"})
        code = client.post("/api/code", json={"message": "fix this loop"})
        write = client.post("/api/write", json={"message": "tighten this sentence"})
        models = client.get("/api/models")
        health = client.get("/health")

    for response, mode in [(chat, "standard"), (research, "research"), (code, "code"), (write, "write")]:
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["content"] == "live reply"
        assert body["model"] == "rapid"
        assert body["mode"] == mode
        assert body["error"] is None

    assert models.status_code == 200
    assert any(m["id"] == "rapid" and m["available"] for m in models.json()["models"])

    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_all_six_endpoints_fail_honestly_over_a_real_socket() -> None:
    app = app_with(crashing())
    with _LiveServer(app) as live, httpx.Client(base_url=live.base, timeout=15) as client:
        responses = [
            client.post(path, json={"message": "hello"})
            for path in ["/api/chat", "/api/research", "/api/code", "/api/write"]
        ]
    for response in responses:
        assert response.status_code == 502
        body = response.json()
        assert body["status"] == "error"
        assert body["content"] == ""
        assert body["error"]["code"] == "generation_failed"
        assert "internal-detail-99" not in response.text
