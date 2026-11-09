"""Task endpoints are fixed modes on the shared chat flow. Nothing here calls a model."""
from __future__ import annotations

from dataclasses import dataclass

from backend.schemas.request import ChatRequest, TaskRequest


@dataclass(frozen=True)
class TaskSpec:
    name: str
    path: str
    mode: str
    summary: str


RESEARCH = TaskSpec("research", "/api/research", "research", "Organize findings and comparisons.")
CODE = TaskSpec("code", "/api/code", "coding", "Generate, explain, and debug code.")
WRITE = TaskSpec("write", "/api/write", "writing", "Draft, rewrite, and refine text.")

TASK_SPECS: tuple[TaskSpec, ...] = (RESEARCH, CODE, WRITE)


def to_chat_request(payload: TaskRequest, spec: TaskSpec) -> ChatRequest:
    """Turn a task request into the shared chat request, with the task's mode fixed."""
    return ChatRequest(
        conversation_id=payload.conversation_id,
        message=payload.message,
        model=payload.model,
        mode=spec.mode,
        attachments=payload.attachments,
    )
