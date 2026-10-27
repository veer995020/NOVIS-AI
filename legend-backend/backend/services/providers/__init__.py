"""Provider integrations. Each provider is a class that satisfies the Provider protocol.

No provider is registered yet. Until one is, every model reports unavailable and chat
returns 503 model_unavailable. The application never produces a reply itself.
"""
from __future__ import annotations

from collections.abc import Mapping

from backend.config import Settings
from backend.services.providers.base import Provider, ProviderError

__all__ = ["Provider", "ProviderError", "default_providers"]


def default_providers(settings: Settings) -> Mapping[str, Provider]:
    """Providers that run with the current configuration. None are registered yet."""
    del settings  # reserved for the first real provider, which will read its credentials here
    return {}
