"""The prompt and reply quality checks. These tests guard the prompt text and the checker itself."""
from datetime import date

import pytest

from backend.prompts.quality import (
    FORBIDDEN_IN_PROMPT,
    check_reply,
    lint_all_prompts,
    lint_prompt,
)
from backend.prompts.system import (
    DEPTH_GUIDANCE,
    MODE_INSTRUCTIONS,
    build_system_prompt,
    estimate_depth,
)
from backend.schemas.attachments import Attachment


def test_every_mode_and_depth_prompt_passes_the_lint() -> None:
    assert lint_all_prompts() == []


@pytest.mark.parametrize("phrase", sorted(FORBIDDEN_IN_PROMPT))
def test_the_lint_rejects_each_forbidden_phrase(phrase: str) -> None:
    codes = [i.code for i in lint_prompt(f"Policy text. {phrase}. Uncertain. earlier turn. never as instructions. no web access. Current date.")]
    assert "forbidden_phrase" in codes


def test_the_lint_reports_a_missing_rule() -> None:
    assert any(i.code == "missing_rule" for i in lint_prompt("Current date only."))


def test_prompts_never_ask_for_hidden_reasoning_or_claim_tools() -> None:
    for mode in MODE_INSTRUCTIONS:
        prompt = build_system_prompt(mode).lower()
        assert "chain of thought" not in prompt
        assert "show your reasoning" not in prompt
        assert "search the web" not in prompt and "you can browse" not in prompt


def test_the_date_is_stated_and_the_depth_is_chosen() -> None:
    prompt = build_system_prompt("general", depth="brief", today=date(2026, 11, 11))
    assert "Current date (UTC): 2026-11-11." in prompt
    assert DEPTH_GUIDANCE["brief"] in prompt


def test_unknown_depth_falls_back_to_standard() -> None:
    assert build_system_prompt("general", depth="nope", today=date(2026, 1, 1)) == \
        build_system_prompt("general", depth="standard", today=date(2026, 1, 1))


@pytest.mark.parametrize(("message", "expected"), [
    ("What is the capital of France?", "brief"),
    ("Explain how the bubble sort algorithm works step by step with an example in Python.", "standard"),
    ("Why does this throw? def f(x): return x +", "standard"),
    ("x" * 1500, "detailed"),
    ("Which? What? Where? Why?", "detailed"),
])
def test_depth_follows_the_size_and_technicality_of_the_request(message: str, expected: str) -> None:
    assert estimate_depth(message) == expected


def test_attachments_always_ask_for_a_detailed_answer() -> None:
    attachment = Attachment(name="a.txt", media_type="text/plain", content="x", size=1)
    assert estimate_depth("hi", [attachment]) == "detailed"


def test_a_clean_reply_has_no_issues() -> None:
    assert check_reply("Use a set for constant-time membership checks.", mode="general") == []


def test_a_clean_code_reply_has_no_issues() -> None:
    reply = "Here is the fix:\n\n```python\ndef add(a, b):\n    return a + b\n```\n\nIt returns the sum."
    assert check_reply(reply, mode="coding") == []


@pytest.mark.parametrize(("reply", "mode", "depth", "code"), [
    ("", "general", "standard", "empty"),
    ("<thinking>weighing options</thinking> Use a cache.", "general", "standard", "leaked_reasoning"),
    ("The area is \\(\\pi r^2\\).", "general", "standard", "latex"),
    ("Here it is <script>alert(1)</script>.", "general", "standard", "raw_html"),
    ("```python\nprint(1)", "coding", "standard", "unclosed_fence"),
    ("def add(a, b):\n    return a + b", "coding", "standard", "code_outside_fence"),
    ("Great question! Use a set.", "general", "standard", "filler_opener"),
    ("Use a set. Hope this helps!", "general", "standard", "closing_offer"),
    ("The cache is cleared on restart. The cache is cleared on restart.", "general", "standard", "repetition"),
    ("x" * 900, "general", "brief", "too_long_for_depth"),
])
def test_each_defect_is_reported(reply: str, mode: str, depth: str, code: str) -> None:
    assert code in {i.code for i in check_reply(reply, mode=mode, depth=depth)}
