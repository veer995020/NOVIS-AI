"""Redaction for logs. Conversation ids and request ids are bearer-style values:
whoever holds one can read or stop that work. They must never reach a log line."""
from __future__ import annotations

import re

_CONVERSATION_PATH = re.compile(r"^/api/conversations/[^/]+")
_STOP_PATH = re.compile(r"^/api/chat/[^/]+/stop$")


def safe_path(path: str) -> str:
    """Return the request path with conversation and request ids replaced by placeholders."""
    path = _CONVERSATION_PATH.sub("/api/conversations/{id}", path)
    return _STOP_PATH.sub("/api/chat/{request_id}/stop", path)
