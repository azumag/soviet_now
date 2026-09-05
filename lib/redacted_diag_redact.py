#!/usr/bin/env python3
"""docich#33: redaction helpers for the read-only diagnostic pipeline.

Used by both the ingestion path (to compute ``redacted_context_hash`` without
ever persisting the raw viewer comment in the long-lived event store) and the
allowlisted collector (to redact runtime evidence before it becomes part of a
snapshot that a diagnostic agent may read).

Stdlib only: this module must import cleanly under the minimal ``env -i``
interpreter used inside the diagnostic sandbox (see ``diagnostics_runner.sh``).
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

# Order matters: more specific patterns first so a token is not partially
# redacted by a broader rule before the specific one can match it.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AUTH_HEADER", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.=]{8,}")),
    ("BASIC_AUTH", re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("OPENAI_STYLE_KEY", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "ENV_ASSIGNMENT_SECRET",
        re.compile(
            r"(?im)^([A-Za-z_][A-Za-z0-9_]*(?:API|SECRET|TOKEN|PASSWORD|PASSWD|KEY|CREDENTIAL)[A-Za-z0-9_]*\s*[=:]\s*)(\S+)"
        ),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("JP_PHONE", re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b")),
    ("HANDLE", re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{3,25}\b")),
    # Generic long opaque token (hex/base64-like, >=24 chars, no whitespace).
    # Catches sentinel-style secrets that don't match a known vendor format.
    ("GENERIC_TOKEN", re.compile(r"\b(?=[A-Za-z0-9_\-]{24,}\b)(?:[A-Za-z0-9_\-]*[0-9][A-Za-z0-9_\-]*[A-Za-z][A-Za-z0-9_\-]*|[A-Za-z0-9_\-]*[A-Za-z][A-Za-z0-9_\-]*[0-9][A-Za-z0-9_\-]*)\b")),
]


def _sub_env_assignment(match: "re.Match[str]") -> str:
    return f"{match.group(1)}[REDACTED_{_current_label[0]}]"


# Small mutable cell so _sub_env_assignment (needed because this pattern keeps
# the key/operator via a capture group) can see which label is active without
# changing the public redact_text() signature.
_current_label = ["SECRET"]


def redact_text(text: str) -> str:
    """Return ``text`` with tokens/secrets/PII sentinels replaced.

    Deterministic and side-effect free: same input always yields the same
    output, which is required for ``redacted_context_hash`` to be stable.
    """
    if not text:
        return ""
    out = text
    for label, pattern in _PATTERNS:
        if label == "ENV_ASSIGNMENT_SECRET":
            _current_label[0] = "SECRET"
            out = pattern.sub(_sub_env_assignment, out)
        else:
            out = pattern.sub(f"[REDACTED_{label}]", out)
    return out


def redact_lines(lines: Iterable[str]) -> list[str]:
    return [redact_text(line) for line in lines]


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def redacted_context_hash(category: str, raw_text: str) -> str:
    """Hash of the *redacted* text, namespaced by category.

    Callers must never pass the raw text anywhere except into this function
    and the short-TTL restricted spool (see redacted_diag_ingest.py) - the
    long-lived event record only ever stores the return value.
    """
    redacted = redact_text(raw_text or "")
    return sha256_hex(f"{category}\x00{redacted}")


def contains_unredacted_sentinel(text: str, sentinels: Iterable[str]) -> list[str]:
    """Test helper: return which of ``sentinels`` still appear verbatim in text."""
    hits = []
    for s in sentinels:
        if s and s in text:
            hits.append(s)
    return hits


if __name__ == "__main__":
    import sys

    sys.stdout.write(redact_text(sys.stdin.read()))
