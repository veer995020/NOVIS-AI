"""Root-level paths for the chat page: POST /chat, /research, /code, /write, and GET /models.

They reach the same handlers as the /api routes, so behaviour cannot differ. They are left out of the
OpenAPI schema, so the documented surface stays the one in CONTRACT.md.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.routes.chat import chat as chat_handler
from backend.routes.models import list_models
from backend.routes.tasks import make_task_endpoint
from backend.services.tasks import TASK_SPECS

router = APIRouter()

router.add_api_route(
    "/chat",
    chat_handler,
    methods=["POST"],
    include_in_schema=False,
    name="root_chat",
)
for _spec in TASK_SPECS:
    router.add_api_route(
        f"/{_spec.name}",
        make_task_endpoint(_spec),
        methods=["POST"],
        include_in_schema=False,
        name=f"root_{_spec.name}",
    )
router.add_api_route("/models", list_models, methods=["GET"], include_in_schema=False, name="root_models")
