"""Attachment contract: the wire shape and limits for files sent with a message.

These limits are part of the API contract, not deployment settings. The frontend must
enforce the same limits, so they are fixed in code and published in the contract file.

Attachments are text only. Content is sent to the model for the turn that carries it.
Conversation history keeps only AttachmentMeta, never the content.
"""
from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from pydantic_core import PydanticCustomError

from backend.schemas.common import contains_unsafe_character

ATTACHMENT_MAX_COUNT = 3
ATTACHMENT_MAX_CHARS = 64_000
ATTACHMENTS_MAX_TOTAL_CHARS = 128_000
ATTACHMENT_NAME_MAX_LENGTH = 255
ATTACHMENT_MEDIA_TYPES: tuple[str, ...] = (
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
)

AttachmentMediaType = Literal["text/plain", "text/markdown", "text/csv", "application/json"]

# Characters Windows and other platforms refuse in file names, plus path separators.
ATTACHMENT_NAME_FORBIDDEN_CHARACTERS = '/\\<>:"|?*'
_FORBIDDEN_NAME_CHARACTERS = frozenset(ATTACHMENT_NAME_FORBIDDEN_CHARACTERS)


def _check_name(value: str) -> str:
    if value in (".", ".."):
        raise PydanticCustomError("invalid_attachment_name", "Name must not be '.' or '..'.")
    for char in value:
        # Format characters (zero-width, bidi overrides) can disguise a name, so names reject them too.
        if char in _FORBIDDEN_NAME_CHARACTERS or unicodedata.category(char) in ("Cc", "Cs", "Cf"):
            raise PydanticCustomError(
                "invalid_attachment_name",
                "Name must not contain / \\ < > : \" | ? * or control or invisible formatting characters.",
            )
    return value


def _check_text(value: str) -> str:
    if contains_unsafe_character(value):
        raise PydanticCustomError(
            "unsafe_character",
            "Content must not contain control characters or bidirectional overrides. "
            "Newline, tab, and carriage return are allowed.",
        )
    return value


AttachmentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=ATTACHMENT_NAME_MAX_LENGTH),
    AfterValidator(_check_name),
]


class Attachment(BaseModel):
    """A text file sent with a message. Content is sent to the model for that turn only.

    The page sends `type` (the browser's media type), `size`, and `kind`. Those names are accepted.
    `size` and `kind` are informational and are not trusted: the size is measured from the content.
    """

    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)

    name: AttachmentName = Field(description="File name, for example notes.md.")
    media_type: AttachmentMediaType = Field(
        validation_alias=AliasChoices("media_type", "type"),
        description="The file's media type.",
    )
    size: int | None = Field(default=None, ge=0, description="Informational. Not trusted.")
    kind: str | None = Field(default=None, max_length=32, description="Informational. Not used.")
    content: str = Field(
        min_length=1,
        max_length=ATTACHMENT_MAX_CHARS,
        description="The file's text, UTF-8. At most 64000 characters.",
    )

    @field_validator("content")
    @classmethod
    def _content_is_valid(cls, value: str, info) -> str:
        _check_text(value)
        if info.data.get("media_type") == "application/json":
            try:
                json.loads(value)
            except (ValueError, RecursionError) as exc:
                raise PydanticCustomError(
                    "invalid_json", "Content must be valid JSON for application/json."
                ) from exc
        return value


class AttachmentMeta(BaseModel):
    """What conversation history keeps about an attachment. The content is not kept."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: AttachmentName
    media_type: AttachmentMediaType
    # UTF-8 uses at most 4 bytes per character.
    size_bytes: int = Field(
        ge=1,
        le=ATTACHMENT_MAX_CHARS * 4,
        description="Size of the content in UTF-8 bytes.",
    )

    @classmethod
    def from_attachment(cls, attachment: Attachment) -> AttachmentMeta:
        return cls(
            name=attachment.name,
            media_type=attachment.media_type,
            size_bytes=len(attachment.content.encode("utf-8")),
        )


def check_attachment_list(attachments: list[Attachment]) -> list[Attachment]:
    """Checks the per-request total. Count and per-item limits are enforced by the field."""
    total = sum(len(attachment.content) for attachment in attachments)
    if total > ATTACHMENTS_MAX_TOTAL_CHARS:
        raise ValueError(
            f"attachments together must not exceed {ATTACHMENTS_MAX_TOTAL_CHARS} characters"
        )
    return attachments
