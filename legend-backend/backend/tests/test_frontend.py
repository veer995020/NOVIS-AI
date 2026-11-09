"""The chat page: how it is served, and the page's own tests run against live servers.

The page is frontend/chat.html, the file the user supplied, connected to the API. The Node tests in
frontend/tests drive that real page in jsdom against real servers started here. They are skipped,
with the reason printed, when Node or the frontend packages are missing. A skip is never a pass.
"""
import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Secret, Settings
from backend.main import create_app
from backend.tests.fakes import app_with, counting, echoing, flaky_then_echo, sleeping
from backend.tests.test_live_server import _LiveServer

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
CONTRACT_FILE = Path(__file__).resolve().parents[2] / "contracts" / "chat.contract.json"
CLOSED_URL = "http://127.0.0.1:9"  # nothing listens here


# ---- serving the page -------------------------------------------------------------------

def test_the_chat_page_is_served_at_app() -> None:
    client = TestClient(create_app(Settings()))
    page = client.get("/app/chat.html")
    assert page.status_code == 200
    assert 'id="composerInput"' in page.text
    assert "<script>" in page.text or "<script " in page.text


def test_files_outside_the_frontend_folder_are_not_served() -> None:
    client = TestClient(create_app(Settings()))
    assert client.get("/app/../backend/config.py").status_code == 404
    assert client.get("/app/..%2fbackend%2fconfig.py").status_code == 404
    assert client.get("/app/missing.html").status_code == 404


def test_the_page_and_contract_need_no_key_but_the_api_still_does() -> None:
    client = TestClient(app_with(echoing(), api_access_key=Secret("page-test-key-1")))
    assert client.get("/app/chat.html").status_code == 200
    assert client.get("/api/contract").status_code == 200
    assert client.get("/api/models").status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_the_page_can_be_switched_off() -> None:
    client = TestClient(create_app(Settings(serve_frontend=False)))
    assert client.get("/app/chat.html").status_code == 404
    assert client.get("/api/contract").status_code == 200


def test_the_contract_the_page_reads_matches_the_committed_file() -> None:
    contract = TestClient(create_app(Settings())).get("/api/contract").json()
    assert contract == json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))


# ---- the page's own tests, against live servers ----------------------------------------

def _node_suites_available() -> str | None:
    if shutil.which("node") is None:
        return "Node.js is not installed"
    if not (FRONTEND / "node_modules" / "jsdom").is_dir():
        return "frontend packages are not installed; run `npm install` in the frontend folder"
    return None


@pytest.mark.skipif(_node_suites_available() is not None, reason=_node_suites_available() or "")
def test_the_original_chat_page_works_against_live_servers() -> None:
    """Drives frontend/chat.html, unchanged, in jsdom against live servers. See the test file."""
    with contextlib.ExitStack() as stack:
        servers = {
            "ok": stack.enter_context(_LiveServer(app_with(echoing()))).base,
            "counting": stack.enter_context(_LiveServer(app_with(counting()))).base,
            "unrouted": stack.enter_context(_LiveServer(create_app(Settings()))).base,
            "slow": stack.enter_context(_LiveServer(app_with(sleeping("slow reply", 1.5)))).base,
            "flaky": stack.enter_context(_LiveServer(app_with(flaky_then_echo()))).base,
            "closed": CLOSED_URL,
        }
        env = {**os.environ, "SERVERS": json.dumps(servers)}
        node = shutil.which("node")
        assert node is not None  # guaranteed by the skip condition above
        result = subprocess.run(
            [node, "--test", "tests/original-page.test.mjs"],
            cwd=FRONTEND,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
    summary = {
        line.split()[1]: line.split()[2]
        for line in result.stdout.splitlines()
        if line.startswith("# ") and len(line.split()) == 3
    }
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert summary.get("fail") == "0", summary
    assert int(summary.get("pass", "0")) > 0, summary
