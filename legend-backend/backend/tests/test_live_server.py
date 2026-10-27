"""Real HTTP server: Uvicorn on a socket, real Server-Sent Events, real stop, real headers."""
import json
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import uvicorn

from backend.tests.fakes import app_with, word_by_word


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _LiveServer:
    def __init__(self, app) -> None:
        self.port = _free_port()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_config=None,
            access_log=False,
            server_header=False,  # the deployment setting: --no-server-header
            lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "_LiveServer":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _sse_events(lines: Iterator[str]) -> Iterator[dict]:
    data = None
    for line in lines:
        if line.startswith("data: "):
            data = json.loads(line[len("data: "):])
        elif line == "" and data is not None:
            yield data
            data = None


def test_real_stream_delivers_deltas_incrementally_and_stop_keeps_the_partial_reply() -> None:
    app = app_with(word_by_word(" ".join(["word"] * 200), delay=0.02))
    with _LiveServer(app) as live, httpx.Client(base_url=live.base, timeout=15) as client:
        with client.stream("POST", "/api/chat/stream", json={"message": "go"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _sse_events(response.iter_lines())
            start = next(events)
            assert start["type"] == "start"
            first_delta = next(events)
            assert first_delta["type"] == "delta"  # arrived while generation was still running

            stop = client.post(f"/api/chat/{start['request_id']}/stop")
            assert stop.status_code == 200
            rest = list(events)

    done = rest[-1]
    assert done["type"] == "done"
    assert done["response"]["status"] == "stopped"
    assert done["response"]["content"].strip() != ""
    assert len(rest) < 150  # stopped long before the 200 words finished


def test_real_server_sends_no_server_header_and_keeps_security_headers() -> None:
    app = app_with(word_by_word("a"))
    with _LiveServer(app) as live, httpx.Client(base_url=live.base, timeout=10) as client:
        response = client.get("/api/models")
    assert response.status_code == 200
    assert "server" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


def test_real_server_rejects_an_oversize_chunked_body() -> None:
    app = app_with(word_by_word("a"), max_request_bytes=2048)
    with _LiveServer(app) as live, httpx.Client(base_url=live.base, timeout=10) as client:
        chunks = (b"x" * 1000 for _ in range(5))
        response = client.post("/api/chat", content=chunks, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
