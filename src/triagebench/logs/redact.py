"""Remove personal data and token-shaped strings from logs before anything is stored.

GitHub already masks registered secrets as ***, but logs can still print emails or tokens that
were never registered as secrets. We replace them and count how many we replaced.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("bearer", re.compile(r"(?i)\b(authorization:\s*bearer\s+)[A-Za-z0-9._~+/=-]{16,}")),
]

# Noreply addresses and bot identities are public plumbing, not personal data.
_KEEP_EMAIL = re.compile(r"(?i)(noreply|no-reply|users\.noreply\.github\.com|example\.(com|org))")


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts_by_kind)."""
    counts: dict[str, int] = {}
    for kind, pattern in _PATTERNS:

        def _sub(m: re.Match[str], kind: str = kind) -> str:
            if kind == "email" and _KEEP_EMAIL.search(m.group(0)):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "bearer":
                return m.group(1) + "[REDACTED]"
            return f"[REDACTED_{kind.upper()}]"

        text = pattern.sub(_sub, text)
    return text, counts
