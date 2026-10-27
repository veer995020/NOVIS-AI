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


def main() -> None:
    print("Conversation store, cost of one get():")
    for conversations in (100, 1_000, 5_000):
        print(f"  {conversations:>5} conversations: {asyncio.run(store_get_cost(conversations)):7.2f} us")
    print("Request path, GET /api/models over the ASGI transport:")
    print(f"  rate limit off: {asyncio.run(request_throughput(0)):7.0f} req/s")
    print(f"  rate limit on : {asyncio.run(request_throughput(100_000)):7.0f} req/s")


if __name__ == "__main__":
    main()
