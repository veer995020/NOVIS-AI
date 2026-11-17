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

from backend.config import (
    APP_VERSION,
    DEFAULT_MODE,
    DEFAULT_MODEL,
    MAX_MESSAGE_CHARS,
    MAX_REPLY_CHARS,
)
from backend.schemas.attachments import (
    ATTACHMENT_MAX_CHARS,
    ATTACHMENT_MAX_COUNT,
    ATTACHMENT_MEDIA_TYPES,
    ATTACHMENT_NAME_FORBIDDEN_CHARACTERS,
    ATTACHMENT_NAME_MAX_LENGTH,
    ATTACHMENTS_MAX_TOTAL_CHARS,
    Attachment,
    AttachmentMeta,
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
from backend.schemas.request import ChatRequest, TaskRequest
from backend.schemas.response import (
    CHAT_PATHS,
    ChatOptions,
    ChatResponse,
    Message,
    StopResponse,
)
from backend.services.tasks import TASK_SPECS

CONTRACT_VERSION = 8
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "chat.contract.json"

# Status codes shared by every chat-style JSON endpoint.
CHAT_STATUSES = {
    "200": "status is success or stopped; content is the reply",
    "422": "the request failed validation",
    "415": "the request body is not application/json",
    "502": "status is error with code GENERATION_FAILED; content is null",
    "409": "chat endpoints: status is error with code CONVERSATION_BUSY; other endpoints: ErrorEnvelope with code CONVERSATION_BUSY",
    "503": "status is error with code MODEL_UNAVAILABLE; content is null",
    "504": "status is error with code GENERATION_TIMEOUT; content is null",
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
        "GET /api/contract": {"response": "Contract", "public": True},
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
            "attachments_max_count": ATTACHMENT_MAX_COUNT,
            "attachment_max_chars": ATTACHMENT_MAX_CHARS,
            "attachments_max_total_chars": ATTACHMENTS_MAX_TOTAL_CHARS,
            "attachment_name_max_length": ATTACHMENT_NAME_MAX_LENGTH,
            "attachment_media_types": list(ATTACHMENT_MEDIA_TYPES),
            "attachment_name_forbidden_characters": list(ATTACHMENT_NAME_FORBIDDEN_CHARACTERS),
        },
        "defaults": {
            "conversation_id": None,
            "model": DEFAULT_MODEL,
            "mode": DEFAULT_MODE,
            "attachments": [],
        },
        "error_formats": {
            "http": {
                "schema": "ErrorEnvelope",
                "shape": {
                    "error": {"code": "string", "message": "string", "details": "FieldIssue[] | null"},
                    "request_id": "string | null",
                },
                "description": "Failures on every other endpoint: not found, conflicts, capacity, size, "
                "method, rate limit, access, host, internal.",
            },
            "chat_outcome": {
                "schema": "ChatResponse",
                "paths": sorted(CHAT_PATHS),
                "description": "Every outcome of a chat or task endpoint is a ChatResponse: a reply "
                "(status success or stopped, content a string, error null), or a failure (status error, "
                "content null, error an ErrorDetail). This includes refusals and validation failures. "
                "Every field is always present. The request id is in the X-Request-ID header.",
            },
            "field_issue": {
                "schema": "FieldIssue",
                "field_format": "body.<field>, with list indexes and sub-fields, e.g. "
                "body.attachments.0.media_type",
                "message": "Human-readable reason. Never contains the submitted value.",
            },
        },
        "endpoints": _build_endpoints(),
        "schemas": {
            "ChatRequest": ChatRequest.model_json_schema(),
            "TaskRequest": TaskRequest.model_json_schema(),
            "ChatResponse": ChatResponse.model_json_schema(),
            "ChatOptions": ChatOptions.model_json_schema(),
            "Attachment": Attachment.model_json_schema(),
            "AttachmentMeta": AttachmentMeta.model_json_schema(),
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
