"""Credential detection in replies and inputs. Findings report kinds and counts, never values."""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.utils.redaction import registered_secrets

WITHHELD = "[withheld]"

# Formats of credentials that are common enough to check for even when they were never registered.
PATTERNS: dict[str, re.Pattern[str]] = {
    "model_provider_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    "openai_key": re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class SecretFinding:
    kind: str
    count: int


def find_secret_kinds(text: str) -> tuple[str, ...]:
    """The kinds of credential present in `text`. Used on inputs, which are stored but not logged."""
    kinds = {kind for kind, pattern in PATTERNS.items() if pattern.search(text)}
    if any(secret and secret in text for secret in registered_secrets()):
        kinds.add("registered_credential")
    return tuple(sorted(kinds))


def redact_secrets(text: str) -> tuple[str, tuple[SecretFinding, ...]]:
    """Replace every credential in `text` with WITHHELD. Returns the new text and what was found."""
    findings: list[SecretFinding] = []
    for secret in registered_secrets():
        if secret:
            count = text.count(secret)
            if count:
                text = text.replace(secret, WITHHELD)
                findings.append(SecretFinding("registered_credential", count))
    for kind, pattern in PATTERNS.items():
        text, count = pattern.subn(WITHHELD, text)
        if count:
            findings.append(SecretFinding(kind, count))
    return text, tuple(findings)
