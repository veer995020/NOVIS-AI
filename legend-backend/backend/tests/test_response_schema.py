"""Response schema: the six fields, status rules, and strict structures across both modules."""
import importlib
import inspect
import json

import pytest
from pydantic import BaseModel, ValidationError

from backend.schemas.errors import ErrorDetail
from backend.schemas.response import ChatResponse, ChatStatus, Message

REQUEST_AND_RESPONSE_MODULES = [
    "backend.schemas.request",
    "backend.schemas.response",
    "backend.schemas.attachments",
    "backend.schemas.errors",
    "backend.schemas.common",
]


def _ok(**overrides) -> dict:
    data = {
        "conversation_id": "c1", "model": "sonet", "mode": "general",
        "content": "hi", "status": "success", "error": None,
    }
    data.update(overrides)
    return data


def _parse(data: dict) -> ChatResponse:
    """Parse as the API does: from JSON text, where the status is a plain string."""
    return ChatResponse.model_validate_json(json.dumps(data))


def test_response_has_exactly_the_six_contract_fields() -> None:
    assert set(ChatResponse.model_fields) == {"conversation_id", "model", "mode", "content", "status", "error", "detail"}


def test_all_six_fields_are_required_except_error() -> None:
    schema = ChatResponse.model_json_schema()
    assert set(schema["required"]) == {"conversation_id", "model", "mode", "content", "status", "error"}


def test_completed_response_has_null_error() -> None:
    assert _parse(_ok()).error is None


def test_error_status_requires_an_error_and_null_content() -> None:
    detail = {"code": "GENERATION_FAILED", "message": "failed", "details": None}
    assert _parse(_ok(status="error", content=None, error=detail)).status is ChatStatus.ERROR
    with pytest.raises(ValidationError) as missing:
        _parse(_ok(status="error", content=None, error=None))
    assert "error is required" in str(missing.value)
    with pytest.raises(ValidationError) as content:
        _parse(_ok(status="error", content="partial", error=detail))
    assert "content must be null" in str(content.value)


def test_non_error_status_rejects_an_error() -> None:
    detail = {"code": "GENERATION_FAILED", "message": "failed", "details": None}
    with pytest.raises(ValidationError) as info:
        _parse(_ok(error=detail))
    assert "error must be null" in str(info.value)


def test_status_must_be_one_of_the_three_values() -> None:
    with pytest.raises(ValidationError):
        _parse(_ok(status="done"))


def test_stopped_keeps_its_partial_content() -> None:
    assert _parse(_ok(status="stopped", content="partial")).content == "partial"


def test_content_has_an_upper_bound() -> None:
    with pytest.raises(ValidationError):
        _parse(_ok(content="a" * 100_001))


def test_error_detail_code_must_be_a_known_code() -> None:
    with pytest.raises(ValidationError):
        ErrorDetail.model_validate({"code": "made_up", "message": "x"})


def test_message_roles_are_fixed() -> None:
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "system", "content": "x"})


def _strict_models() -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    for module_name in REQUEST_AND_RESPONSE_MODULES:
        module = importlib.import_module(module_name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseModel) and obj.__module__ == module_name:
                found.append(obj)
    return found


@pytest.mark.parametrize("model", _strict_models(), ids=lambda m: m.__name__)
def test_every_request_and_response_model_is_strict_and_rejects_unknown_fields(model) -> None:
    """Consistency: one JSON policy across the whole contract."""
    config = model.model_config
    assert config.get("strict") is True, f"{model.__name__} is not strict"
    assert config.get("extra") == "forbid", f"{model.__name__} accepts unknown fields"


def test_validation_issues_carry_a_stable_type_in_the_contract() -> None:
    from backend.schemas.contract import build_contract

    issue = build_contract()["schemas"]["ErrorEnvelope"]["$defs"]["FieldIssue"]
    assert "type" in issue["properties"]
    assert set(issue["required"]) == {"field", "type", "message"}


def test_request_required_fields_in_the_contract() -> None:
    from backend.schemas.contract import build_contract

    schemas = build_contract()["schemas"]
    assert schemas["ChatRequest"]["required"] == ["message"]
    assert schemas["TaskRequest"]["required"] == ["message"]
