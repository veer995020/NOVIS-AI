"""Model routing: which provider and provider model serve each model id.

This is separate from mode routing. Modes choose the instructions a model is
given (see prompts/ and services/tasks.py). The model router only chooses where
a model's requests go.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.config import DEFAULT_MODEL, DEFAULT_MODELS, ConfigError, ModelBinding
from backend.services.providers.base import Provider


class ModelUnavailableError(Exception):
    """The model has no route in the current configuration."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"No route is configured for model '{model_id}'.")


@dataclass(frozen=True)
class Route:
    model_id: str
    provider: Provider
    provider_model: str


def resolve_model_id(requested: str | None) -> str:
    """A missing model resolves to the default model (Rapid)."""
    return requested or DEFAULT_MODEL


class ModelRouter:
    def __init__(self, routes: Mapping[str, Route]) -> None:
        self._routes = dict(routes)

    def route_for(self, model_id: str) -> Route:
        route = self._routes.get(model_id)
        if route is None:
            raise ModelUnavailableError(model_id)
        return route

    def available_models(self) -> tuple[str, ...]:
        return tuple(model for model in DEFAULT_MODELS if model in self._routes)


def build_model_router(
    bindings: tuple[ModelBinding, ...],
    providers: Mapping[str, Provider],
) -> ModelRouter:
    """Build the router at startup. A route that names a missing provider fails startup."""
    routes: dict[str, Route] = {}
    problems: list[str] = []
    for model_id, provider_name, provider_model in bindings:
        provider = providers.get(provider_name)
        if provider is None:
            problems.append(
                f"MODEL_ROUTES sends '{model_id}' to provider '{provider_name}', which is not "
                "available. Check its credentials and APP_ENV."
            )
            continue
        routes[model_id] = Route(model_id=model_id, provider=provider, provider_model=provider_model)
    if problems:
        raise ConfigError(problems)
    return ModelRouter(routes)
