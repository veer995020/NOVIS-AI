"""Heuristic contradiction check: the reply states a value for a subject that the user's own messages
state differently. It looks for "<subject> is/was/= <number>" in both texts. It finds only simple
statements, so a clean result does not mean the reply is consistent."""
from __future__ import annotations

import re
from typing import Final

SUBJECT_VALUE: Final = re.compile(
    r"\b((?:[A-Za-z][A-Za-z]*\s){0,3}[A-Za-z][A-Za-z]*)\s+(?:is|are|was|were|equals|=)\s+(\$?-?\d[\d,]*(?:\.\d+)?%?)",
    re.IGNORECASE,
)
_ARTICLES: Final = re.compile(r"^(?:the|a|an|my|our|your)\s+", re.IGNORECASE)


def _values_by_subject(text: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for subject, value in SUBJECT_VALUE.findall(text):
        key = _ARTICLES.sub("", subject.strip().lower())
        normal = value.replace(",", "").replace("$", "").rstrip(".")
        found.setdefault(key, set()).add(normal)
    return found


def contradicts_conversation(context: str, reply: str) -> bool:
    """True when the reply gives a subject a value the user's messages do not contain."""
    stated = _values_by_subject(context)
    for subject, values in _values_by_subject(reply).items():
        if subject in stated and not values <= stated[subject]:
            return True
    return False
