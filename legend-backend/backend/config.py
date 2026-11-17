"""Configuration: environment loading, startup validation, and secret handling.

Every value comes from an environment variable. Secrets have no defaults, are
wrapped so they never appear in repr(), str(), or logs, and are never stored in
the source tree.

Environment variables:
  APP_ENV                  development | test | production   (default: development)
  LOG_LEVEL                DEBUG | INFO | WARNING | ERROR     (default: INFO)
  LOG_FORMAT               json | text                        (default: json)
  ENABLE_DOCS              true | false, serves /docs         (default: false)
  CORS_ORIGINS             comma-separated origins            (default: none)
  MODEL_ROUTES             model=provider:provider_model, comma-separated
                           e.g. rapid=<provider>:<provider model name> (default: none)
  CHAT_MODES               comma-separated mode names
  MAX_REQUEST_BYTES        largest accepted request body      (default: 2097152)
  MAX_HISTORY_MESSAGES     messages kept per conversation     (default: 200)
  MAX_CONTEXT_MESSAGES     messages sent to the model         (default: 20)
  MAX_CONVERSATIONS        conversations kept in memory       (default: 1000)
  GENERATION_TIMEOUT_SECONDS  longest allowed reply generation (default: 60)
  CONVERSATION_IDLE_SECONDS   idle time before a conversation is dropped (default: 3600)
  DATABASE_URL               postgresql://... connection string. Unset: in-memory conversations
  DATABASE_POOL_MAX          connections to the database, 1 to 100 (default: 10)
  PROVIDER_MAX_ATTEMPTS      total upstream attempts per turn, 1 to 5 (default: 3)
  PROVIDER_READ_TIMEOUT_SECONDS  longest silence from the provider between chunks, 1 to 600 (default: 60)
  MAX_CONCURRENT_TURNS        replies generating at once, across all clients (default: 32)
  API_ACCESS_KEY              shared bearer key for every API call; required in production
  ALLOWED_HOSTS               comma-separated Host names accepted; required in production
  RATE_LIMIT_PER_MINUTE       requests per client per minute, 0 disables (default: 120)
  RATE_LIMIT_GENERATION_PER_MINUTE  reply requests per client per minute, 0 disables (default: 30)
  TRUST_FORWARDED_FOR         true only behind a proxy that sets X-Forwarded-For (default: false)
  SERVE_FRONTEND              serve the chat page at /app/chat.html (default: true)

A model with no route answers with model_unavailable. Nothing is generated for it.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

APP_VERSION = "0.1.0"
MAX_MESSAGE_CHARS = 8000
MAX_REPLY_CHARS = 100_000

# Model ids accepted by the API: Brainstormer, Mythical, Sonet, and Rapid.
DEFAULT_MODELS = ("brainstormer", "mythical", "sonet", "rapid")
DEFAULT_MODEL = "rapid"  # used when a request omits the model
MODEL_DISPLAY_NAMES = {
    "brainstormer": "Brainstormer",
    "mythical": "Mythical",
    "sonet": "Sonet",
    "rapid": "Rapid",
}

DEFAULT_MODES = ("general", "research", "coding", "writing")
REQUIRED_MODES = ("general", "research", "coding", "writing")
DEFAULT_MODE = "general"

ENVIRONMENTS = ("development", "test", "production")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMATS = ("json", "text")

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PROVIDER_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# (env var, Settings attribute, minimum, maximum, default)
_INT_SETTINGS: tuple[tuple[str, str, int, int, int], ...] = (
    ("MAX_REQUEST_BYTES", "max_request_bytes", 1024, 10 * 1024 * 1024, 2 * 1024 * 1024),
    ("MAX_HISTORY_MESSAGES", "max_history_messages", 1, 10_000, 200),
    ("MAX_CONTEXT_MESSAGES", "max_context_messages", 1, 200, 20),
    ("MAX_CONVERSATIONS", "max_conversations", 1, 100_000, 1000),
    ("GENERATION_TIMEOUT_SECONDS", "generation_timeout_seconds", 1, 600, 60),
    ("CONVERSATION_IDLE_SECONDS", "conversation_idle_seconds", 60, 604800, 3600),
    ("MAX_CONCURRENT_TURNS", "max_concurrent_turns", 1, 1000, 32),
    ("DATABASE_POOL_MAX", "database_pool_max", 1, 100, 10),
    ("PROVIDER_MAX_ATTEMPTS", "provider_max_attempts", 1, 5, 3),
    ("PROVIDER_READ_TIMEOUT_SECONDS", "provider_read_timeout_seconds", 1, 600, 60),
    ("RATE_LIMIT_PER_MINUTE", "rate_limit_per_minute", 0, 100000, 120),
    ("RATE_LIMIT_GENERATION_PER_MINUTE", "rate_limit_generation_per_minute", 0, 100000, 30),
)

# (model id, provider name, provider model name)
ModelBinding = tuple[str, str, str]


class ConfigError(Exception):
    """Configuration is missing or invalid. Lists every problem found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("Invalid configuration: " + "; ".join(self.problems))


class Secret:
    """Holds a credential that must never be printed or logged."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the raw value. Call only where the credential is actually used."""
        return self._value

    def __repr__(self) -> str:
        return "Secret(****)"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    app_name: str = "Legend Inc. - Novis AI Backend"
    version: str = APP_VERSION
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"
    enable_docs: bool = False
    cors_origins: tuple[str, ...] = ()
    max_request_bytes: int = 2 * 1024 * 1024
    model_routes: tuple[ModelBinding, ...] = ()
    modes: tuple[str, ...] = DEFAULT_MODES
    max_history_messages: int = 200
    max_context_messages: int = 20
    max_conversations: int = 1000
    generation_timeout_seconds: int = 60
    conversation_idle_seconds: int = 3600
    max_concurrent_turns: int = 32
    database_pool_max: int = 10
    provider_max_attempts: int = 3
    provider_read_timeout_seconds: int = 60
    # PostgreSQL connection string. Unset: conversations live in memory, in one process only.
    database_url: Secret | None = None
    api_access_key: Secret | None = None
    allowed_hosts: tuple[str, ...] = ()
    rate_limit_per_minute: int = 120
    rate_limit_generation_per_minute: int = 30
    trust_forwarded_for: bool = False
    serve_frontend: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


class _Env:
    """Typed readers for environment values. Collects problems instead of failing fast."""

    def __init__(self, source: Mapping[str, str]) -> None:
        self._source = source
        self.problems: list[str] = []

    def raw(self, name: str) -> str:
        return self._source.get(name, "")

    def text(self, name: str, default: str) -> str:
        return self._source.get(name, "").strip() or default

    def choice(self, name: str, default: str, allowed: tuple[str, ...]) -> str:
        value = self.text(name, default).lower()
        for option in allowed:
            if option.lower() == value:
                return option
        self.problems.append(f"{name} must be one of: {', '.join(allowed)}")
        return default

    def secret(self, name: str) -> Secret | None:
        value = self.raw(name).strip()
        return Secret(value) if value else None

    def flag(self, name: str, default: bool) -> bool:
        value = self.text(name, "true" if default else "false").lower()
        if value in _TRUE_VALUES:
            return True
        if value in _FALSE_VALUES:
            return False
        self.problems.append(f"{name} must be true or false")
        return default

    def integer(self, name: str, default: int, minimum: int, maximum: int) -> int:
        raw = self.text(name, str(default))
        try:
            value = int(raw)
        except ValueError:
            self.problems.append(f"{name} must be an integer")
            return default
        if not minimum <= value <= maximum:
            self.problems.append(f"{name} must be between {minimum} and {maximum}")
            return default
        return value

    def csv(self, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        items = tuple(item.strip() for item in self.raw(name).split(",") if item.strip())
        return items or default


def parse_model_routes(raw: str) -> tuple[tuple[ModelBinding, ...], list[str]]:
    """Parse 'model=provider:provider_model' entries. Malformed entries become problems."""
    bindings: list[ModelBinding] = []
    problems: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        model_id, equals, target = item.partition("=")
        provider, colon, provider_model = target.partition(":")
        if not equals or not colon:
            problems.append("MODEL_ROUTES entries must look like model=provider:provider_model")
            continue
        bindings.append((model_id.strip(), provider.strip(), provider_model.strip()))
    return tuple(bindings), problems


def _route_problems(bindings: tuple[ModelBinding, ...]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for model_id, provider, provider_model in bindings:
        if model_id not in DEFAULT_MODELS:
            problems.append(
                f"MODEL_ROUTES: unknown model '{model_id}'; allowed: {', '.join(DEFAULT_MODELS)}"
            )
        elif model_id in seen:
            problems.append(f"MODEL_ROUTES: model '{model_id}' is routed more than once")
        seen.add(model_id)
        if not _PROVIDER_PATTERN.match(provider):
            problems.append(f"MODEL_ROUTES: invalid provider name for '{model_id}'")
        if not _PROVIDER_MODEL_PATTERN.match(provider_model):
            problems.append(f"MODEL_ROUTES: invalid provider model name for '{model_id}'")
    return problems


def _origin_problems(origin: str, production: bool) -> list[str]:
    if origin == "*":
        return ["CORS_ORIGINS must not contain '*'; list each allowed origin explicitly"]
    try:
        parts = urlsplit(origin)
    except ValueError:
        return [f"CORS origin {origin!r} is not a valid URL"]
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return [f"CORS origin {origin!r} must be an absolute http or https URL"]
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return [f"CORS origin {origin!r} must not include a path, query, or fragment"]
    if production and parts.scheme != "https":
        return [f"CORS origin {origin!r} must use https in production"]
    return []


def _settings_problems(settings: Settings) -> list[str]:
    problems: list[str] = []

    if settings.database_url is not None:
        # Name the rule, never the value: the string holds the password.
        url = settings.database_url.reveal()
        if not (url.startswith("postgresql://") or url.startswith("postgres://")):
            problems.append("DATABASE_URL must be a postgresql:// connection string")

    if settings.environment not in ENVIRONMENTS:
        problems.append(f"environment must be one of: {', '.join(ENVIRONMENTS)}")
    if settings.log_level not in LOG_LEVELS:
        problems.append(f"log_level must be one of: {', '.join(LOG_LEVELS)}")
    if settings.log_format not in LOG_FORMATS:
        problems.append(f"log_format must be one of: {', '.join(LOG_FORMATS)}")

    for origin in settings.cors_origins:
        problems.extend(_origin_problems(origin, settings.is_production))

    problems.extend(_route_problems(settings.model_routes))

    if not settings.modes:
        problems.append("CHAT_MODES must not be empty")
    elif any(not _NAME_PATTERN.match(mode) for mode in settings.modes):
        problems.append(
            "CHAT_MODES names must start with a lowercase letter or digit and use "
            "only lowercase letters, digits, '.', '_' or '-'"
        )
    if len(set(settings.modes)) != len(settings.modes):
        problems.append("CHAT_MODES must not contain duplicates")
    missing = [mode for mode in REQUIRED_MODES if mode not in settings.modes]
    if missing:
        problems.append(f"CHAT_MODES must include: {', '.join(missing)}")

    for env_name, attr, minimum, maximum, _default in _INT_SETTINGS:
        value = getattr(settings, attr)
        if not minimum <= value <= maximum:
            problems.append(f"{env_name} must be between {minimum} and {maximum}")

    if settings.max_context_messages > settings.max_history_messages:
        problems.append("MAX_CONTEXT_MESSAGES cannot exceed MAX_HISTORY_MESSAGES")

    for host in settings.allowed_hosts:
        if not _HOST_PATTERN.match(host) or len(host) > 253:
            problems.append(f"ALLOWED_HOSTS contains an invalid host name: {host!r}")

    if settings.is_production:
        if settings.enable_docs:
            problems.append("ENABLE_DOCS must be false in production")
        if settings.api_access_key is None:
            problems.append("API_ACCESS_KEY is required in production")
        if not settings.allowed_hosts:
            problems.append("ALLOWED_HOSTS is required in production")

    return problems


def validate_settings(settings: Settings) -> None:
    """Startup validation for any Settings object. Raises ConfigError listing every problem."""
    problems = _settings_problems(settings)
    if problems:
        raise ConfigError(problems)


def load_settings(source: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from environment variables (or a mapping, for tests), then validate."""
    env = _Env(os.environ if source is None else source)
    bindings, route_problems = parse_model_routes(env.raw("MODEL_ROUTES"))
    int_values: dict[str, Any] = {
        attr: env.integer(name, default, minimum, maximum)
        for name, attr, minimum, maximum, default in _INT_SETTINGS
    }
    settings = Settings(
        environment=env.choice("APP_ENV", "development", ENVIRONMENTS),
        log_level=env.choice("LOG_LEVEL", "INFO", LOG_LEVELS),
        log_format=env.choice("LOG_FORMAT", "json", LOG_FORMATS),
        enable_docs=env.flag("ENABLE_DOCS", False),
        cors_origins=env.csv("CORS_ORIGINS", ()),
        model_routes=bindings,
        modes=env.csv("CHAT_MODES", DEFAULT_MODES),
        api_access_key=env.secret("API_ACCESS_KEY"),
        database_url=env.secret("DATABASE_URL"),
        allowed_hosts=tuple(host.lower() for host in env.csv("ALLOWED_HOSTS", ())),
        trust_forwarded_for=env.flag("TRUST_FORWARDED_FOR", False),
        serve_frontend=env.flag("SERVE_FRONTEND", True),
        **int_values,
    )
    problems = env.problems + route_problems + _settings_problems(settings)
    if problems:
        raise ConfigError(problems)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
