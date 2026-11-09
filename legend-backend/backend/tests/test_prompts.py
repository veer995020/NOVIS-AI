from backend.prompts.system import build_messages, build_system_prompt
from backend.schemas.response import Message


def test_system_prompt_is_first_and_history_is_trimmed() -> None:
    history = [Message(role="user", content=str(i)) for i in range(5)]
    messages = build_messages(mode="general", history=history, max_context=2)
    assert messages[0]["role"] == "system"
    assert [m["content"] for m in messages[1:]] == ["3", "4"]


def test_unknown_mode_falls_back_to_standard() -> None:
    assert build_system_prompt("nope") == build_system_prompt("general")
