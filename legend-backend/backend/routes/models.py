from __future__ import annotations

from fastapi import APIRouter, Request

from backend.config import DEFAULT_MODEL, DEFAULT_MODELS, MODEL_DISPLAY_NAMES
from backend.schemas.models import ModelInfo, ModelList

router = APIRouter(prefix="/api", tags=["models"])


@router.get(
    "/models",
    response_model=ModelList,
    summary="List models and whether each is available",
    description=(
        "Every model id the API accepts, with whether it can be used in this deployment. "
        "Use the id in the `model` field. Provider names are never shown."
    ),
)
async def list_models(request: Request) -> ModelList:
    available = set(request.app.state.model_router.available_models())
    return ModelList(
        models=[
            ModelInfo(
                id=model_id,
                name=MODEL_DISPLAY_NAMES[model_id],
                available=model_id in available,
                default=model_id == DEFAULT_MODEL,
            )
            for model_id in DEFAULT_MODELS
        ],
        default_model=DEFAULT_MODEL,
    )
