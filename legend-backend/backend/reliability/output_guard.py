"""The reply gate. Runs on every reply before it is saved or returned.

Order: credentials are withheld, fragments of the system policy are withheld, the existing reply checks
run (prompts/quality.py), claims are classified, and contradictions are checked. A reply that
contains unverified specific details gets a visible note. It is never described as verified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from backend.prompts.quality import HARD_CODES, check_reply
from backend.reliability.claims import classify_reply
from backend.reliability.contradictions import contradicts_conversation
from backend.reliability.input_guard import InputReport
from backend.reliability.secret_guard import WITHHELD, redact_secrets

UNVERIFIED_NOTE = (
    "Note: the specific figures, dates, or links above come from general knowledge. "
    "I have not checked them against a source."
)
_POLICY_WINDOW = 40  # any run of this many policy characters in a reply is withheld


@dataclass(frozen=True, slots=True)
class ReplyAnalysis:
    content: str
    traceable_claims: int
    unverified_claims: int
    injection_suspected: bool
    content_redacted: bool
    flags: tuple[str, ...] = field(default=())


@lru_cache(maxsize=16)
def _policy_windows(policy: str) -> frozenset[str]:
    """Every run of _POLICY_WINDOW characters of the policy, one character apart, in the compact,
    lowered form. The policy is fixed per mode and depth, so this is computed once per policy."""
    compact = " ".join(policy.split()).lower()
    return frozenset(compact[i : i + _POLICY_WINDOW] for i in range(len(compact) - _POLICY_WINDOW + 1))


@lru_cache(maxsize=8192)
def _fragment_pattern(fragment: str) -> re.Pattern[str]:
    """Whitespace-tolerant pattern for one policy fragment. Cached: the fragments come from fixed policies."""
    return re.compile(r"\s+".join(re.escape(word) for word in fragment.split()), re.IGNORECASE)


def withhold_policy_fragments(reply: str, policy: str) -> tuple[str, bool]:
    """Replace any long fragment of the policy that the reply repeats. Returns (text, found)."""
    windows = _policy_windows(policy)
    if not windows:
        return reply, False
    # One pass over the reply: a 40-character run of the reply matches a fragment exactly when it is one of
    # the policy's windows. Whitespace in the reply may differ from the policy's, so matches are then replaced
    # by a whitespace-tolerant pattern, in the same order the windows were always replaced in.
    lowered = reply.lower()
    size = _POLICY_WINDOW
    matched = {lowered[i : i + size] for i in range(len(lowered) - size + 1)} & windows
    if not matched:
        return reply, False
    for fragment in sorted(matched):
        if fragment in lowered:
            reply, _ = _fragment_pattern(fragment).subn(WITHHELD, reply)
            lowered = reply.lower()
    return reply, True


def analyse_reply(
    reply: str,
    *,
    context: str,
    policy: str,
    mode: str,
    depth: str,
    input_report: InputReport,
) -> ReplyAnalysis:
    """Analyse one reply. `context` is the text of the user's messages and attachments."""
    text, findings = redact_secrets(reply)
    flags: list[str] = []
    if findings:
        flags.append("credential_withheld")

    text, fragment_found = withhold_policy_fragments(text, policy)
    if fragment_found:
        flags.append("policy_fragment_withheld")

    for issue in check_reply(text, mode=mode, depth=depth):
        if issue.code in HARD_CODES:
            flags.append(f"defect:{issue.code}")

    claims = classify_reply(text, context)
    if contradicts_conversation(context, text):
        flags.append("contradicts_conversation")
    if claims.unverified:
        text = f"{text.rstrip()}\n\n{UNVERIFIED_NOTE}"
        flags.append("unverified_details")

    if input_report.injection_suspected:
        flags.append("input_injection_suspected")
    if input_report.credential_kinds:
        flags.append("input_contains_credential")

    return ReplyAnalysis(
        content=text,
        traceable_claims=claims.traceable,
        unverified_claims=claims.unverified,
        injection_suspected=input_report.injection_suspected,
        content_redacted=bool(findings) or fragment_found,
        flags=tuple(flags),
    )
