"""Attachment contract: types, limits, defaults, error formats, and flow behaviour."""
import json

import pytest
from pydantic import ValidationError

from backend.prompts.system import render_turn
from backend.schemas.attachments import (
    ATTACHMENT_MAX_CHARS,
    ATTACHMENT_MAX_COUNT,
    ATTACHMENTS_MAX_TOTAL_CHARS,
    Attachment,
    AttachmentMeta,
)
from backend.schemas.contract import build_contract
from backend.schemas.request import ChatRequest, TaskRequest
from backend.tests.fakes import client_with, replying

GOOD = {"name": "notes.md", "media_type": "text/markdown", "content": "# Title\nbody"}


def _req(**overrides) -> dict:
    data = {"message": "summarise", "model": "rapid"}
    data.update(overrides)
    return data


def _att(**overrides) -> dict:
    data = dict(GOOD)
    data.update(overrides)
    return data


# ---- defaults and the valid shape ----------------------------------------------------

def test_attachments_default_to_an_empty_list() -> None:
    assert ChatRequest.model_validate(_req()).attachments == []
    assert TaskRequest.model_validate({"message": "x"}).attachments == []


def test_a_valid_attachment_is_accepted_with_its_fields() -> None:
    req = ChatRequest.model_validate(_req(attachments=[GOOD]))
    assert req.attachments[0].name == "notes.md"
    assert req.attachments[0].media_type == "text/markdown"
    assert req.attachments[0].content == "# Title\nbody"


@pytest.mark.parametrize("media_type", ["text/plain", "text/markdown", "text/csv", "application/json"])
def test_each_allowed_media_type_is_accepted(media_type: str) -> None:
    content = '{"ok": true}' if media_type == "application/json" else "plain text"
    assert Attachment.model_validate(_att(media_type=media_type, content=content)).media_type == media_type


# ---- media types and content --------------------------------------------------------

@pytest.mark.parametrize("media_type", ["image/png", "application/pdf", "text/html", "TEXT/PLAIN", ""])
def test_unsupported_media_types_are_rejected(media_type: str) -> None:
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(media_type=media_type))


def test_json_content_must_parse() -> None:
    with pytest.raises(ValidationError) as info:
        Attachment.model_validate(_att(media_type="application/json", content="{not json"))
    assert "valid JSON" in str(info.value)


def test_json_nesting_too_deep_is_a_validation_error_not_a_crash() -> None:
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(media_type="application/json", content="[" * 60_000))


@pytest.mark.parametrize("content", ["", "bad\x00byte", "bell\x07", "lone\ud800surrogate"])
def test_empty_or_control_character_content_is_rejected(content: str) -> None:
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(content=content))


def test_newlines_tabs_and_carriage_returns_are_allowed_in_content() -> None:
    assert Attachment.model_validate(_att(content="a\tb\r\nc")).content == "a\tb\r\nc"


def test_content_at_the_per_file_limit_is_accepted_and_one_more_is_not() -> None:
    assert len(Attachment.model_validate(_att(content="a" * ATTACHMENT_MAX_CHARS)).content) == ATTACHMENT_MAX_CHARS
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(content="a" * (ATTACHMENT_MAX_CHARS + 1)))


# ---- names -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name", ["a/b.txt", "..", ".", "c:\\x.txt", "bad|name.txt", "what?.txt", "tab\tname.txt", "x" * 256, "   "]
)
def test_unsafe_or_oversized_names_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(name=name))


def test_names_are_trimmed_and_may_contain_spaces_and_unicode() -> None:
    assert Attachment.model_validate(_att(name="  résumé v2.md  ")).name == "résumé v2.md"


# ---- counts and totals -------------------------------------------------------------

def test_three_attachments_are_the_maximum() -> None:
    assert len(ChatRequest.model_validate(_req(attachments=[GOOD] * ATTACHMENT_MAX_COUNT)).attachments) == 3
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(_req(attachments=[GOOD] * (ATTACHMENT_MAX_COUNT + 1)))


def test_total_characters_across_attachments_are_capped() -> None:
    per_file = 50_000  # each file is under the per-file limit; together they exceed the total
    big = [_att(content="a" * per_file) for _ in range(3)]
    assert per_file * 3 > ATTACHMENTS_MAX_TOTAL_CHARS
    with pytest.raises(ValidationError) as info:
        ChatRequest.model_validate(_req(attachments=big))
    assert "128000" in str(info.value)


def test_total_at_the_limit_is_accepted() -> None:
    files = [_att(content="a" * 64_000), _att(content="a" * 64_000)]
    assert sum(len(a.content) for a in ChatRequest.model_validate(_req(attachments=files)).attachments) == 128_000


# ---- strictness ----------------------------------------------------------------------

def test_unknown_attachment_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(url="https://example.com/x"))


def test_content_must_be_a_string() -> None:
    with pytest.raises(ValidationError):
        Attachment.model_validate(_att(content=123))


# ---- HTTP: error format and field paths --------------------------------------------

def test_http_validation_errors_use_the_documented_field_path() -> None:
    client = client_with(replying("ok"))
    response = client.post(
        "/api/chat",
        json=_req(attachments=[GOOD, _att(media_type="image/png")]),
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    fields = [issue["field"] for issue in body["error"]["details"]]
    assert "body.attachments.1.media_type" in fields


def test_http_rejects_attachment_content_that_is_not_json_for_json_type() -> None:
    client = client_with(replying("ok"))
    response = client.post(
        "/api/research",
        json={"message": "x", "attachments": [_att(media_type="application/json", content="{")]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"].startswith("body.attachments.0")


def test_error_messages_do_not_echo_attachment_content() -> None:
    client = client_with(replying("ok"))
    secret = "SECRET-PAYLOAD-DO-NOT-ECHO"
    response = client.post(
        "/api/chat",
        json=_req(attachments=[_att(media_type="image/png", content=secret)]),
    )
    assert response.status_code == 422
    assert secret not in response.text


# ---- all three task endpoints and chat accept attachments --------------------------

@pytest.mark.parametrize("path", ["/api/chat", "/api/research", "/api/code", "/api/write"])
def test_every_chat_style_endpoint_accepts_attachments(path: str) -> None:
    response = client_with(replying("ok")).post(path, json={"message": "read it", "attachments": [GOOD]})
    assert response.status_code == 200
    assert response.json()["status"] == "success"


# ---- flow: the model receives content; history keeps metadata only -----------------

def test_the_model_receives_the_attachment_text_for_the_turn() -> None:
    provider = replying("ok")
    client = client_with(provider)
    client.post("/api/chat", json=_req(attachments=[GOOD]))
    last_user = provider.calls[-1][-1]["content"]
    assert "[Attachment 1: notes.md (text/markdown, " in last_user
    assert "# Title\nbody" in last_user
    assert "[End of attachment 1]" in last_user


def test_history_keeps_attachment_metadata_but_not_content() -> None:
    client = client_with(replying("ok"))
    created = client.post("/api/conversations", json={}).json()["id"]
    client.post("/api/chat", json=_req(conversation_id=created, attachments=[GOOD]))
    stored = client.get(f"/api/conversations/{created}").json()["messages"][0]
    assert stored["content"] == "summarise"
    assert stored["attachments"] == [
        {"name": "notes.md", "media_type": "text/markdown", "size_bytes": len(b"# Title\nbody")}
    ]
    assert "# Title" not in json.dumps(stored)


def test_attachment_content_is_not_sent_on_later_turns() -> None:
    provider = replying("ok")
    client = client_with(provider)
    first = client.post("/api/chat", json=_req(attachments=[GOOD])).json()["conversation_id"]
    client.post("/api/chat", json={"conversation_id": first, "message": "follow up"})
    later = provider.calls[-1]
    assert all("# Title" not in message["content"] for message in later)


def test_render_turn_with_no_attachments_is_the_message_unchanged() -> None:
    assert render_turn("hello", []) == "hello"


def test_attachment_meta_size_is_utf8_bytes_not_characters() -> None:
    meta = AttachmentMeta.from_attachment(Attachment.model_validate(_att(content="é")))
    assert meta.size_bytes == 2


# ---- contract agreement ---------------------------------------------------------------

def test_contract_publishes_the_attachment_limits_from_the_code() -> None:
    contract = json.loads(json.dumps(build_contract()))
    assert contract["limits"]["attachments_max_count"] == ATTACHMENT_MAX_COUNT
    assert contract["limits"]["attachment_max_chars"] == ATTACHMENT_MAX_CHARS
    assert contract["limits"]["attachments_max_total_chars"] == ATTACHMENTS_MAX_TOTAL_CHARS
    assert contract["defaults"]["attachments"] == []
    assert "http" in contract["error_formats"] and "chat_outcome" in contract["error_formats"]


def test_worst_case_attachment_payload_fits_the_default_body_limit() -> None:
    """Two full files of supplementary characters, JSON-escaped by a client that uses
    ensure_ascii (each emoji costs 12 bytes on the wire). This is the largest legal request."""
    emoji_file = {"name": "big.txt", "media_type": "text/plain", "content": "\U0001F642" * 64_000}
    body = json.dumps({"message": "x" * 8000, "attachments": [emoji_file, {**emoji_file, "name": "b.txt"}]})
    assert len(body.encode("utf-8")) > 1024 * 1024  # larger than the old 1 MiB default
    response = client_with(replying("ok")).post(
        "/api/chat", content=body.encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
