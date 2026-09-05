#!/usr/bin/env python3
"""docich#33: reference read-only diagnostic script.

Runs INSIDE the diagnostic sandbox (see diagnostics_runner.sh). Contract:

- Reads redacted evidence snapshots from ``$DIAG_INPUT_DIR`` (one JSON file
  per evidence_ref, as copied out by redacted_diag_broker.py).
- Prints exactly one JSON array to stdout: a list of
  ``{finding, evidence_ref, confidence, recommended_action}`` objects (see
  redacted_diag_report.py for the schema the runner enforces on this
  output). Nothing else goes to stdout.
- Never performs a network call, never sends a signal, never writes outside
  ``$DIAG_OUTPUT_DIR`` (this script does not even need to write a file --
  the runner captures stdout), and never generates a code change.

This is intentionally a thin, deterministic placeholder: the actual bug
triage heuristics are out of scope for docich#33, which is about the safe
pipeline the heuristics would run inside, not the heuristics themselves.

Stdlib only -- this must import cleanly under the sandboxed ``env -i``
interpreter.
"""

from __future__ import annotations

import json
import os
import sys

_ANOMALY_MARKERS = (
    "[REDACTED_",  # a secret/PII was present and had to be redacted
    "error",
    "ERROR",
    "traceback",
    "Traceback",
)


def _looks_anomalous(content) -> bool:
    text = json.dumps(content, ensure_ascii=False)
    return any(marker in text for marker in _ANOMALY_MARKERS)


def main() -> int:
    input_dir = os.environ.get("DIAG_INPUT_DIR")
    if not input_dir or not os.path.isdir(input_dir):
        print(json.dumps([]))
        return 0

    findings = []
    for name in sorted(os.listdir(input_dir)):
        if not name.endswith(".json"):
            continue
        evidence_ref = name[: -len(".json")]
        path = os.path.join(input_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
        except Exception:
            findings.append(
                {
                    "finding": "evidence snapshot could not be parsed",
                    "evidence_ref": evidence_ref,
                    "confidence": "low",
                    "recommended_action": "re-collect this evidence source and retry",
                }
            )
            continue

        status = snapshot.get("status")
        content = snapshot.get("content")
        if status == "unavailable":
            findings.append(
                {
                    "finding": "evidence source was unavailable at collection time",
                    "evidence_ref": evidence_ref,
                    "confidence": "low",
                    "recommended_action": "confirm the underlying service/log path exists on the host",
                }
            )
        elif _looks_anomalous(content):
            findings.append(
                {
                    "finding": "evidence contains an error/anomaly marker",
                    "evidence_ref": evidence_ref,
                    "confidence": "medium",
                    "recommended_action": "have an operator review the corresponding evidence snapshot",
                }
            )
        else:
            findings.append(
                {
                    "finding": "no anomaly detected in this evidence source",
                    "evidence_ref": evidence_ref,
                    "confidence": "low",
                    "recommended_action": "no action needed from this evidence alone",
                }
            )

    if not findings:
        findings.append(
            {
                "finding": "no evidence was provided to this run",
                "evidence_ref": "none",
                "confidence": "low",
                "recommended_action": "operator should attach at least one evidence_ref and retry",
            }
        )
    print(json.dumps(findings, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
