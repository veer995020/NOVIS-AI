"""Benchmarks for the conversation store and the request path.

Run from the project root:  python scripts/benchmark.py
Results vary with the machine. Compare runs on the same machine.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.services.conversation_store import ConversationStore  # noqa: E402
from backend.tests.fakes import app_with, replying  # noqa: E402


async def store_get_cost(conversations: int, operations: int = 20_000) -> float:
    store = ConversationStore(max_messages=200, max_conversations=conversations + 10, idle_seconds=3600)
    ids = [(await store.create()).id for _ in range(conversations)]
    start = time.perf_counter()
    for index in range(operations):
        await store.get(ids[index % conversations])
    return (time.perf_counter() - start) / operations * 1e6


async def request_throughput(rate_limit: int, requests: int = 3_000) -> float:
    # WARNING keeps per-request logging out of the measurement.
    app = create_app(Settings(rate_limit_per_minute=rate_limit, log_level="WARNING"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench") as client:
            await client.get("/api/models")
            start = time.perf_counter()
            for _ in range(requests):
                await client.get("/api/models")
            return requests / (time.perf_counter() - start)


async def chat_latency(message_chars: int, requests: int = 1_000, attachments: list | None = None) -> float:
    """Median-free mean latency of POST /api/chat with a short provider reply, in microseconds."""
    app = app_with(replying("ok"), rate_limit_per_minute=0, rate_limit_generation_per_minute=0)
    body = {"message": "x" * message_chars, "attachments": attachments or []}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench") as client:
            for _ in range(50):
                await client.post("/api/chat", json=body)
            start = time.perf_counter()
            for _ in range(requests):
                response = await client.post("/api/chat", json=body)
            elapsed = time.perf_counter() - start
            assert response.status_code == 200, response.text[:200]
            return elapsed / requests * 1e6


def large_request_latency(requests: int = 50) -> float:
    """The largest legal request: two full text attachments. Milliseconds per request."""
    files = [{"name": f"f{i}.txt", "media_type": "text/plain", "content": "line of text 0123456789\n" * 2560}
             for i in range(2)]
    return asyncio.run(chat_latency(200, requests=requests, attachments=[{**f, "content": f["content"][:64000]} for f in files])) / 1000


def main() -> None:
    print("Conversation store, cost of one get():")
    for conversations in (100, 1_000, 5_000):
        print(f"  {conversations:>5} conversations: {asyncio.run(store_get_cost(conversations)):7.2f} us")
    print("Request path, GET /api/models over the ASGI transport:")
    print(f"  rate limit off: {asyncio.run(request_throughput(0)):7.0f} req/s")
    print(f"  rate limit on : {asyncio.run(request_throughput(100_000)):7.0f} req/s")
    print("Request path, POST /api/chat (400-character message, test provider):")
    print(f"  {asyncio.run(chat_latency(400)):7.0f} us per request")
    print("Largest legal request (two 64,000-character attachments):")
    print(f"  {large_request_latency():7.2f} ms per request")


if __name__ == "__main__":
    main()
