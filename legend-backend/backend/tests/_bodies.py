"""Reads an error body without assuming which shape it has. Chat endpoints answer with a
ChatResponse, every other endpoint with an ErrorEnvelope. The helper says which one it found."""
from __future__ import annotations

from typing import Any


def error_of(response: Any) -> dict[str, Any]:
    body = response.json()
    if isinstance(body, dict) and "error" in body and isinstance(body["error"], dict):
        return body["error"]
    raise AssertionError(f"not an error body: {body!r}")


def is_chat_shape(response: Any) -> bool:
    body = response.json()
    return isinstance(body, dict) and "status" in body and "content" in body


def code_of(response: Any) -> str:
    return error_of(response)["code"]


def field_paths(response: Any) -> list[str]:
    return [issue["field"] for issue in (error_of(response).get("details") or [])]


def parse_error_body(text: str):
    """Parse an error body as the shape it actually has, and return the validated model.

    Both models expose .error, so a test can check the code and message without knowing
    which endpoint produced the body. The model itself is still strict."""
    import json

    from backend.schemas.errors import ErrorEnvelope
    from backend.schemas.response import ChatResponse

    body = json.loads(text)
    if isinstance(body, dict) and "status" in body and "content" in body:
        return ChatResponse.model_validate_json(text)
    return ErrorEnvelope.model_validate_json(text)
