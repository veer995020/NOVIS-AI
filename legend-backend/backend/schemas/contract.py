"""Frontend/backend contract, generated from the Pydantic models.

`contracts/chat.contract.json` is the file the frontend reads. A test fails if
it drifts from the code. Regenerate after any schema change:

    python -m backend.schemas.contract
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from backend.config import APP_VERSION, MAX_MESSAGE_CHARS, MAX_REPLY_CHARS
from backend.schemas.chat import (
    ChatOptions,
    ChatRequest,
    ChatResponse,
    Message,
    StopResponse,
    TaskRequest,
)
from backend.schemas.conversations import (
    Conversation,
    ConversationClear,
    ConversationCreate,
    ConversationUpdate,
)
from backend.schemas.errors import ErrorEnvelope
from backend.schemas.events import StreamEvent
from backend.schemas.health import HealthReport, ProbeReport
from backend.schemas.models import ModelList
from backend.services.tasks import TASK_SPECS

CONTRACT_VERSION = 1
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "chat.contract.json"

# Status codes shared by every chat-style JSON endpoint.
CHAT_STATUSES = {
    "200": "status is completed or stopped",
    "422": "the request failed validation",
    "502": "status is error with code generation_failed",
    "409": "ErrorEnvelope with code conversation_busy when the request cannot start, or ChatResponse with status error and code conversation_busy when the reply could not be saved",
    "503": "status is error with code model_unavailable",
    "504": "status is error with code generation_timeout",
}


def _build_endpoints() -> dict[str, Any]:
    endpoints: dict[str, Any] = {
        "POST /api/chat": {
            "request": "ChatRequest",
            "response": "ChatResponse",
            "error": "ErrorEnvelope",
            "statuses": CHAT_STATUSES,
        },
        "POST /api/chat/stream": {"request": "ChatRequest", "events": "StreamEvent"},
        "POST /api/conversations": {"request": "ConversationCreate", "response": "Conversation", "status": "201"},
        "GET /api/conversations/{conversation_id}": {"response": "Conversation", "errors": ["404 conversation_not_found"]},
        "PATCH /api/conversations/{conversation_id}": {"request": "ConversationUpdate", "response": "Conversation", "errors": ["404 conversation_not_found", "409 version_conflict"]},
        "POST /api/conversations/{conversation_id}/clear": {"request": "ConversationClear", "response": "Conversation", "errors": ["404 conversation_not_found", "409 version_conflict", "409 conversation_busy"]},
        "GET /api/chat/options": {"response": "ChatOptions"},
        "GET /api/models": {"response": "ModelList"},
        "GET /health": {"response": "HealthReport", "statuses": {"200": "ok or degraded", "503": "down"}},
        "GET /health/live": {"response": "ProbeReport"},
        "GET /health/ready": {"response": "ProbeReport", "statuses": {"200": "ready", "503": "not_ready"}},
        "POST /api/chat/{request_id}/stop": {"response": "StopResponse"},
    }
    for spec in TASK_SPECS:
        endpoints[f"POST {spec.path}"] = {
            "request": "TaskRequest",
            "response": "ChatResponse",
            "error": "ErrorEnvelope",
            "mode": spec.mode,
            "statuses": CHAT_STATUSES,
        }
    return endpoints


def build_contract() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "api_version": APP_VERSION,
        "limits": {
            "message_max_chars": MAX_MESSAGE_CHARS,
            "reply_max_chars": MAX_REPLY_CHARS,
            "conversation_id_max_length": 64,
            "name_max_length": 64,
        },
        "endpoints": _build_endpoints(),
        "schemas": {
            "ChatRequest": ChatRequest.model_json_schema(),
            "TaskRequest": TaskRequest.model_json_schema(),
            "ChatResponse": ChatResponse.model_json_schema(),
            "ChatOptions": ChatOptions.model_json_schema(),
            "ModelList": ModelList.model_json_schema(),
            "HealthReport": HealthReport.model_json_schema(),
            "ProbeReport": ProbeReport.model_json_schema(),
            "Message": Message.model_json_schema(),
            "Conversation": Conversation.model_json_schema(),
            "ConversationCreate": ConversationCreate.model_json_schema(),
            "ConversationUpdate": ConversationUpdate.model_json_schema(),
            "ConversationClear": ConversationClear.model_json_schema(),
            "StopResponse": StopResponse.model_json_schema(),
            "ErrorEnvelope": ErrorEnvelope.model_json_schema(),
            "StreamEvent": TypeAdapter(StreamEvent).json_schema(),
        },
    }


def write_contract(path: Path = CONTRACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(build_contract(), indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    write_contract()
    print(f"wrote {CONTRACT_PATH}")
