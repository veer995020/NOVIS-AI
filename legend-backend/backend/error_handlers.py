"""Central error handling. Two body shapes, chosen by the path:

  Chat endpoints (CHAT_PATHS): every outcome is a ChatResponse, including refusals and
      validation failures. status is "error", content is null, error has code and message.
  Everything else: the ErrorEnvelope, {"error": {...}, "request_id": "..."}.

Internal exception details are logged with the request ID and never returned. Validation
messages name the field and the rule, never the submitted value. A chat error echoes only
identifiers it has checked against the allowlists, so the echo cannot carry attacker text.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.config import DEFAULT_MODE, DEFAULT_MODEL, DEFAULT_MODELS
from backend.schemas.common import CONVERSATION_ID_MAX_LENGTH, CONVERSATION_ID_PATTERN
from backend.schemas.errors import ErrorCode, ErrorDetail, ErrorEnvelope, FieldIssue
from backend.schemas.response import CHAT_PATHS, ChatResponse, ChatStatus
from backend.services.errors import ConversationError
from backend.utils.redaction import safe_path

logger = logging.getLogger("backend.errors")

REQUEST_ID_HEADER = "X-Request-ID"

_CONVERSATION_ID = re.compile(CONVERSATION_ID_PATTERN)

_HTTP_CODES: dict[int, ErrorCode] = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    400: ErrorCode.BAD_REQUEST,
    403: ErrorCode.FORBIDDEN,
    401: ErrorCode.UNAUTHORIZED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    429: ErrorCode.RATE_LIMITED,
}

# The task endpoints fix their mode, so an error from them reports that mode.
_TASK_MODES: dict[str, str] = {
    "/api/research": "research", "/api/code": "coding", "/api/write": "writing",
    "/research": "research", "/code": "coding", "/write": "writing",
}


def build_error_response(
    status: int,
    code: ErrorCode,
    message: str,
    request_id: str | None,
    details: list[FieldIssue] | None = None,
    extra_headers: dict[str, str] | None = None,
    *,
    path: str | None = None,
    conversation_id: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> JSONResponse:
    error = ErrorDetail(code=code, message=message, details=details)
    if path is not None and path in CHAT_PATHS:
        body: dict[str, Any] = ChatResponse(
            conversation_id=conversation_id,
            model=model,
            mode=mode,
            content=None,
            status=ChatStatus.ERROR,
            error=error,
        ).model_dump(mode="json")
    else:
        body = ErrorEnvelope(error=error, request_id=request_id).model_dump(mode="json")
    headers = dict(extra_headers or {})
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(body, status_code=status, headers=headers)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _path(request: Request) -> str:
    return request.url.path


def _clean_message(message: str) -> str:
    """Pydantic prefixes errors raised in validators with 'Value error, '. Drop it."""
    prefix = "Value error, "
    return message[len(prefix):] if message.startswith(prefix) else message


def _field_path(loc: tuple[Any, ...] | list[Any]) -> str:
    """The machine-readable location, as the contract documents it: body.attachments.0.name."""
    return ".".join(str(part) for part in loc) or "body"


def _field_label(loc: tuple[Any, ...] | list[Any]) -> str:
    """The name a person reads in a message: attachments.0.name, without the 'body' prefix."""
    parts = [str(part) for part in loc if part != "body"]
    return ".".join(parts) or "request"


def friendly_message(err: dict[str, Any]) -> str:
    """One validation problem as a sentence a person can act on."""
    label = _field_label(err["loc"])
    if err["type"] == "missing":
        return f"The {label} field is required."
    return f"The {label} field is invalid: {_clean_message(str(err['msg']))}"


def _echoed_identifiers(request: Request, body: Any) -> dict[str, str | None]:
    """Identifiers a chat error may echo. Each one is checked against its allowlist first."""
    path = _path(request)
    settings = request.app.state.settings
    if not isinstance(body, dict):
        body = {}

    conversation_id = body.get("conversation_id")
    if not (
        isinstance(conversation_id, str)
        and len(conversation_id) <= CONVERSATION_ID_MAX_LENGTH
        and _CONVERSATION_ID.fullmatch(conversation_id)
    ):
        conversation_id = None

    if path in _TASK_MODES:
        mode: str | None = _TASK_MODES[path]
    elif "mode" not in body:
        mode = DEFAULT_MODE
    else:
        requested = body.get("mode")
        mode = requested if isinstance(requested, str) and requested in settings.modes else None

    if "model" not in body:
        model: str | None = DEFAULT_MODEL
    else:
        requested = body.get("model")
        model = requested if isinstance(requested, str) and requested in DEFAULT_MODELS else None

    return {"conversation_id": conversation_id, "model": model, "mode": mode}


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, ErrorCode.HTTP_ERROR)
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Request failed."
    return build_error_response(
        exc.status_code, code, message[:500], _request_id(request), path=_path(request)
    )


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    echoed = _echoed_identifiers(request, getattr(exc, "body", None))
    details = [
        FieldIssue(
            field=_field_path(err["loc"])[:200],
            type=str(err["type"])[:100],
            message=friendly_message(err)[:500],
        )
        for err in errors
    ]
    message = friendly_message(errors[0]) if errors else "The request is invalid."
    return build_error_response(
        422,
        ErrorCode.VALIDATION_ERROR,
        message[:500],
        _request_id(request),
        details,
        path=_path(request),
        conversation_id=echoed["conversation_id"],
        model=echoed["model"],
        mode=echoed["mode"],
    )


async def _conversation_error(request: Request, exc: ConversationError) -> JSONResponse:
    return build_error_response(
        exc.http_status, exc.code, str(exc), _request_id(request), path=_path(request)
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={
            "event": "error",
            "request_id": _request_id(request),
            "method": request.method,
            "path": safe_path(request.url.path),
        },
    )
    return build_error_response(
        500,
        ErrorCode.INTERNAL_ERROR,
        "An unexpected error occurred.",
        _request_id(request),
        path=_path(request),
    )


def register_error_handlers(app: FastAPI) -> None:
    # Starlette's stubs type every handler as taking Exception. Each handler is only
    # called for the exception class it is registered for, so the narrower type is safe.
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(ConversationError, _conversation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled)
