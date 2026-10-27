from __future__ import annotations

from collections.abc import Sequence

from backend.schemas.chat import Message

SYSTEM_PROMPT = (
    "You are Novis AI, a helpful and honest assistant built for Legend Inc. "
    "Answer clearly and say when you are unsure."
)

# The instructions sent to the model for each mode. Editing this text changes model behaviour.
MODE_INSTRUCTIONS: dict[str, str] = {
    "standard": "Give a direct, well-organized answer.",
    "research": (
        "Organize findings clearly. For comparisons, set the options side by side "
        "against the same criteria. Separate established facts from uncertainty, say what "
        "should be verified, and do not invent sources."
    ),
    "code": (
        "Generate, explain, or debug code. Put code in fenced blocks and name the language. "
        "When debugging, state the cause before the fix. List your assumptions and say what "
        "you could not verify."
    ),
    "write": (
        "Draft, rewrite, or refine the text. Keep the meaning and the user's intent. "
        "Give the revised text first, then a brief note on what changed if that helps."
    ),
    "creative": "Favor original, vivid writing while following the user's constraints.",
}


def build_system_prompt(mode: str) -> str:
    # Unknown modes fall back to "standard" so raw input never reaches the prompt.
    if mode not in MODE_INSTRUCTIONS:
        mode = "standard"
    return f"{SYSTEM_PROMPT}\n\nMode: {mode}. {MODE_INSTRUCTIONS[mode]}"


def build_messages(
    *, mode: str, history: Sequence[Message], max_context: int
) -> list[dict[str, str]]:
    """System prompt followed by the most recent `max_context` messages."""
    recent = list(history)[-max_context:]
    messages = [{"role": "system", "content": build_system_prompt(mode)}]
    messages.extend({"role": m.role, "content": m.content} for m in recent)
    return messages
