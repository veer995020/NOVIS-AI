"""Application factory and ASGI entry point.

Run from the project root:  uvicorn backend.main:app

Startup fails fast with a ConfigError listing every configuration problem,
including a route that names a provider which cannot run.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import DEFAULT_MODELS, Settings, get_settings, validate_settings
from backend.error_handlers import register_error_handlers
from backend.middleware import AccessControlMiddleware, RequestContextMiddleware
from backend.routes.chat import router as chat_router
from backend.routes.conversations import router as conversations_router
from backend.routes.health import router as health_router
from backend.routes.models import router as models_router
from backend.routes.tasks import router as tasks_router
from backend.schemas.options import configure_allowed_options
from backend.services.chat import ChatService
from backend.services.conversation_store import ConversationStore
from backend.services.model_router import build_model_router
from backend.services.providers import Provider, default_providers
from backend.utils.logging_setup import configure_logging

API_DESCRIPTION = """
Chat backend for Legend Inc.

**Replies.** Chat endpoints return a `ChatResponse`. Its `status` is `completed`, `stopped`, or `error`.
A failure is never reported as `completed`. Error responses use the HTTP status that matches the outcome.

**Models.** `GET /api/models` lists the model ids and which are available. A request that omits
`model` uses `rapid`. A model with no route returns 503 `model_unavailable`.

**Conversations.** Conversations are temporary. They live in server memory and are lost on restart.
The conversation id is the only access control, so keep it private. Each change increments `version`.

**Errors.** Request-level failures (validation, not found, conflicts, capacity, size, method, internal)
return one envelope: `{"error": {code, message, details}, "request_id"}`. A chat outcome that did not
produce a reply (generation failed, timed out, model unavailable) returns a `ChatResponse` with
`status: "error"` and the same `error` object, so streaming and JSON clients see the same outcome.
Provider and internal details are never returned. Quote `request_id` when reporting a problem.

**Limits.** Request bodies are capped in bytes, messages and titles in characters, and replies by
concurrent-turn count and a generation timeout. See the README for every limit.
"""

OPENAPI_TAGS = [
    {"name": "chat", "description": "Send a message and receive a reply, either whole or streamed."},
    {"name": "tasks", "description": "Research, code, and writing endpoints. Same behaviour as /api/chat with a fixed mode."},
    {"name": "conversations", "description": "Create, read, update, and clear temporary conversation state."},
    {"name": "models", "description": "Model ids and whether each is available here."},
    {"name": "health", "description": "Health and readiness. Status words, version, and uptime only."},
]

# Only the methods and headers the API uses. OPTIONS preflight is answered by the middleware.
CORS_METHODS = ["GET", "POST", "PATCH"]
CORS_HEADERS = ["Content-Type", "Authorization", "X-Request-ID"]

logger = logging.getLogger("backend.app")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.started_at = time.monotonic()
    app.state.ready = True
    logger.info(
        "application started: environment=%s version=%s docs=%s cors_origins=%d available_models=%d",
        settings.environment,
        settings.version,
        "enabled" if settings.enable_docs else "disabled",
        len(settings.cors_origins),
        len(app.state.model_router.available_models()),
        extra={"event": "startup"},
    )
    try:
        yield
    finally:
        app.state.ready = False
        logger.info("application stopping", extra={"event": "shutdown"})


def create_app(
    settings: Settings | None = None,
    providers: Mapping[str, Provider] | None = None,
) -> FastAPI:
    """Build the application.

    `providers` replaces the default provider registry. Tests use it to supply
    scripted providers. Production passes nothing.
    """
    settings = settings or get_settings()
    validate_settings(settings)
    configure_logging(settings.log_level, settings.log_format)
    configure_allowed_options(DEFAULT_MODELS, settings.modes)

    active_providers = dict(providers) if providers is not None else dict(default_providers(settings))
    model_router = build_model_router(settings.model_routes, active_providers)

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=_lifespan,
        # Docs are off unless explicitly enabled outside production.
        docs_url="/docs" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.ready = False
    app.state.started_at = None
    app.state.model_router = model_router

    # Middleware added last runs first: CORS, then request context, then access control.
    # Access control is innermost, so its rejections carry the request id and security headers.
    # CORS answers preflight before any of them, so browsers can send the access key.
    app.add_middleware(
        AccessControlMiddleware,
        api_key=settings.api_access_key.reveal() if settings.api_access_key else None,
        allowed_hosts=settings.allowed_hosts,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        trust_forwarded_for=settings.trust_forwarded_for,
    )
    app.add_middleware(RequestContextMiddleware, max_request_bytes=settings.max_request_bytes)
    if settings.cors_origins:
        # No origins configured means no CORS headers at all. Never "*".
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=CORS_METHODS,
            allow_headers=CORS_HEADERS,
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

    register_error_handlers(app)

    store = ConversationStore(
        max_messages=settings.max_history_messages,
        max_conversations=settings.max_conversations,
        idle_seconds=settings.conversation_idle_seconds,
    )
    app.state.conversation_store = store
    app.state.chat_service = ChatService(store=store, settings=settings, models=model_router)

    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_router)
    app.include_router(tasks_router)
    app.include_router(conversations_router)
    return app


app = create_app()
