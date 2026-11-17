"""ASGI middleware.

Pure ASGI rather than BaseHTTPMiddleware, so streaming responses pass through unbuffered.

- RequestContextMiddleware: request ids, body size limit, security headers, request logging.
- AccessControlMiddleware: Host allowlist, per-client rate limit, shared access key.

Order matters. Access control sits inside request context, so its rejections still carry
the request id and security headers. Health endpoints are exempt from access control so
that orchestrator probes keep working.
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.error_handlers import REQUEST_ID_HEADER, build_error_response
from backend.schemas.errors import ErrorCode, FieldIssue
from backend.utils.ids import new_id
from backend.utils.redaction import safe_path

logger = logging.getLogger("backend.request")

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_HEALTH_PREFIX = "/health"
_PUBLIC_PATHS = frozenset({"/docs", "/openapi.json", "/api/contract"})
_PUBLIC_PREFIXES = ("/app/",)
_RATE_WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 10_000
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
# API responses are data, never a page: they load nothing and run nothing.
API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
# The chat page is one file: its own inline script and style. It loads nothing from another origin, and
# its requests go to its own server (apiBase is empty by default). Anything else is refused.
CHAT_PAGE_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)


def _is_chat_page(path: str) -> bool:
    return path == "/app" or path.startswith("/app/")


def _serves_a_page(path: str) -> bool:
    """The chat page and the API docs need their own policy, so the API policy is not applied to them."""
    return path == "/app" or path.startswith("/app/") or path in ("/docs", "/openapi.json")


def _is_public(path: str) -> bool:
    """Public: API documentation, the contract, and the static chat page. Not the API itself."""
    return path in _PUBLIC_PATHS or path == "/app" or path.startswith(_PUBLIC_PREFIXES)


def _json_body_problem(body: bytes) -> str | None:
    """None if the body is strict UTF-8 JSON, otherwise a short reason the client can act on.

    JSON over HTTP is UTF-8. Parsers that guess the encoding accept UTF-16 and similar, and
    a NUL byte is never valid outside an escape, so both are refused here.
    """
    if b"\x00" in body:
        return "The body contains a NUL byte. Send UTF-8 JSON."
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "The body is not valid UTF-8. Send UTF-8 JSON."
    try:
        json.loads(text)
    except RecursionError:
        return "The JSON is nested too deeply."
    except ValueError:
        return "The JSON could not be parsed. Check its syntax and number sizes."
    return None


class _BodyTooLarge(Exception):
    pass


class _ClientDisconnected(Exception):
    pass


async def _read_limited_body(receive: Receive, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            raise _ClientDisconnected()
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            raise _BodyTooLarge()
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


# The security headers, as ordered pairs, built once. The API gets a strict content security policy;
# the served page does not, because it runs its own script.
_API_HEADERS: tuple[tuple[str, str], ...] = tuple(
    {**SECURITY_HEADERS, "Content-Security-Policy": API_CONTENT_SECURITY_POLICY}.items()
)
_PAGE_HEADERS: tuple[tuple[str, str], ...] = tuple(SECURITY_HEADERS.items())
_CHAT_PAGE_HEADERS: tuple[tuple[str, str], ...] = tuple(
    {**SECURITY_HEADERS, "Content-Security-Policy": CHAT_PAGE_CONTENT_SECURITY_POLICY}.items()
)

# ASGI header names are lowercase bytes. Encoding them once keeps the per-response work to list operations.
_REQUEST_ID_NAME = REQUEST_ID_HEADER.lower().encode("latin-1")
_SERVER_NAME = b"server"


def _raw(headers: tuple[tuple[str, str], ...]) -> tuple[tuple[bytes, bytes], ...]:
    return tuple((name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers)


_API_RAW = _raw(_API_HEADERS)
_PAGE_RAW = _raw(_PAGE_HEADERS)
_CHAT_PAGE_RAW = _raw(_CHAT_PAGE_HEADERS)


class RequestContextMiddleware:
    """Outermost layer: request id, security headers, and the access log line. It reads no body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always server-generated. A client-chosen id could collide with another turn's id.
        request_id = new_id()
        scope.setdefault("state", {})["request_id"] = request_id

        method = scope["method"]
        raw_path = scope["path"]
        # The log and every response carry the placeholder form. The raw path only decides which policy applies.
        path = safe_path(raw_path)
        status_code = 500
        started = time.perf_counter()

        if _is_chat_page(raw_path):
            policy_headers = _CHAT_PAGE_RAW
        elif _serves_a_page(raw_path):
            policy_headers = _PAGE_RAW  # the API docs load their own assets, so they get no policy here
        else:
            policy_headers = _API_RAW
        request_id_raw = request_id.encode("ascii")

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Drop the server software name and any request id the app set; ours replaces it.
                headers = [
                    (name, value)
                    for name, value in message.get("headers", ())
                    if name != _SERVER_NAME and name != _REQUEST_ID_NAME
                ]
                present = {name for name, _ in headers}
                headers.append((_REQUEST_ID_NAME, request_id_raw))
                for name, value in policy_headers:
                    if name not in present:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request completed",
                extra={
                    "event": "request",
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )


class BodyLimitMiddleware:
    """Reads and checks the request body. It sits inside authentication, host, and rate checks,
    so a caller who is refused is never made to send, or the server to buffer, a body.
    Responses it sends pass through the request-context layer, so they carry the same headers."""

    def __init__(self, app: ASGIApp, max_request_bytes: int) -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = scope.get("state", {}).get("request_id", "")

        async def reject(status: int, code: ErrorCode, message: str, details: list[FieldIssue] | None = None) -> None:
            response = build_error_response(status, code, message, request_id, details, path=scope["path"])
            await response(scope, receive, send)

        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_request_bytes:
            await reject(413, ErrorCode.PAYLOAD_TOO_LARGE, f"Request body exceeds {self.max_request_bytes} bytes.")
            return
        try:
            body = await _read_limited_body(receive, self.max_request_bytes)
        except _BodyTooLarge:
            await reject(413, ErrorCode.PAYLOAD_TOO_LARGE, f"Request body exceeds {self.max_request_bytes} bytes.")
            return
        except _ClientDisconnected:
            return  # the client closed the request; nothing to send

        # A body must be JSON. An empty body needs no content type.
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if body and media_type != "application/json":
            await reject(415, ErrorCode.UNSUPPORTED_MEDIA_TYPE, "Request body must be sent as application/json.")
            return

        problem = _json_body_problem(body) if body else None
        if problem is not None:
            await reject(
                422,
                ErrorCode.VALIDATION_ERROR,
                "Request body is not valid JSON.",
                [FieldIssue(field="body", type="json_invalid", message=problem)],
            )
            return

        # Hand the application the buffered body once, then the real receive.
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


class CorsOriginGuardMiddleware:
    """Refuses any browser request from an origin that is neither listed in CORS_ORIGINS nor the
    site the request was sent to. A request without an Origin header (a non-browser client) is not
    affected. The refusal uses the error envelope, with the same headers as every other response.

    Runs inside the request-context layer, outside CORS, so a refused preflight is answered in the
    envelope rather than by the CORS middleware.
    """

    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str]) -> None:
        self.app = app
        self._allowed = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and not self._origin_allowed(origin, headers.get("host", "")):
                request_id = scope.get("state", {}).get("request_id", "")
                response = build_error_response(
                    403,
                    ErrorCode.FORBIDDEN,
                    "This origin is not allowed to call the API.",
                    request_id,
                    path=scope["path"],
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _origin_allowed(self, origin: str, host: str) -> bool:
        if origin in self._allowed:
            return True
        # The page is served from the same site as the API. Compare the host and port, which the
        # Host allowlist has already checked; the scheme can differ behind a TLS-terminating proxy.
        parts = urlsplit(origin)
        return bool(host) and parts.scheme in ("http", "https") and parts.netloc.lower() == host.lower()


class _RateLimiter:
    """Sliding one-minute window per client. Single-threaded asyncio: the check and the
    update run without an await between them, so they cannot interleave. Memory is capped
    at MAX_TRACKED_CLIENTS; the least recently seen client is forgotten first."""

    def __init__(self, limit_per_window: int, clock: Callable[[], float]) -> None:
        self._limit = limit_per_window
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def take(self, client: str) -> float | None:
        """Record one request. Returns None if allowed, or seconds until the next slot."""
        now = self._clock()
        hits = self._hits.get(client)
        if hits is None:
            hits = deque()
            self._hits[client] = hits
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(client)
        cutoff = now - _RATE_WINDOW_SECONDS
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            return hits[0] + _RATE_WINDOW_SECONDS - now
        hits.append(now)
        return None


# POST endpoints that generate a reply. Each costs a model call, so they get a stricter limit.
_GENERATION_PATHS = frozenset({
    "/api/chat", "/api/chat/stream", "/api/research", "/api/code", "/api/write",
    "/chat", "/research", "/code", "/write",
})


class AccessControlMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        api_key: str | None,
        allowed_hosts: tuple[str, ...],
        rate_limit_per_minute: int,
        trust_forwarded_for: bool,
        clock: Callable[[], float] = time.monotonic,
        generation_limit_per_minute: int = 0,
    ) -> None:
        self.app = app
        self._key = api_key.encode("utf-8") if api_key else None
        self._hosts = frozenset(allowed_hosts)
        self._trust_forwarded = trust_forwarded_for
        self._limiter = _RateLimiter(rate_limit_per_minute, clock) if rate_limit_per_minute > 0 else None
        self._generation_limiter = (
            _RateLimiter(generation_limit_per_minute, clock) if generation_limit_per_minute > 0 else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(_HEALTH_PREFIX):
            await self.app(scope, receive, send)
            return

        request_id = scope.get("state", {}).get("request_id")
        headers = Headers(scope=scope)

        if self._hosts and not self._host_allowed(headers.get("host", "")):
            await self._reject(
                scope, receive, send, 400, ErrorCode.BAD_REQUEST,
                "The Host header is not allowed.", request_id,
            )
            return

        if self._limiter is not None:
            wait = self._limiter.take(self._client_key(scope, headers))
            if wait is not None:
                await self._reject(
                    scope, receive, send, 429, ErrorCode.RATE_LIMITED,
                    "Too many requests. Wait before retrying.", request_id,
                    extra={"Retry-After": str(max(1, math.ceil(wait)))},
                )
                return

        if self._generation_limiter is not None and scope["method"] == "POST" and scope["path"] in _GENERATION_PATHS:
            wait = self._generation_limiter.take(self._client_key(scope, headers))
            if wait is not None:
                await self._reject(
                    scope, receive, send, 429, ErrorCode.RATE_LIMITED,
                    "Too many replies requested. Wait before asking again.", request_id,
                    extra={"Retry-After": str(max(1, math.ceil(wait)))},
                )
                return

        if self._key is not None and not _is_public(scope["path"]) and not self._authorized(headers):
            await self._reject(
                scope, receive, send, 401, ErrorCode.UNAUTHORIZED,
                "A valid bearer token is required.", request_id,
                extra={"WWW-Authenticate": "Bearer"},
            )
            return

        await self.app(scope, receive, send)

    def _host_allowed(self, host_header: str) -> bool:
        host = host_header.lower()
        if host.startswith("["):  # IPv6 literal: never in the allowlist
            return False
        return host.split(":", 1)[0] in self._hosts

    def _client_key(self, scope: Scope, headers: Headers) -> str:
        if self._trust_forwarded:
            forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded and len(forwarded) <= 64:
                return forwarded
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _authorized(self, headers: Headers) -> bool:
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or self._key is None:
            return False
        return hmac.compare_digest(token.strip().encode("utf-8"), self._key)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        code: ErrorCode,
        message: str,
        request_id: str | None,
        extra: dict[str, str] | None = None,
    ) -> None:
        response = build_error_response(status, code, message, request_id, extra_headers=extra, path=scope["path"])
        await response(scope, receive, send)
