import pytest
from pydantic import ValidationError

from backend.config import (
    DEFAULT_MODELS,
    DEFAULT_MODES,
    MAX_MESSAGE_CHARS,
    MAX_REPLY_CHARS,
)
from backend.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStatus,
    ErrorCode,
    ErrorDetail,
)
from backend.schemas.options import configure_allowed_options


@pytest.fixture(autouse=True)
def _default_options() -> None:
    configure_allowed_options(DEFAULT_MODELS, DEFAULT_MODES)


def _req(**overrides) -> dict:
    data = {"message": "hi", "model": "rapid"}
    data.update(overrides)
    return data


# ---- request: required fields, defaults, types ---------------------------------

def test_minimal_request_gets_defaults() -> None:
    req = ChatRequest.model_validate(_req())
    assert req.mode == "standard"
    assert req.conversation_id is None


def test_message_is_required_and_model_defaults_to_rapid() -> None:
    with pytest.raises(ValidationError) as missing_message:
        ChatRequest.model_validate({"model": "rapid"})
    assert missing_message.value.errors()[0]["loc"] == ("message",)
    assert ChatRequest.model_validate({"message": "hi"}).model is None


@pytest.mark.parametrize("bad", [123, None, ["hi"], {"text": "hi"}, True])
def test_message_must_be_a_string(bad: object) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(message=bad))


def test_model_and_mode_must_be_strings() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(model=1))
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(mode=["standard"]))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(temperature=0.2))


# ---- request: message length and content --------------------------------------

def test_message_is_trimmed() -> None:
    assert ChatRequest.model_validate(_req(message="  hi  ")).message == "hi"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_message_is_rejected(blank: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(message=blank))


def test_message_length_boundary() -> None:
    assert len(ChatRequest.model_validate(_req(message="a" * MAX_MESSAGE_CHARS)).message) == MAX_MESSAGE_CHARS
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(message="a" * (MAX_MESSAGE_CHARS + 1)))


def test_message_rejects_control_characters_but_allows_newlines() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(message="hi\x00there"))
    assert ChatRequest.model_validate(_req(message="line1\nline2\tx")).message == "line1\nline2\tx"


# ---- request: allowed values --------------------------------------------------

def test_model_must_be_in_allowlist_and_error_lists_allowed_values() -> None:
    with pytest.raises(ValidationError) as info:
        ChatRequest.model_validate(_req(model="nope"))
    message = info.value.errors()[0]["msg"]
    assert "Allowed values" in message
    assert "rapid" in message


@pytest.mark.parametrize("model", ["Fast", "fast model", "-fast", "x" * 65, ""])
def test_model_name_format_is_enforced(model: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(model=model))


def test_mode_must_be_in_allowlist() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(mode="nope"))


def test_custom_allowlist_replaces_defaults() -> None:
    configure_allowed_options(("alpha",), ("standard",))
    assert ChatRequest.model_validate(_req(model="alpha")).model == "alpha"
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(model="rapid"))


# ---- request: conversation_id -------------------------------------------------

@pytest.mark.parametrize("cid", ["abc-123_X", "0" * 32, "a" * 64])
def test_valid_conversation_ids(cid: str) -> None:
    assert ChatRequest.model_validate(_req(conversation_id=cid)).conversation_id == cid


@pytest.mark.parametrize("cid", ["", "has space", "a" * 65, "a/b", 42])
def test_invalid_conversation_ids(cid: object) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(conversation_id=cid))


# ---- response -----------------------------------------------------------------

def test_success_response_shape() -> None:
    response = ChatResponse(
        conversation_id="c1", model="rapid", mode="standard",
        content="hi", status=ChatStatus.COMPLETED,
    )
    assert response.error is None


def test_error_response_requires_error_and_empty_content() -> None:
    detail = ErrorDetail(code=ErrorCode.GENERATION_FAILED, message="failed")
    ok = ChatResponse(
        conversation_id="c1", model="rapid", mode="standard",
        content="", status=ChatStatus.ERROR, error=detail,
    )
    assert ok.status is ChatStatus.ERROR
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1", model="rapid", mode="standard",
            content="", status=ChatStatus.ERROR, error=None,
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1", model="rapid", mode="standard",
            content="partial", status=ChatStatus.ERROR, error=detail,
        )


def test_success_response_rejects_error_field() -> None:
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1", model="rapid", mode="standard", content="hi",
            status=ChatStatus.COMPLETED,
            error=ErrorDetail(code=ErrorCode.INTERNAL_ERROR, message="x"),
        )


def test_response_content_length_limit() -> None:
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1", model="rapid", mode="standard",
            content="a" * (MAX_REPLY_CHARS + 1), status=ChatStatus.COMPLETED,
        )
