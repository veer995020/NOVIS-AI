"""Central error handling. Every error body uses the ErrorEnvelope schema:

    {"error": {"code": ..., "message": ..., "details": [...] | null}, "request_id": "..."}

Internal exception details are logged with the request ID and never returned.
Validation details give field paths and messages, never the submitted values.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.schemas.errors import ErrorCode, ErrorDetail, ErrorEnvelope, FieldIssue
from backend.services.errors import ConversationError
from backend.utils.redaction import safe_path

logger = logging.getLogger("backend.errors")

REQUEST_ID_HEADER = "X-Request-ID"

_HTTP_CODES: dict[int, ErrorCode] = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    429: ErrorCode.RATE_LIMITED,
}


def build_error_response(
    status: int,
    code: ErrorCode,
    message: str,
    request_id: str | None,
    details: list[FieldIssue] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details),
        request_id=request_id,
    )
    headers = dict(extra_headers or {})
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(envelope.model_dump(mode="json"), status_code=status, headers=headers)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, ErrorCode.HTTP_ERROR)
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Request failed."
    return build_error_response(exc.status_code, code, message[:500], _request_id(request))


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        FieldIssue(
            field=".".join(str(part) for part in err["loc"])[:200],
            message=str(err["msg"])[:500],
        )
        for err in exc.errors()
    ]
    return build_error_response(
        422,
        ErrorCode.VALIDATION_ERROR,
        "Request validation failed.",
        _request_id(request),
        details,
    )


async def _conversation_error(request: Request, exc: ConversationError) -> JSONResponse:
    return build_error_response(exc.http_status, exc.code, str(exc), _request_id(request))


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
        500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.", _request_id(request)
    )


def register_error_handlers(app: FastAPI) -> None:
    # Starlette's stubs type every handler as taking Exception. Each handler is only
    # called for the exception class it is registered for, so the narrower type is safe.
    app.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(ConversationError, _conversation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled)
