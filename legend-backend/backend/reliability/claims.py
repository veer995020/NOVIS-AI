"""Separates what a reply says into claims traceable to the conversation, inference, and unverified
details. A sentence is "traceable" only when every specific detail in it (a link, a year, a percentage,
a currency amount, or an attribution) also appears in the user's own messages or attachments. The
server never checks anything externally, so no sentence is ever labelled "verified".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

URL: Final = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
YEAR: Final = re.compile(r"\b(?:19|20)\d{2}\b")
PERCENT: Final = re.compile(r"\b\d+(?:\.\d+)?\s?%")
CURRENCY: Final = re.compile(r"[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|INR)\b")
ATTRIBUTION: Final = re.compile(r"\baccording to\b|\bstudies show\b|\bresearch (?:shows|found)\b", re.IGNORECASE)
HEDGE: Final = re.compile(
    r"\b(?:may|might|possibly|probably|likely|perhaps|uncertain|unverified|approximately|roughly|"
    r"estimated?|as far as i know|i(?:'m| am) not sure|i don'?t know|i can'?t verify|i cannot verify|"
    r"may have changed)\b",
    re.IGNORECASE,
)
SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True, slots=True)
class ClaimReport:
    traceable: int
    inference: int
    unverified: int
    unverified_sentences: tuple[str, ...]


def specific_details(sentence: str) -> list[str]:
    """Details that must be traceable before a sentence can be called traceable."""
    details: list[str] = []
    for pattern in (URL, YEAR, PERCENT, CURRENCY, ATTRIBUTION):
        details.extend(match.group(0) for match in pattern.finditer(sentence))
    return details


def classify_sentence(sentence: str, context_lower: str) -> str:
    """'traceable', 'inference', or 'unverified' for one sentence. 'skip' for empty text."""
    text = sentence.strip()
    if not text:
        return "skip"
    if HEDGE.search(text):
        return "inference"
    details = specific_details(text)
    if not details:
        return "inference"
    return "traceable" if all(detail.lower() in context_lower for detail in details) else "unverified"


def classify_reply(reply: str, context: str) -> ClaimReport:
    context_lower = context.lower()
    counts = {"traceable": 0, "inference": 0, "unverified": 0}
    unverified: list[str] = []
    for sentence in SENTENCE_BREAK.split(reply):
        label = classify_sentence(sentence, context_lower)
        if label == "skip":
            continue
        counts[label] += 1
        if label == "unverified":
            unverified.append(sentence.strip())
    return ClaimReport(counts["traceable"], counts["inference"], counts["unverified"], tuple(unverified))
