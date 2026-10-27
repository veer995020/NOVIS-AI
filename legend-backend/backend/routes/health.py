"""Health endpoints. They expose status words, the version, and uptime only.

GET /health         aggregated status with component checks (200 unless down, then 503)
GET /health/live    the process is running (always 200 while the process is up)
GET /health/ready   the app has started and is not shutting down (200 or 503)
"""
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.schemas.health import CheckStatus, HealthReport, ProbeReport

router = APIRouter(tags=["health"])

_CONVERSATION_DEGRADED_AT = 0.9


def _uptime_seconds(request: Request) -> float:
    started = getattr(request.app.state, "started_at", None)
    return round(time.monotonic() - started, 1) if started is not None else 0.0


async def _conversations_have_room(request: Request) -> bool:
    count = await request.app.state.conversation_store.count()
    return count < _CONVERSATION_DEGRADED_AT * request.app.state.settings.max_conversations


@router.get(
    "/health",
    response_model=HealthReport,
    summary="Service health with component checks",
    responses={503: {"model": HealthReport, "description": "down: not started, or shutting down."}},
)
async def health(request: Request) -> JSONResponse:
    state = request.app.state
    ready = bool(getattr(state, "ready", False))
    checks: dict[str, CheckStatus] = {
        "server": "ok" if ready else "down",
        "models": "ok" if state.model_router.available_models() else "degraded",
        "conversations": "ok" if await _conversations_have_room(request) else "degraded",
    }
    overall: Literal["ok", "degraded", "down"]
    if not ready:
        overall, code = "down", 503
    elif all(value == "ok" for value in checks.values()):
        overall, code = "ok", 200
    else:
        overall, code = "degraded", 200
    report = HealthReport(
        status=overall,
        version=state.settings.version,
        uptime_seconds=_uptime_seconds(request),
        checks=checks,
    )
    return JSONResponse(report.model_dump(mode="json"), status_code=code)


@router.get("/health/live", response_model=ProbeReport, summary="Process liveness")
async def liveness(request: Request) -> ProbeReport:
    return ProbeReport(
        status="ok",
        version=request.app.state.settings.version,
        uptime_seconds=_uptime_seconds(request),
    )


@router.get(
    "/health/ready",
    response_model=ProbeReport,
    summary="Readiness for traffic",
    responses={503: {"model": ProbeReport, "description": "not_ready: starting or shutting down."}},
)
async def readiness(request: Request) -> JSONResponse:
    state = request.app.state
    ready = bool(getattr(state, "ready", False))
    report = ProbeReport(
        status="ready" if ready else "not_ready",
        version=state.settings.version,
        uptime_seconds=_uptime_seconds(request),
    )
    return JSONResponse(report.model_dump(mode="json"), status_code=200 if ready else 503)
