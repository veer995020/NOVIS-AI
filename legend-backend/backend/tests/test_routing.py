"""Routing: the exact route table, and agreement between the contract and the app."""
import json

from backend.config import Settings
from backend.main import create_app
from backend.schemas.contract import CONTRACT_PATH

EXPECTED_ROUTES = {
    ("POST", "/api/chat"),
    ("POST", "/api/chat/stream"),
    ("GET", "/api/chat/options"),
    ("POST", "/api/chat/{request_id}/stop"),
    ("POST", "/api/research"),
    ("POST", "/api/code"),
    ("POST", "/api/write"),
    ("GET", "/api/models"),
    ("GET", "/api/contract"),
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("POST", "/api/conversations"),
    ("GET", "/api/conversations/{conversation_id}"),
    ("PATCH", "/api/conversations/{conversation_id}"),
    ("POST", "/api/conversations/{conversation_id}/clear"),
}


def _app_routes(app) -> set[tuple[str, str]]:
    """The served route table, read from the OpenAPI document.

    FastAPI wraps included routers, so `app.routes` does not list APIRoute objects
    directly in current versions. The OpenAPI document is generated from the live
    routes, so it reflects what is actually served.
    """
    paths = app.openapi()["paths"]
    return {(method.upper(), path) for path, operations in paths.items() for method in operations}


def test_route_table_is_exactly_the_documented_set() -> None:
    assert _app_routes(create_app(Settings())) == EXPECTED_ROUTES


def test_docs_routes_only_exist_when_enabled() -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app(Settings())) as off:
        assert off.get("/docs").status_code == 404
        assert off.get("/openapi.json").status_code == 404
    with TestClient(create_app(Settings(enable_docs=True))) as on:
        assert on.get("/docs").status_code == 200
        assert on.get("/openapi.json").status_code == 200


def test_every_contract_endpoint_is_a_real_route() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract_routes = {tuple(key.split(" ", 1)) for key in contract["endpoints"]}
    assert contract_routes == EXPECTED_ROUTES


def test_the_six_required_endpoints_are_routed() -> None:
    routes = _app_routes(create_app(Settings()))
    for required in [
        ("POST", "/api/chat"),
        ("POST", "/api/research"),
        ("POST", "/api/code"),
        ("POST", "/api/write"),
        ("GET", "/api/models"),
        ("GET", "/health"),
    ]:
        assert required in routes
