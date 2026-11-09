from __future__ import annotations

from collections.abc import Sequence

from backend.schemas.attachments import Attachment
from backend.schemas.response import Message

SYSTEM_PROMPT = (
    "You are Chatify AI, a helpful and honest assistant built for Legend Inc. "
    "Answer clearly and say when you are unsure."
)

# The instructions sent to the model for each mode. Editing this text changes model behaviour.
MODE_INSTRUCTIONS: dict[str, str] = {
    "general": "Give a direct, well-organized answer.",
    "research": (
        "Organize findings clearly. For comparisons, set the options side by side "
        "against the same criteria. Separate established facts from uncertainty, say what "
        "should be verified, and do not invent sources."
    ),
    "coding": (
        "Generate, explain, or debug code. Put code in fenced blocks and name the language. "
        "When debugging, state the cause before the fix. List your assumptions and say what "
        "you could not verify."
    ),
    "writing": (
        "Draft, rewrite, or refine the text. Keep the meaning and the user's intent. "
        "Give the revised text first, then a brief note on what changed if that helps."
    ),
}


def build_system_prompt(mode: str) -> str:
    # Unknown modes fall back to "general" so raw input never reaches the prompt.
    if mode not in MODE_INSTRUCTIONS:
        mode = "general"
    return f"{SYSTEM_PROMPT}\n\nMode: {mode}. {MODE_INSTRUCTIONS[mode]}"


def build_messages(
    *, mode: str, history: Sequence[Message], max_context: int
) -> list[dict[str, str]]:
    """System prompt followed by the most recent `max_context` messages."""
    recent = list(history)[-max_context:]
    messages = [{"role": "system", "content": build_system_prompt(mode)}]
    messages.extend({"role": m.role, "content": m.content} for m in recent)
    return messages


def render_turn(message: str, attachments: Sequence[Attachment]) -> str:
    """The text the model receives for one user turn: the message, then each attachment."""
    parts = [message]
    for index, attachment in enumerate(attachments, start=1):
        size = len(attachment.content.encode("utf-8"))
        parts.append(
            f"\n\n[Attachment {index}: {attachment.name} ({attachment.media_type}, {size} bytes)]\n"
            f"{attachment.content}\n[End of attachment {index}]"
        )
    return "".join(parts)

