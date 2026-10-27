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
import logging
import math
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.error_handlers import REQUEST_ID_HEADER, build_error_response
from backend.schemas.errors import ErrorCode
from backend.utils.ids import new_id
from backend.utils.redaction import safe_path

logger = logging.getLogger("backend.request")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_HEALTH_PREFIX = "/health"
_PUBLIC_PATHS = frozenset({"/docs", "/openapi.json"})
_RATE_WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 10_000
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


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


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, max_request_bytes: int) -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming = headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else new_id()
        scope.setdefault("state", {})["request_id"] = request_id

        method = scope["method"]
        path = safe_path(scope["path"])
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
                # Do not advertise the server software.
                if "server" in response_headers:
                    del response_headers["server"]
                for name, value in SECURITY_HEADERS.items():
                    if name not in response_headers:
                        response_headers[name] = value
            await send(message)

        async def reject_too_large() -> None:
            response = build_error_response(
                413,
                ErrorCode.PAYLOAD_TOO_LARGE,
                f"Request body exceeds {self.max_request_bytes} bytes.",
                request_id,
            )
            await response(scope, receive, send_wrapper)

        try:
            if method in _BODY_METHODS:
                declared = headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_request_bytes:
                    await reject_too_large()
                    return
                try:
                    body = await _read_limited_body(receive, self.max_request_bytes)
                except _BodyTooLarge:
                    await reject_too_large()
                    return
                except _ClientDisconnected:
                    status_code = 499  # client closed the request; nothing to send
                    return

                # Hand the application the buffered body once, then the real receive.
                replayed = False

                async def replay_receive() -> Message:
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()

                await self.app(scope, replay_receive, send_wrapper)
            else:
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
    ) -> None:
        self.app = app
        self._key = api_key.encode("utf-8") if api_key else None
        self._hosts = frozenset(allowed_hosts)
        self._trust_forwarded = trust_forwarded_for
        self._limiter = _RateLimiter(rate_limit_per_minute, clock) if rate_limit_per_minute > 0 else None

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

        if self._key is not None and scope["path"] not in _PUBLIC_PATHS and not self._authorized(headers):
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
        response = build_error_response(status, code, message, request_id, extra_headers=extra)
        await response(scope, receive, send)
