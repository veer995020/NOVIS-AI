"""Constrained types shared by request and response schemas."""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import StringConstraints
from pydantic_core import PydanticCustomError

CONVERSATION_ID_MAX_LENGTH = 64
CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
# Bidirectional overrides and isolates. They make text display differently from how it is stored.


# One pass over the text instead of one Unicode lookup per character. The class is: control characters
# (C0 and C1, except tab, newline, and return), unpaired surrogates (Python keeps a surrogate pair as one
# character, so any surrogate left in a string is unpaired), and the bidirectional overrides and isolates.
_UNSAFE_CHARACTERS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\u202a-\u202e\u2066-\u2069]"
)


def contains_unsafe_character(text: str) -> bool:
    return _UNSAFE_CHARACTERS.search(text) is not None


def is_unsafe_character(char: str) -> bool:
    """True for one character that is unsafe to store or display."""
    return _UNSAFE_CHARACTERS.fullmatch(char) is not None


def reject_unsafe_characters(value: str) -> str:
    """Refuse text with characters that are unsafe to store or display."""
    if contains_unsafe_character(value):
        raise PydanticCustomError(
            "unsafe_character",
            "Text must not contain control characters or bidirectional overrides. "
            "Newline, tab, and carriage return are allowed.",
        )
    return value


ConversationId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=CONVERSATION_ID_MAX_LENGTH,
        pattern=CONVERSATION_ID_PATTERN,
    ),
]
