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
_FORMS: tuple[str, ...] = ()  # every registered credential, raw and JSON-escaped, longest first
_REPLACEMENT = "[redacted]"


def register_secret(value: str) -> None:
    """Add a credential to the set that is scrubbed from every log line. Call once at startup."""
    global _FORMS
    if value and value not in _SECRETS:
        _SECRETS.add(value)
        forms = {form for secret in _SECRETS for form in (secret, json.dumps(secret)[1:-1])}
        _FORMS = tuple(sorted(forms, key=len, reverse=True))


def registered_secrets() -> tuple[str, ...]:
    """The raw credentials registered at startup. Used by the reply scanner; never logged."""
    return tuple(_SECRETS)


def redact_text(text: str) -> str:
    """Replace every registered credential in a log line, including its JSON-escaped form."""
    for form in _FORMS:
        if form in text:
            text = text.replace(form, _REPLACEMENT)
    return text
