"""Provider integrations. Each provider is a class that satisfies the Provider protocol.

No provider is built into this release. Every model reports unavailable, and chat returns
503 model_unavailable, until a provider is registered in default_providers below.
"""
from __future__ import annotations

from collections.abc import Mapping

from backend.config import Settings
from backend.services.providers.base import (
    Provider,
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderEmptyReplyError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)

__all__ = [
    "Provider",
    "ProviderAuthError",
    "ProviderBadRequestError",
    "ProviderEmptyReplyError",
    "ProviderError",
    "ProviderInvalidResponseError",
    "ProviderRateLimitedError",
    "ProviderUnavailableError",
    "default_providers",
]


def default_providers(settings: Settings) -> Mapping[str, Provider]:
    """Providers that run with the current configuration. None are built in yet.

    To add one: construct it from `settings`, return it under the name that MODEL_ROUTES uses.
    """
    del settings
    return {}
