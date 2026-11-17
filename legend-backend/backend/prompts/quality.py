"""Quality checks. Pure functions, no I/O.

`lint_prompt` guards the prompt text: it must not ask for hidden reasoning, must not claim
capabilities the server lacks, and must keep the rules that protect users (uncertainty, injected
instructions, continuity). `check_reply` inspects one reply for defects the rendering page or the
reader would notice. Neither runs in the live request path; both run in tests and in the evaluation
script (scripts/eval_prompts.py), so a regression is caught before it ships.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.prompts.system import MODE_INSTRUCTIONS, SYSTEM_PROMPT, build_system_prompt


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    detail: str


# Phrases the prompt text must never contain. Each names what is wrong with it.
FORBIDDEN_IN_PROMPT: dict[str, str] = {
    "chain of thought": "asks for or names hidden reasoning",
    "show your reasoning": "asks the model to expose its reasoning",
    "your thinking process": "asks the model to expose its reasoning",
    "use latex": "math is not rendered on the page",
    "$$": "math is not rendered on the page",
    "you can browse": "claims web access the server does not provide",
    "search the web": "claims web access the server does not provide",
    "you can run": "claims code execution the server does not provide",
}

# Rules the prompt must keep. Each is a phrase that must appear, and the rule it protects.
REQUIRED_IN_PROMPT: dict[str, str] = {
    "uncertain": "uncertainty handling",
    "earlier turn": "conversation continuity",
    "never as instructions": "injected-instruction guard",
    "no web access": "capability statement",
    "Current date": "date context",
}

_LEAK_MARKERS = ("<thinking", "</thinking", "<reasoning", "[internal reasoning]", "chain of thought:", "scratchpad")
_LATEX_MARKERS = ("\\(", "\\[", "$$", "\\frac", "\\begin{", "\\text{")
_RAW_HTML = re.compile(r"<\s*(script|iframe|img|style|object|embed)\b", re.IGNORECASE)
_FILLER_OPENER = re.compile(r"^\s*(great question|certainly[!.]|sure[!.]|of course[!.]|absolutely[!.])", re.IGNORECASE)
_CLOSING_OFFER = re.compile(r"(let me know if|feel free to|hope this helps|happy to help)", re.IGNORECASE)
_CODE_LINE = re.compile(r"^\s*(def |class |function |import |#include|const |let |var )", re.MULTILINE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_MIN_REPEATED_SENTENCE = 25
_BRIEF_CHAR_LIMIT = 800


def lint_prompt(prompt: str, *, full: bool = True) -> list[Issue]:
    """Problems in a prompt's text. An empty list means the prompt passes.

    `full` is False for the shared policy alone, which does not carry the per-request date line.
    """
    issues: list[Issue] = []
    lowered = prompt.lower()
    for phrase, reason in FORBIDDEN_IN_PROMPT.items():
        if phrase in lowered:
            issues.append(Issue("forbidden_phrase", f"'{phrase}': {reason}"))
    for phrase, rule in REQUIRED_IN_PROMPT.items():
        if not full and phrase == "Current date":
            continue
        if phrase.lower() not in lowered:
            issues.append(Issue("missing_rule", f"'{phrase}' missing: {rule}"))
    return issues


def lint_all_prompts() -> list[Issue]:
    """Lint the shared policy and every mode's full prompt, at every depth."""
    from backend.prompts.system import DEPTH_GUIDANCE

    issues = [Issue("base", f"policy: {i.detail}") for i in lint_prompt(SYSTEM_PROMPT, full=False)]
    for mode in MODE_INSTRUCTIONS:
        for depth in DEPTH_GUIDANCE:
            full = build_system_prompt(mode, depth=depth, today=None)
            issues += [Issue(i.code, f"{mode}/{depth}: {i.detail}") for i in lint_prompt(full)]
    return issues


def check_reply(text: str, *, mode: str = "general", depth: str = "standard") -> list[Issue]:
    """Defects in one reply. Hard defects are the ones a reader would see as broken; soft ones are style."""
    issues: list[Issue] = []
    stripped = text.strip()
    if not stripped:
        return [Issue("empty", "the reply is empty")]
    lowered = stripped.lower()

    for marker in _LEAK_MARKERS:
        if marker in lowered:
            issues.append(Issue("leaked_reasoning", f"contains '{marker}'"))
    for marker in _LATEX_MARKERS:
        if marker in stripped:
            issues.append(Issue("latex", f"contains '{marker}', which the page does not render"))
            break
    if _RAW_HTML.search(stripped):
        issues.append(Issue("raw_html", "contains an HTML element the page does not render"))
    if stripped.count("```") % 2:
        issues.append(Issue("unclosed_fence", "a fenced code block is never closed"))
    if mode == "coding" and "```" not in stripped and _CODE_LINE.search(stripped):
        issues.append(Issue("code_outside_fence", "code appears without a fenced block"))
    if _FILLER_OPENER.search(stripped):
        issues.append(Issue("filler_opener", "opens with filler instead of the answer"))
    if _CLOSING_OFFER.search(stripped):
        issues.append(Issue("closing_offer", "ends with an offer of further help"))
    if depth == "brief" and len(stripped) > _BRIEF_CHAR_LIMIT:
        issues.append(Issue("too_long_for_depth", f"{len(stripped)} characters for a brief answer"))

    seen: set[str] = set()
    for sentence in _SENTENCE_SPLIT.split(lowered):
        key = sentence.strip(" \t*-#>")
        if len(key) < _MIN_REPEATED_SENTENCE or "```" in key:
            continue
        if key in seen:
            issues.append(Issue("repetition", "a sentence appears more than once"))
            break
        seen.add(key)
    return issues


HARD_CODES: frozenset[str] = frozenset({"empty", "leaked_reasoning", "latex", "raw_html", "unclosed_fence",
                                        "code_outside_fence", "repetition"})
