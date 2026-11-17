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


# ---- errors: one class per failure the chat service must tell apart ----------------------
# Any provider raises these. The classifier and the chat service depend only on these names.

class ProviderBadRequestError(ProviderError):
    """The request cannot succeed as sent. Never retried."""


class ProviderAuthError(ProviderError):
    """The upstream key was refused. Never retried. A configuration fault, not a user fault."""


class ProviderRateLimitedError(ProviderError):
    """The upstream rate limit was still in force after the bounded retries."""

    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailableError(ProviderError):
    """Timeouts, connection failures, server errors, or a stream broken mid-reply."""


class ProviderEmptyReplyError(ProviderError):
    """The model finished without producing any text."""


class ProviderInvalidResponseError(ProviderError):
    """The upstream response did not match what the provider can accept."""


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
