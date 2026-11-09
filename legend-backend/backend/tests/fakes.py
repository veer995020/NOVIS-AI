"""Test doubles. A ScriptedProvider replaces a real provider in tests only. The
application never registers it, and its output is written by the test itself."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import DEFAULT_MODELS, ModelBinding, Settings
from backend.main import create_app
from backend.services.model_router import ModelRouter, build_model_router
from backend.services.providers import ProviderError

FAKE_PROVIDER = "fake"
ROUTES: tuple[ModelBinding, ...] = tuple((m, FAKE_PROVIDER, "fake-model") for m in DEFAULT_MODELS)

Script = Callable[[list[dict[str, str]], asyncio.Event], AsyncIterator[str]]


class ScriptedProvider:
    name = FAKE_PROVIDER

    def __init__(self, script: Script) -> None:
        self._script = script
        self.calls: list[list[dict[str, str]]] = []
        self.models_seen: list[str] = []

    async def generate(self, *, model, messages, cancel):
        self.calls.append(messages)
        self.models_seen.append(model)
        async for piece in self._script(messages, cancel):
            yield piece


def replying(text: str) -> ScriptedProvider:
    async def script(messages, cancel):
        yield text

    return ScriptedProvider(script)


def failing_with_provider_error() -> ScriptedProvider:
    async def script(messages, cancel):
        raise ProviderError("provider rejected the request")
        yield  # pragma: no cover

    return ScriptedProvider(script)


def crashing() -> ScriptedProvider:
    async def script(messages, cancel):
        raise RuntimeError("unexpected failure: internal-detail-99")
        yield  # pragma: no cover

    return ScriptedProvider(script)


def timing_out() -> ScriptedProvider:
    async def script(messages, cancel):
        await asyncio.sleep(5)
        yield "too late"

    return ScriptedProvider(script)


def empty() -> ScriptedProvider:
    async def script(messages, cancel):
        if False:  # pragma: no cover
            yield ""

    return ScriptedProvider(script)


def word_by_word(text: str, delay: float = 0.001) -> ScriptedProvider:
    """Yields one word at a time, pausing `delay` seconds before each, and honours cancellation."""

    async def script(messages, cancel):
        for word in text.split(" "):
            if cancel.is_set():
                return
            await asyncio.sleep(delay)
            yield word + " "

    return ScriptedProvider(script)


def sleeping(text: str, seconds: float) -> ScriptedProvider:
    """Waits `seconds` before answering. Used to hold a turn open while other requests arrive."""

    async def script(messages, cancel):
        await asyncio.sleep(seconds)
        yield text

    return ScriptedProvider(script)


def echoing() -> ScriptedProvider:
    """Replies with the last user turn, exactly as the model received it. Test double only."""

    async def script(messages, cancel):
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        yield last_user

    return ScriptedProvider(script)


def flaky_then_echo() -> ScriptedProvider:
    """Fails the first call, then echoes. Lets a test check that retry works. Test double only."""
    calls = {"n": 0}

    async def script(messages, cancel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("first attempt fails")
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        yield last_user

    return ScriptedProvider(script)


def counting() -> ScriptedProvider:
    """Replies with how many messages the model was given, so a test can see conversation context."""

    async def script(messages, cancel):
        yield f"{len(messages)} messages in context"

    return ScriptedProvider(script)


def settings_for_tests(**overrides) -> Settings:
    return Settings(model_routes=ROUTES, **overrides)


def app_with(provider: ScriptedProvider, **overrides) -> FastAPI:
    return create_app(settings_for_tests(**overrides), providers={FAKE_PROVIDER: provider})


def client_with(provider: ScriptedProvider, **overrides) -> TestClient:
    return TestClient(app_with(provider, **overrides))


def router_with(provider: ScriptedProvider) -> ModelRouter:
    return build_model_router(ROUTES, {FAKE_PROVIDER: provider})
