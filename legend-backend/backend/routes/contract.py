"""GET /api/contract: the published contract. The chat page reads its limits from here,
so the browser and the server always agree. Public: it contains no data and no secrets."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter

from backend.schemas.contract import build_contract

router = APIRouter(prefix="/api", tags=["meta"])


@lru_cache(maxsize=1)
def _contract() -> dict[str, Any]:
    return build_contract()


@router.get(
    "/contract",
    summary="The API contract: limits, defaults, schemas, and error formats",
    description="Public and read-only. The chat page uses it to enforce the same limits as the server.",
)
async def contract() -> dict[str, Any]:
    return _contract()
