"""Async behaviour under real concurrency: many requests on one event loop."""
import asyncio

import httpx

from backend.config import Settings
from backend.main import create_app
from backend.tests.fakes import app_with, sleeping


def _run(coro):
    return asyncio.run(coro)


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_concurrent_turns_on_one_conversation_only_one_runs() -> None:
    async def scenario():
        app = app_with(sleeping("reply", 0.3))
        async with await _client(app) as client:
            cid = (await client.post("/api/conversations", json={})).json()["id"]
            results = await asyncio.gather(
                *(client.post("/api/chat", json={"conversation_id": cid, "message": f"m{i}"}) for i in range(5))
            )
            history = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
        return [r.status_code for r in results], history

    codes, history = _run(scenario())
    assert sorted(codes) == [200, 409, 409, 409, 409]
    # One turn committed: exactly one question and one answer, never interleaved.
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_concurrent_turns_on_separate_conversations_all_succeed() -> None:
    async def scenario():
        app = app_with(sleeping("reply", 0.2))
        async with await _client(app) as client:
            responses = await asyncio.gather(
                *(client.post("/api/chat", json={"message": f"m{i}"}) for i in range(5))
            )
        return [r.status_code for r in responses], [r.json()["content"] for r in responses]

    codes, contents = _run(scenario())
    assert codes == [200] * 5
    assert contents == ["reply"] * 5


def test_turn_cap_holds_under_concurrency() -> None:
    async def scenario():
        app = app_with(sleeping("reply", 0.3), max_concurrent_turns=2)
        async with await _client(app) as client:
            responses = await asyncio.gather(
                *(client.post("/api/chat", json={"message": f"m{i}"}) for i in range(5))
            )
        return sorted(r.status_code for r in responses), [r.json() for r in responses]

    codes, bodies = _run(scenario())
    assert codes.count(200) == 2
    assert codes.count(503) == 3
    refused = [b for b in bodies if b.get("error") and b["error"]["code"] == "capacity_exceeded"]
    assert len(refused) == 3


def test_a_request_during_a_long_turn_is_served_promptly() -> None:
    async def scenario():
        app = app_with(sleeping("slow reply", 1.0))
        async with await _client(app) as client:
            slow = asyncio.create_task(client.post("/api/chat", json={"message": "slow"}))
            await asyncio.sleep(0.05)
            loop = asyncio.get_running_loop()
            started = loop.time()
            quick = await client.get("/api/models")
            elapsed = loop.time() - started
            await slow
        return quick.status_code, elapsed

    status, elapsed = _run(scenario())
    assert status == 200
    assert elapsed < 0.5  # the event loop was not blocked by the slow turn


def test_async_app_without_routes_is_still_correct_under_load() -> None:
    async def scenario():
        app = create_app(Settings())
        async with await _client(app) as client:
            responses = await asyncio.gather(*(client.post("/api/chat", json={"message": "x"}) for _ in range(20)))
        return [r.status_code for r in responses]

    assert _run(scenario()) == [503] * 20
