"""Error classification: one category per failure, used for logs and for the retry decision.

Clients still receive the fixed messages and codes from the chat service. The category is for operators.
"""
from __future__ import annotations

from enum import StrEnum

from backend.services.errors import ConversationError
from backend.services.model_router import ModelUnavailableError
from backend.services.providers.base import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderEmptyReplyError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)


class ErrorCategory(StrEnum):
    CLIENT_INPUT = "client_input"
    CONFIGURATION = "configuration"
    UPSTREAM_TRANSIENT = "upstream_transient"
    UPSTREAM_REJECTED = "upstream_rejected"
    CAPACITY = "capacity"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


RETRYABLE: frozenset[ErrorCategory] = frozenset(
    {ErrorCategory.UPSTREAM_TRANSIENT, ErrorCategory.CAPACITY, ErrorCategory.TIMEOUT}
)


def classify_error(exc: BaseException) -> ErrorCategory:
    # Subclasses first: a broad ProviderError entry would hide the specific cause.
    if isinstance(exc, ProviderAuthError):
        return ErrorCategory.CONFIGURATION
    if isinstance(exc, ProviderRateLimitedError):
        return ErrorCategory.CAPACITY
    if isinstance(exc, ProviderUnavailableError | ProviderEmptyReplyError):
        return ErrorCategory.UPSTREAM_TRANSIENT
    if isinstance(exc, ProviderBadRequestError | ProviderInvalidResponseError):
        return ErrorCategory.UPSTREAM_REJECTED
    if isinstance(exc, ProviderError):
        return ErrorCategory.UPSTREAM_REJECTED
    if isinstance(exc, ModelUnavailableError):
        return ErrorCategory.CONFIGURATION
    if isinstance(exc, ConversationError):
        return ErrorCategory.CLIENT_INPUT
    if isinstance(exc, TimeoutError):
        return ErrorCategory.TIMEOUT
    return ErrorCategory.INTERNAL
