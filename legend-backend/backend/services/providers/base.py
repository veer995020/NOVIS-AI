"""Provider contract. A provider turns a prompt into text, or fails with ProviderError.

Providers never invent a reply. If the call fails, they raise. Error messages
are safe to log: they carry no credentials and no response bodies.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol


class ProviderError(Exception):
    """A provider call failed or returned no usable text."""


class Provider(Protocol):
    name: str

    def generate(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        cancel: asyncio.Event,
    ) -> AsyncIterator[str]:
        """Yield text pieces for `messages`. Honour `cancel` where practical.

        Raise ProviderError when no usable text can be produced. Never yield invented text.
        """
