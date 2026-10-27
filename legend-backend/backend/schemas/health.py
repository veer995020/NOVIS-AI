"""Health responses. They carry only status words, the version, and uptime.
No environment names, provider names, model routes, counts of users, or errors."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CheckStatus = Literal["ok", "degraded", "down"]


class HealthReport(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    status: Literal["ok", "degraded", "down"] = Field(
        description="ok: all checks pass. degraded: serving, but a check is not ok. down: not serving."
    )
    version: str
    uptime_seconds: float = Field(ge=0)
    checks: dict[str, CheckStatus] = Field(
        description="server: ok when running. models: degraded when no model has a route. "
        "conversations: degraded above 90% of the conversation limit."
    )


class ProbeReport(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    status: Literal["ok", "ready", "not_ready"]
    version: str
    uptime_seconds: float = Field(ge=0)
