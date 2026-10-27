"""Model catalogue. Lists public model ids and whether each one can be used now.
It never reveals which provider serves a model or the provider's model name."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelInfo(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str = Field(description="Use this value in the `model` field.")
    name: str = Field(description="Display name.")
    available: bool = Field(description="False when the model has no route in this deployment.")
    default: bool = Field(description="True for the model used when `model` is omitted.")


class ModelList(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    models: list[ModelInfo]
    default_model: str
