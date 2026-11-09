"""POST /api/research, /api/code, and /api/write.

Each endpoint is built from its TaskSpec and runs the shared chat flow with a
fixed mode. Validation, timeouts, rollback, status codes, and the response
shape are therefore identical to POST /api/chat.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.routes.responses import CHAT_RESPONSES, chat_json_response
from backend.schemas.request import TaskRequest
from backend.schemas.response import ChatResponse
from backend.services.tasks import TASK_SPECS, TaskSpec, to_chat_request

router = APIRouter(tags=["tasks"])


def make_task_endpoint(spec: TaskSpec) -> Callable[[TaskRequest, Request], Awaitable[JSONResponse]]:
    async def endpoint(payload: TaskRequest, request: Request) -> JSONResponse:
        if "mode" in payload.model_fields_set and payload.mode is None:
            raise RequestValidationError(
                [{"type": "string_type", "loc": ("body", "mode"), "msg": "Input should be a valid string", "input": None}]
            )
        if payload.mode is not None and payload.mode != spec.mode:
            raise RequestValidationError(
                [{
                    "type": "literal_error",
                    "loc": ("body", "mode"),
                    "msg": f"Input should be '{spec.mode}' on this endpoint",
                    "input": payload.mode,
                }]
            )
        service = request.app.state.chat_service
        response = await service.complete(to_chat_request(payload, spec))
        return chat_json_response(response)

    return endpoint


for _spec in TASK_SPECS:
    router.add_api_route(
        _spec.path,
        make_task_endpoint(_spec),
        methods=["POST"],
        response_model=ChatResponse,
        responses=CHAT_RESPONSES,
        summary=_spec.summary,
        name=f"task_{_spec.name}",
    )
