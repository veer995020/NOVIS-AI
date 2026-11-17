"""Input inspection. It flags, it does not block: the model is told to treat user and attachment text as
content, and the flags are the server's own record of what it saw."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from backend.reliability.secret_guard import find_secret_kinds
from backend.schemas.attachments import Attachment

# Phrases that try to change the model's instructions or reveal them. Matched case-insensitively.
INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(?:ignore|disregard|forget)\b.{0,40}\b(?:previous|prior|above|earlier|all)\b.{0,40}"
        r"\b(?:instructions?|prompts?|rules?)\b", re.IGNORECASE | re.DOTALL),
    "reveal_prompt": re.compile(
        r"\b(?:reveal|show|print|repeat|output|display)\b.{0,30}"
        r"\b(?:system prompt|your instructions|hidden prompt|developer message)\b", re.IGNORECASE | re.DOTALL),
    "role_override": re.compile(r"\b(?:you are now|pretend to be|developer mode|jailbreak)\b", re.IGNORECASE),
    "chat_markup": re.compile(r"<\|(?:im_start|im_end|system|endoftext)\|>|^\s*(?:system|assistant)\s*:",
                              re.IGNORECASE | re.MULTILINE),
}


@dataclass(frozen=True, slots=True)
class InputReport:
    injection_labels: tuple[str, ...]
    credential_kinds: tuple[str, ...]

    @property
    def injection_suspected(self) -> bool:
        return bool(self.injection_labels)


def inspect_input(message: str, attachments: Sequence[Attachment] = ()) -> InputReport:
    texts = [message, *(attachment.content for attachment in attachments)]
    labels = {label for text in texts for label, pattern in INJECTION_PATTERNS.items() if pattern.search(text)}
    credentials = {kind for text in texts for kind in find_secret_kinds(text)}
    return InputReport(tuple(sorted(labels)), tuple(sorted(credentials)))
