"""Request schema: required fields, lengths, model and mode values, attachment
metadata, empty messages, and invalid types. Each failure is checked by its stable type."""
import pytest
from pydantic import ValidationError

from backend.config import DEFAULT_MODELS, DEFAULT_MODES
from backend.schemas.attachments import AttachmentMeta
from backend.schemas.options import configure_allowed_options
from backend.schemas.request import ChatRequest, TaskRequest


@pytest.fixture(autouse=True)
def _defaults() -> None:
    configure_allowed_options(DEFAULT_MODELS, DEFAULT_MODES)


def _errors(model, data) -> list[dict]:
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return exc.errors()
    raise AssertionError("expected a validation error")


def _types(model, data) -> list[tuple[tuple, str]]:
    return [(tuple(e["loc"]), e["type"]) for e in _errors(model, data)]


# ---- required fields ------------------------------------------------------------------

def test_message_is_required() -> None:
    assert _types(ChatRequest, {"model": "rapid"}) == [(("message",), "missing")]


def test_optional_fields_have_documented_defaults() -> None:
    req = ChatRequest.model_validate({"message": "hi"})
    assert req.conversation_id is None
    assert req.model is None  # the server resolves None to rapid
    assert req.mode == "general"
    assert req.attachments == []


def test_null_is_accepted_for_nullable_fields_only() -> None:
    assert ChatRequest.model_validate({"message": "hi", "conversation_id": None, "model": None}).model is None
    assert _types(ChatRequest, {"message": None}) == [(("message",), "string_type")]


# ---- empty messages -----------------------------------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\n\t \r\n"])
def test_blank_messages_get_a_specific_error(blank: str) -> None:
    errors = _errors(ChatRequest, {"message": blank})
    assert errors[0]["type"] == "empty_message"
    assert errors[0]["msg"] == "Message must not be empty."


def test_message_is_trimmed_and_kept() -> None:
    assert ChatRequest.model_validate({"message": "  hi  "}).message == "hi"


# ---- string lengths -----------------------------------------------------------------

def test_message_length_boundary() -> None:
    assert len(ChatRequest.model_validate({"message": "a" * 8000}).message) == 8000
    assert _types(ChatRequest, {"message": "a" * 8001}) == [(("message",), "string_too_long")]


def test_conversation_id_length_boundary() -> None:
    assert ChatRequest.model_validate({"message": "x", "conversation_id": "a" * 64}).conversation_id
    assert _types(ChatRequest, {"message": "x", "conversation_id": "a" * 65}) == [
        (("conversation_id",), "string_too_long")
    ]


def test_conversation_id_format_is_enforced() -> None:
    assert _types(ChatRequest, {"message": "x", "conversation_id": "has space"})[0][1] == "string_pattern_mismatch"


def test_model_name_length_is_enforced_after_the_format_check() -> None:
    assert _errors(ChatRequest, {"message": "x", "model": "a" * 65})[0]["type"] == "invalid_name"


# ---- model and mode values -----------------------------------------------------------

def test_unknown_model_lists_the_allowed_values() -> None:
    error = _errors(ChatRequest, {"message": "x", "model": "gpt-9"})[0]
    assert error["type"] == "unknown_model"
    assert "brainstormer, mythical, sonet, rapid" in error["msg"]


@pytest.mark.parametrize("model", ["Fast", "rapid model", "-rapid", ""])
def test_malformed_model_names_get_a_plain_explanation(model: str) -> None:
    error = _errors(ChatRequest, {"message": "x", "model": model})[0]
    assert error["type"] == "invalid_name"
    assert "lowercase letters" in error["msg"]


def test_unknown_mode_lists_the_allowed_values() -> None:
    error = _errors(ChatRequest, {"message": "x", "mode": "poetry"})[0]
    assert error["type"] == "unknown_mode"
    assert "general" in error["msg"] and "writing" in error["msg"]


def test_task_requests_carry_an_optional_mode_that_the_endpoint_checks() -> None:
    assert TaskRequest.model_validate({"message": "x"}).mode is None
    assert TaskRequest.model_validate({"message": "x", "mode": "coding"}).mode == "coding"


# ---- attachment metadata ----------------------------------------------------------------

def test_attachment_list_accepts_three_and_rejects_four() -> None:
    item = {"name": "a.txt", "media_type": "text/plain", "content": "x"}
    assert len(ChatRequest.model_validate({"message": "x", "attachments": [item] * 3}).attachments) == 3
    assert _types(ChatRequest, {"message": "x", "attachments": [item] * 4}) == [
        (("attachments",), "too_long")
    ]


def test_attachment_name_errors_point_at_the_attachment() -> None:
    data = {"message": "x", "attachments": [{"name": "a/b", "media_type": "text/plain", "content": "x"}]}
    assert _types(ChatRequest, data) == [(("attachments", 0, "name"), "invalid_attachment_name")]


def test_attachment_media_type_must_be_allowed() -> None:
    data = {"message": "x", "attachments": [{"name": "a", "media_type": "image/png", "content": "x"}]}
    assert _types(ChatRequest, data) == [(("attachments", 0, "media_type"), "literal_error")]


def test_attachment_content_cannot_be_blank() -> None:
    data = {"message": "x", "attachments": [{"name": "a", "media_type": "text/plain", "content": ""}]}
    assert _types(ChatRequest, data) == [(("attachments", 0, "content"), "string_too_short")]


def test_attachment_metadata_size_must_be_positive_and_bounded() -> None:
    with pytest.raises(ValidationError):
        AttachmentMeta(name="a", media_type="text/plain", size_bytes=0)
    with pytest.raises(ValidationError):
        AttachmentMeta(name="a", media_type="text/plain", size_bytes=10**9)


def test_attachment_metadata_name_uses_the_same_rules_as_requests() -> None:
    with pytest.raises(ValidationError):
        AttachmentMeta(name="../etc", media_type="text/plain", size_bytes=5)


def test_attachment_metadata_is_strict() -> None:
    with pytest.raises(ValidationError):
        AttachmentMeta(name="a", media_type="text/plain", size_bytes="5")


# ---- invalid types -----------------------------------------------------------------

@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("message", 123, "string_type"),
        ("message", ["hi"], "string_type"),
        ("message", True, "string_type"),
        ("model", 5, "string_type"),
        ("mode", {"name": "coding"}, "string_type"),
        ("conversation_id", 42, "string_type"),
        ("attachments", "a.txt", "list_type"),
        ("attachments", [42], "model_type"),
    ],
)
def test_wrong_types_are_rejected_with_a_type_error(field: str, value, expected: str) -> None:
    data = {"message": "x", field: value} if field != "message" else {"message": value}
    errors = _types(ChatRequest, data)
    assert any(loc[0] == field and error_type == expected for loc, error_type in errors), errors


def test_unknown_request_fields_are_rejected() -> None:
    assert _types(ChatRequest, {"message": "x", "temperature": 0.2}) == [(("temperature",), "extra_forbidden")]


def test_a_valid_request_has_no_errors() -> None:
    ChatRequest.model_validate(
        {
            "message": "summarise",
            "model": "sonet",
            "mode": "research",
            "conversation_id": "abc-123",
            "attachments": [{"name": "n.md", "media_type": "text/markdown", "content": "# n"}],
        }
    )


def test_attachment_json_errors_have_a_stable_type() -> None:
    data = {"message": "x", "attachments": [{"name": "a.json", "media_type": "application/json", "content": "{"}]}
    assert _types(ChatRequest, data) == [(("attachments", 0, "content"), "invalid_json")]


def test_attachment_control_characters_have_a_stable_type() -> None:
    data = {"message": "x", "attachments": [{"name": "a", "media_type": "text/plain", "content": "bad\x00"}]}
    assert _types(ChatRequest, data) == [(("attachments", 0, "content"), "unsafe_character")]
