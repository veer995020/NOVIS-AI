"""Allowed model and mode names, read by the request schema at validation time.

create_app() calls configure_allowed_options() with the configured lists, so the
request schema, GET /api/chat/options, and the configuration always agree.
"""
from __future__ import annotations

from collections.abc import Iterable

from pydantic_core import PydanticCustomError

from backend.config import DEFAULT_MODELS, DEFAULT_MODES

_allowed_models: tuple[str, ...] = DEFAULT_MODELS
_allowed_modes: tuple[str, ...] = DEFAULT_MODES


def configure_allowed_options(models: Iterable[str], modes: Iterable[str]) -> None:
    global _allowed_models, _allowed_modes
    _allowed_models = tuple(models)
    _allowed_modes = tuple(modes)


def allowed_models() -> tuple[str, ...]:
    return _allowed_models


def allowed_modes() -> tuple[str, ...]:
    return _allowed_modes


def require_allowed_model(value: str) -> str:
    if value not in _allowed_models:
        raise PydanticCustomError(
            "unknown_model",
            "Unknown model. Allowed values: {allowed}.",
            {"allowed": ", ".join(_allowed_models)},
        )
    return value


def require_allowed_mode(value: str) -> str:
    if value not in _allowed_modes:
        raise PydanticCustomError(
            "unknown_mode",
            "Unknown mode. Allowed values: {allowed}.",
            {"allowed": ", ".join(_allowed_modes)},
        )
    return value
