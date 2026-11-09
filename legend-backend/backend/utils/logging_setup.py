"""Structured logging: one JSON object per line.

Only whitelisted fields are emitted, so stray record attributes never leak into
log output. Never pass credentials or message content as log fields.
"""
from __future__ import annotations

import json
import logging
import sys

from backend.utils.clock import utcnow
from backend.utils.redaction import redact_text

STRUCTURED_FIELDS = ("event", "request_id", "method", "path", "status_code", "duration_ms")


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": utcnow().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in STRUCTURED_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact_text(json.dumps(payload, default=str))


def configure_logging(level: str, log_format: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(PlainFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn through the same handler so every line has one format.
    # Request logging is done by our middleware, so silence uvicorn's access log.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.setLevel(logging.WARNING)
    access.propagate = True
