"""QA matrix for the provider pipeline: general, reasoning, coding, maths, long conversations, context,
structured output, API failures, rate limits, streaming, and concurrency.

These tests use the scripted provider, so they check the pipeline, not the quality of any model. Quality
metrics (accuracy, reasoning, coding and maths correctness, hallucination rate) need a real model and are
not measured here. See QA_REPORT.md.
"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.schemas.response import ChatResponse, ChatStatus
from backend.services.providers.base import (
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from backend.tests.fakes import (
    ScriptedProvider,
    app_with,
    counting,
    echoing,
    replying,
    sleeping,
    word_by_word,
)

LEAK_MARKERS = ("internal-detail-99", "Traceback", "RuntimeError", "File \"")


def _parse_stream(text: str) -> list[dict]:
    events = []
    for frame in text.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def _recording(script) -> ScriptedProvider:
    return ScriptedProvider(script)


# ---- 1. general questions: the reply reaches the client exactly as the model produced it ---------------

GENERAL_REPLIES = [
    "Paris is the capital of France.",
    "Water boils at 100 degrees Celsius at sea level.",
    "Café, naïve, Zürich, 東京, emoji 🙂 — all survive.",
    "Line one\nLine two\n\n- a bullet\n- another",
    "A" * 3000,
]


@pytest.mark.parametrize("text", GENERAL_REPLIES, ids=["short", "fact", "unicode", "markdown", "long"])
def test_general_reply_reaches_the_client_unchanged(text: str) -> None:
    body = TestClient(app_with(replying(text))).post("/api/chat", json={"message": "question"}).json()
    assert body["status"] == "success"
    assert body["content"] == text


# ---- 2. complex reasoning, 3. coding, 4. mathematics: the model's output is delivered byte-for-byte -----

REASONING_AND_MATHS = [
    "Step 1: assume x = 2. Step 2: then 2x + 3 = 7, so x = 2. Therefore the claim holds.",
    "Let f(x) = x^2. Then f'(x) = 2x and f''(x) = 2, which is positive, so f is convex.",
    "\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}   (proof by induction omitted)",
    "∑ᵢ₌₁ⁿ i = n(n+1)/2, and for n = 10 the sum is 55.",
]

CODE_REPLIES = [
    "```python\ndef add(a, b):\n    return a + b\n\nprint(add(2, 3))  # 5\n```",
    "```js\nconst s = \"quote's \\\"inside\\\"\";\nconsole.log(s);\n```",
    "SELECT name FROM users WHERE id = 1; -- returns one row",
    "```json\n{\"ok\": true, \"items\": [1, 2, 3], \"nested\": {\"k\": null}}\n```",
]


@pytest.mark.parametrize("text", REASONING_AND_MATHS + CODE_REPLIES, ids=lambda t: t[:24])
def test_reasoning_maths_and_code_survive_the_pipeline_unchanged(text: str) -> None:
    body = TestClient(app_with(replying(text))).post("/api/chat", json={"message": "solve it"}).json()
    assert body["status"] == "success"
    assert body["content"] == text


@pytest.mark.parametrize("mode", ["general", "research", "coding", "writing"])
def test_each_mode_gives_the_model_a_distinct_system_prompt_and_the_users_words(mode: str) -> None:
    provider = counting()
    client = TestClient(app_with(provider))
    question = "Write a Python function that reverses a string."
    body = client.post("/api/chat", json={"message": question, "mode": mode}).json()
    assert body["status"] == "success"
    sent = provider.calls[-1]
    assert sent[0]["role"] == "system" and sent[0]["content"].strip()
    assert sent[-1]["role"] == "user" and question in sent[-1]["content"]


def test_the_system_prompt_differs_between_modes() -> None:
    prompts = {}
    for mode in ("general", "research", "coding", "writing"):
        provider = counting()
        TestClient(app_with(provider)).post("/api/chat", json={"message": "hi", "mode": mode})
        prompts[mode] = provider.calls[-1][0]["content"]
    assert len(set(prompts.values())) == 4


# ---- 5. long conversations: the prompt stays bounded, the stored history is bounded ------------------

def test_a_long_conversation_keeps_the_prompt_bounded_and_the_history_capped() -> None:
    provider = counting()
    settings_limit_context, settings_limit_history = 6, 12
    client = TestClient(app_with(provider, max_context_messages=settings_limit_context,
                                 max_history_messages=settings_limit_history,
                                 rate_limit_generation_per_minute=0))
    conversation_id = None
    for turn in range(40):
        payload = {"message": f"turn {turn}"}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        body = client.post("/api/chat", json=payload).json()
        assert body["status"] == "success", body
        conversation_id = body["conversation_id"]
        assert len(provider.calls[-1]) <= settings_limit_context + 1  # the context, plus the system prompt
    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert len(stored["messages"]) == settings_limit_history
    assert stored["messages"][-1]["role"] == "assistant"


# ---- 6. context retention: a later turn's prompt contains the earlier turns, in order -----------------

def test_later_turns_are_given_the_earlier_turns_in_order() -> None:
    async def script(messages, cancel):
        answers = sum(1 for m in messages if m["role"] == "user")
        yield f"answer-{answers}"

    provider = _recording(script)
    client = TestClient(app_with(provider))
    first = client.post("/api/chat", json={"message": "question-one"}).json()
    second = client.post("/api/chat", json={"message": "question-two", "conversation_id": first["conversation_id"]}).json()
    third = client.post("/api/chat", json={"message": "question-three", "conversation_id": first["conversation_id"]}).json()
    assert (first["content"], second["content"], third["content"]) == ("answer-1", "answer-2", "answer-3")
    third_prompt = [m["content"] for m in provider.calls[-1] if m["role"] != "system"]
    assert third_prompt[:4] == ["question-one", "answer-1", "question-two", "answer-2"][:len(third_prompt[:4])]
    assert "question-one" in third_prompt[0] and "question-two" in third_prompt[2]


def test_two_conversations_never_see_each_others_messages() -> None:
    provider = echoing()
    client = TestClient(app_with(provider))
    a = client.post("/api/chat", json={"message": "secret-of-A"}).json()
    client.post("/api/chat", json={"message": "secret-of-B"})
    client.post("/api/chat", json={"message": "follow-up", "conversation_id": a["conversation_id"]})
    prompt = json.dumps(provider.calls[-1])
    assert "secret-of-A" in prompt and "secret-of-B" not in prompt


# ---- 7. structured outputs: every response body is a valid ChatResponse; JSON in replies round-trips ---

def test_success_bodies_validate_against_the_response_schema() -> None:
    body = TestClient(app_with(replying('{"a": 1}'))).post("/api/chat", json={"message": "x"}).json()
    parsed = ChatResponse.model_validate_json(json.dumps(body))
    assert parsed.status is ChatStatus.SUCCESS
    assert json.loads(parsed.content) == {"a": 1}


@pytest.mark.parametrize(
    "provider, overrides",
    [(ScriptedProvider(lambda m, c: (_ for _ in ()).throw(ProviderError("x"))), {}),
     (ScriptedProvider(lambda m, c: (_ for _ in ()).throw(RuntimeError("boom"))), {})],
)
def test_error_bodies_validate_against_the_response_schema(provider, overrides) -> None:
    response = TestClient(app_with(provider, provider_max_attempts=1)).post("/api/chat", json={"message": "x"})
    parsed = ChatResponse.model_validate_json(response.text)
    assert parsed.status is ChatStatus.ERROR and parsed.error is not None and parsed.content is None


def test_a_request_with_an_unknown_field_is_refused_with_a_structured_error() -> None:
    response = TestClient(app_with(echoing())).post("/api/chat", json={"message": "x", "surprise": 1})
    assert response.status_code == 422
    ChatResponse.model_validate_json(response.text)


# ---- 8. API failures: the right status, the right code, and no internal detail in the body -------------

@pytest.mark.parametrize(
    "provider, status, code",
    [
        (ScriptedProvider(lambda m, c: (_ for _ in ()).throw(ProviderError("first"))), 502, "GENERATION_FAILED"),
        (ScriptedProvider(lambda m, c: (_ for _ in ()).throw(RuntimeError("internal-detail-99"))), 502, "GENERATION_FAILED"),
        (ScriptedProvider(lambda m, c: (_ for _ in ()).throw(ProviderUnavailableError("down"))), 502, "GENERATION_FAILED"),
    ],
    ids=["provider-error", "crash", "upstream-unavailable"],
)
def test_upstream_failures_return_the_right_status_without_leaking_detail(provider, status, code) -> None:
    response = TestClient(app_with(provider, provider_max_attempts=1)).post("/api/chat", json={"message": "x"})
    assert response.status_code == status
    body = response.text
    assert response.json()["error"]["code"] == code
    for marker in LEAK_MARKERS:
        assert marker not in body


def test_a_timeout_returns_504_generation_timeout() -> None:
    client = TestClient(app_with(sleeping("late", 5), generation_timeout_seconds=1))
    response = client.post("/api/chat", json={"message": "x"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "GENERATION_TIMEOUT"


def test_an_empty_reply_is_an_error_not_a_success() -> None:
    body = TestClient(app_with(replying(""))).post("/api/chat", json={"message": "x"})
    assert body.status_code == 502 and body.json()["status"] == "error"


def test_a_reply_over_the_size_limit_is_refused() -> None:
    from backend.services.chat import MAX_REPLY_CHARS

    response = TestClient(app_with(replying("x" * (MAX_REPLY_CHARS + 1)))).post("/api/chat", json={"message": "x"})
    assert response.status_code == 502 and response.json()["status"] == "error"


# ---- 9. retries: transient provider failures are retried, within the configured bound ------------------

def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    calls = {"n": 0}

    async def script(messages, cancel):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderUnavailableError("blip")
        yield "recovered"

    body = TestClient(app_with(ScriptedProvider(script), provider_max_attempts=3)).post(
        "/api/chat", json={"message": "x"}).json()
    assert body["status"] == "success" and body["content"] == "recovered"
    assert calls["n"] == 3


def test_retries_stop_at_the_configured_bound() -> None:
    calls = {"n": 0}

    async def script(messages, cancel):
        calls["n"] += 1
        raise ProviderUnavailableError("down")
        yield ""

    response = TestClient(app_with(ScriptedProvider(script), provider_max_attempts=2)).post(
        "/api/chat", json={"message": "x"})
    assert response.status_code == 502
    assert calls["n"] == 2


def test_a_rejected_request_is_never_retried() -> None:
    calls = {"n": 0}

    async def script(messages, cancel):
        calls["n"] += 1
        raise ProviderError("bad request")
        yield ""

    TestClient(app_with(ScriptedProvider(script), provider_max_attempts=3)).post("/api/chat", json={"message": "x"})
    assert calls["n"] == 1


# ---- 10. rate limits: upstream and our own ------------------------------------------------------------

def test_an_upstream_rate_limit_is_reported_as_429_with_retry_after() -> None:
    async def script(messages, cancel):
        raise ProviderRateLimitedError("slow down", retry_after=2)
        yield ""

    response = TestClient(app_with(ScriptedProvider(script), provider_max_attempts=1)).post(
        "/api/chat", json={"message": "x"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["retry-after"]) >= 1


def test_our_own_generation_limit_returns_429_with_retry_after() -> None:
    client = TestClient(app_with(echoing(), rate_limit_generation_per_minute=3))
    statuses = [client.post("/api/chat", json={"message": f"m{i}"}).status_code for i in range(4)]
    assert statuses[:3] == [200, 200, 200] and statuses[3] == 429
    last = client.post("/api/chat", json={"message": "again"})
    assert last.status_code == 429 and int(last.headers["retry-after"]) >= 1


# ---- 11. streaming: ordered events, deltas that add up to the reply, a clean error when it fails --------

def test_a_stream_is_start_then_deltas_then_done_and_the_deltas_add_up() -> None:
    client = TestClient(app_with(word_by_word("one two three four five")))
    with client.stream("POST", "/api/chat/stream", json={"message": "x"}) as response:
        assert response.status_code == 200
        events = _parse_stream(response.read().decode())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert all(k == "delta" for k in kinds[1:-1]) and len(kinds) > 3
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    done = events[-1]["response"]
    expected = "".join(word + " " for word in "one two three four five".split())
    assert done["status"] == "success" and done["content"] == text == expected


def test_a_stream_that_fails_mid_reply_ends_with_an_error_in_done() -> None:
    async def script(messages, cancel):
        yield "partial "
        raise RuntimeError("internal-detail-99")

    client = TestClient(app_with(ScriptedProvider(script)))
    with client.stream("POST", "/api/chat/stream", json={"message": "x"}) as response:
        assert response.status_code == 200
        raw = response.read().decode()
    done = _parse_stream(raw)[-1]
    assert done["type"] == "done" and done["response"]["status"] == "error"
    assert done["response"]["error"]["code"] == "GENERATION_FAILED"
    assert "internal-detail-99" not in raw


def test_stopping_an_unknown_stream_is_a_404() -> None:
    response = TestClient(app_with(echoing())).post("/api/chat/stop/" + "a" * 32)
    assert response.status_code == 404


# ---- 12. concurrency ----------------------------------------------------------------------------------

def _post_many(app, payloads):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return await asyncio.gather(*(client.post("/api/chat", json=p) for p in payloads))

    return asyncio.run(run())


def test_concurrent_turns_in_one_conversation_are_serialised() -> None:
    app = app_with(sleeping("slow reply", 0.3))
    with TestClient(app) as client:
        conversation = client.post("/api/conversations", json={}).json()
    responses = _post_many(app, [{"message": f"m{i}", "conversation_id": conversation["id"]} for i in range(20)])
    successes = [r for r in responses if r.status_code == 200]
    busy = [r for r in responses if r.status_code == 409]
    assert len(successes) >= 1 and len(successes) + len(busy) == 20
    assert all(r.status_code in (200, 409) for r in responses)
    assert all((r.json().get("error") or {}).get("code") in (None, "CONVERSATION_BUSY") for r in busy)


def test_concurrent_turns_in_separate_conversations_all_succeed() -> None:
    responses = _post_many(app_with(echoing()), [{"message": f"m{i}"} for i in range(25)])
    assert all(r.status_code == 200 and r.json()["status"] == "success" for r in responses)
    assert len({r.json()["conversation_id"] for r in responses}) == 25


def test_the_server_turn_limit_refuses_the_overflow_with_capacity_exceeded() -> None:
    app = app_with(sleeping("held", 0.6), max_concurrent_turns=2)
    responses = _post_many(app, [{"message": f"m{i}"} for i in range(6)])
    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(200) >= 2
    refused = [r for r in responses if r.status_code != 200]
    assert refused and all(r.status_code == 503 for r in refused)
    assert all(r.json()["error"]["code"] == "CAPACITY_EXCEEDED" for r in refused)
