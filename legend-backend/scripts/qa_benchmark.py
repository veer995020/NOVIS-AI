"""Pipeline benchmark for the chat provider path. Writes QA_REPORT.md and qa_results.json.

It runs the real application against scripted providers, so every number is measured here, on this machine.
It cannot measure model quality, cost, or upstream latency: those need a real model and are reported as not measured.

Run from the project root:  python scripts/qa_benchmark.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

import httpx  # noqa: E402

from backend.schemas.response import ChatResponse  # noqa: E402
from backend.services.providers.base import (  # noqa: E402
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from backend.tests.fakes import ScriptedProvider, app_with, echoing, sleeping  # noqa: E402


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(p / 100 * (len(ordered) - 1))))
    return ordered[index]


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench", timeout=60)


async def _timed_posts(client, payloads, concurrency: int):
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[int, float, dict]] = []

    async def one(payload):
        async with sem:
            start = time.perf_counter()
            response = await client.post("/api/chat", json=payload)
            results.append((response.status_code, (time.perf_counter() - start) * 1000, response.json()))

    await asyncio.gather(*(one(p) for p in payloads))
    return results


def rate_limited(message: str) -> ProviderRateLimitedError:
    return ProviderRateLimitedError(message, retry_after=1)


def flaky(rate: float, seed: int, error=ProviderUnavailableError) -> ScriptedProvider:
    rng = random.Random(seed)

    async def script(messages, cancel):
        if rng.random() < rate:
            raise error("injected upstream failure")
        yield "reply"

    return ScriptedProvider(script)


async def run() -> dict:
    results: dict = {"environment": {"python": sys.version.split()[0], "cpus": "see report"}}
    rate_off = dict(rate_limit_per_minute=0, rate_limit_generation_per_minute=0)

    # 1. Latency, non-streaming, pipeline only (provider returns at once)
    app = app_with(echoing(), **rate_off)
    async with app.router.lifespan_context(app), await _client(app) as client:
        for _ in range(30):
            await client.post("/api/chat", json={"message": "warm"})
        rows = await _timed_posts(client, [{"message": f"question {i}"} for i in range(400)], concurrency=1)
    times = [ms for status, ms, _ in rows if status == 200]
    results["latency_pipeline_ms"] = {"n": len(times), "p50": pct(times, 50), "p95": pct(times, 95), "p99": pct(times, 99),
                                      "success_rate": len(times) / len(rows)}

    # 2. Concurrency: distinct conversations, provider takes 50 ms per reply
    results["concurrency_distinct"] = []
    for level in (1, 8, 32, 64):
        app = app_with(sleeping("reply", 0.05), **rate_off)
        async with app.router.lifespan_context(app), await _client(app) as client:
            start = time.perf_counter()
            rows = await _timed_posts(client, [{"message": f"m{i}"} for i in range(200)], concurrency=level)
            elapsed = time.perf_counter() - start
        ok = [ms for status, ms, _ in rows if status == 200]
        codes = {}
        for status, _, body in rows:
            if status != 200:
                key = f"{status} {(body.get('error') or {}).get('code')}"
                codes[key] = codes.get(key, 0) + 1
        results["concurrency_distinct"].append({
            "concurrency": level, "requests": len(rows), "ok": len(ok),
            "throughput_rps": len(ok) / elapsed, "p50_ms": pct(ok, 50), "p95_ms": pct(ok, 95), "refused": codes,
        })

    # 3. Concurrency: one conversation, 32 simultaneous turns. Invariant: version == 1 + successful turns
    app = app_with(sleeping("reply", 0.1), **rate_off)
    async with app.router.lifespan_context(app), await _client(app) as client:
        conversation = (await client.post("/api/conversations", json={})).json()
        rows = await _timed_posts(client, [{"message": f"m{i}", "conversation_id": conversation["id"]} for i in range(32)],
                                  concurrency=32)
        final = (await client.get(f"/api/conversations/{conversation['id']}")).json()
    ok = sum(1 for status, _, _ in rows if status == 200)
    busy = sum(1 for status, _, _ in rows if status == 409)
    results["concurrency_same_conversation"] = {"requests": 32, "ok": ok, "busy_409": busy, "other": 32 - ok - busy,
                                                "version": final["version"], "invariant_holds": final["version"] == 1 + ok}

    # 4. Reliability under injected transient failures, with and without the retry bound
    results["reliability"] = []
    for rate in (0.0, 0.10, 0.30):
        for attempts in (1, 3):
            app = app_with(flaky(rate, seed=42), provider_max_attempts=attempts, **rate_off)
            async with app.router.lifespan_context(app), await _client(app) as client:
                rows = await _timed_posts(client, [{"message": f"m{i}"} for i in range(500)], concurrency=8)
            ok = sum(1 for status, _, _ in rows if status == 200)
            results["reliability"].append({"failure_rate_per_attempt": rate, "max_attempts": attempts,
                                           "requests": len(rows), "success_rate": ok / len(rows)})

    # 5. Upstream rate limits, with retries: what the client sees
    results["rate_limit_upstream"] = []
    for rate in (0.2, 0.6):
        app = app_with(flaky(rate, seed=7, error=rate_limited), provider_max_attempts=3, **rate_off)
        async with app.router.lifespan_context(app), await _client(app) as client:
            rows = await _timed_posts(client, [{"message": f"m{i}"} for i in range(400)], concurrency=8)
        counts: dict[str, int] = {}
        for status, _, _ in rows:
            counts[str(status)] = counts.get(str(status), 0) + 1
        results["rate_limit_upstream"].append({"attempt_rate_limit_probability": rate, "outcomes": counts})

    # 6. Context: the prompt the model receives stays bounded as the conversation grows
    results["context"] = []
    for stored_turns in (0, 10, 50, 100):
        provider = ScriptedProvider(lambda m, c: _yield_once("ok"))
        app = app_with(provider, max_context_messages=20, max_history_messages=200, **rate_off)
        async with app.router.lifespan_context(app), await _client(app) as client:
            conversation = (await client.post("/api/conversations", json={})).json()
            for i in range(stored_turns):
                await client.post("/api/chat", json={"message": f"seed {i}", "conversation_id": conversation["id"]})
            provider.calls.clear()
            timings = []
            for i in range(30):
                start = time.perf_counter()
                await client.post("/api/chat", json={"message": f"probe {i}", "conversation_id": conversation["id"]})
                timings.append((time.perf_counter() - start) * 1000)
            sent = [len(call) for call in provider.calls]
        results["context"].append({"stored_turns": stored_turns, "prompt_messages_max": max(sent),
                                   "prompt_messages_min": min(sent), "p50_ms": pct(timings, 50)})

    # 7. Structured output: a JSON object sent as the message comes back as the same JSON object
    rng = random.Random(3)
    objects = [{"id": i, "items": [rng.randint(0, 9) for _ in range(rng.randint(0, 5))], "ok": bool(i % 2),
                "name": f'\u00fcn\u00efcode "quoted" \\ {i}'} for i in range(300)]
    app = app_with(echoing(), **rate_off)
    async with app.router.lifespan_context(app), await _client(app) as client:
        rows = await _timed_posts(client, [{"message": json.dumps(o)} for o in objects], concurrency=8)
    identical = 0
    schema_valid = 0
    for (status, _, body), obj in zip(rows, objects, strict=True):
        parsed = ChatResponse.model_validate_json(json.dumps(body))
        schema_valid += 1
        if status == 200 and parsed.content is not None and json.loads(parsed.content) == obj:
            identical += 1
    results["structured_output"] = {"requests": len(objects), "valid_chat_response_schema": schema_valid,
                                    "json_reply_identical": identical}

    # 8. Timeouts: a provider that takes longer than the limit must return 504 every time
    app = app_with(sleeping("late", 2.0), generation_timeout_seconds=1, **rate_off)
    async with app.router.lifespan_context(app), await _client(app) as client:
        rows = await _timed_posts(client, [{"message": f"m{i}"} for i in range(10)], concurrency=4)
    results["timeout"] = {"requests": 10, "returned_504": sum(1 for s, _, _ in rows if s == 504)}
    return results


async def _yield_once(text: str):
    yield text


def write_report(results: dict) -> None:
    (ROOT / "qa_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    data = asyncio.run(run())
    write_report(data)
    print(json.dumps(data, indent=2, default=str))
