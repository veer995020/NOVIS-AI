"""Redaction for logs. Conversation ids and request ids are bearer-style values:
whoever holds one can read or stop that work. They must never reach a log line."""
from __future__ import annotations

import json
import re

_CONVERSATION_PATH = re.compile(r"^/api/conversations/[^/]+")
_STOP_PATH = re.compile(r"^/api/chat/[^/]+/stop$")


def safe_path(path: str) -> str:
    """Return the request path with conversation and request ids replaced by placeholders."""
    path = _CONVERSATION_PATH.sub("/api/conversations/{id}", path)
    return _STOP_PATH.sub("/api/chat/{request_id}/stop", path)


_SECRETS: set[str] = set()
_REPLACEMENT = "[redacted]"


def register_secret(value: str) -> None:
    """Add a credential to the set that is scrubbed from every log line. Call once at startup."""
    if value:
        _SECRETS.add(value)


def redact_text(text: str) -> str:
    """Replace every registered credential in a log line, including its JSON-escaped form."""
    for secret in sorted(_SECRETS, key=len, reverse=True):
        for form in {secret, json.dumps(secret)[1:-1]}:
            if form in text:
                text = text.replace(form, _REPLACEMENT)
    return text
