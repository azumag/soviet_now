#!/usr/bin/env python3
"""docich#33: output schema validation + report construction for the runner.

The diagnostic runner treats the sandboxed script's stdout as untrusted: it
must be a JSON array of finding objects with EXACTLY the four allowed keys,
and every ``evidence_ref`` must point at evidence the runner itself fetched
for this run (never something the script invented). Anything else is a
schema violation and is reported as a failure -- the runner never forwards
free-form text or generates a code change.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ALLOWED_KEYS = {"finding", "evidence_ref", "confidence", "recommended_action"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}


class SchemaError(ValueError):
    pass


def validate_findings(raw: Any, *, allowed_evidence_refs: set[str]) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise SchemaError("top-level output must be a JSON array of findings")
    if not raw:
        raise SchemaError("output array must contain at least one finding")

    normalized = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SchemaError(f"finding[{i}] is not an object")
        keys = set(item.keys())
        if keys != ALLOWED_KEYS:
            extra = keys - ALLOWED_KEYS
            missing = ALLOWED_KEYS - keys
            raise SchemaError(
                f"finding[{i}] keys must be exactly {sorted(ALLOWED_KEYS)}"
                f" (extra={sorted(extra)}, missing={sorted(missing)})"
            )
        for key in ("finding", "evidence_ref", "recommended_action"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise SchemaError(f"finding[{i}].{key} must be a non-empty string")
        if item["confidence"] not in ALLOWED_CONFIDENCE:
            raise SchemaError(f"finding[{i}].confidence must be one of {sorted(ALLOWED_CONFIDENCE)}")
        if item["evidence_ref"] not in allowed_evidence_refs:
            raise SchemaError(
                f"finding[{i}].evidence_ref {item['evidence_ref']!r} was not fetched for this run "
                f"(allowed: {sorted(allowed_evidence_refs)})"
            )
        normalized.append(
            {
                "finding": item["finding"],
                "evidence_ref": item["evidence_ref"],
                "confidence": item["confidence"],
                "recommended_action": item["recommended_action"],
            }
        )
    return normalized


def build_success_report(*, event_id: str, findings: list[dict[str, str]], tmpfs_used: bool, sandbox_backend: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "event_id": event_id,
        "generated_at": int(time.time()),
        "tmpfs_used": tmpfs_used,
        "sandbox_backend": sandbox_backend,
        "findings": findings,
    }


def build_failure_report(*, event_id: str, reason: str, tmpfs_used: bool, sandbox_backend: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "event_id": event_id,
        "generated_at": int(time.time()),
        "tmpfs_used": tmpfs_used,
        "sandbox_backend": sandbox_backend,
        "reason": reason,
        "findings": [],
    }


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _main(argv: list[str] | None = None) -> int:
    """CLI used by diagnostics_runner.sh to turn one run's outcome into the
    final operator-facing report, without the shell layer having to embed
    any schema-validation logic of its own.
    """
    p = argparse.ArgumentParser(description="finalize a diagnostic run's report")
    p.add_argument("--event-id", required=True)
    p.add_argument("--out", required=True, help="path to write the final report JSON")
    p.add_argument("--tmpfs-used", required=True, choices=["true", "false"])
    p.add_argument("--sandbox-backend", required=True)
    p.add_argument("--allowed-refs", default="", help="comma-separated evidence_refs fetched for this run")
    p.add_argument("--stdout-file", default=None, help="path to the sandboxed script's captured stdout")
    p.add_argument("--fail-reason", default=None, help="if set, short-circuit straight to a failure report")
    args = p.parse_args(argv)

    tmpfs_used = args.tmpfs_used == "true"
    allowed_refs = {r for r in args.allowed_refs.split(",") if r}

    if args.fail_reason:
        report = build_failure_report(
            event_id=args.event_id, reason=args.fail_reason, tmpfs_used=tmpfs_used, sandbox_backend=args.sandbox_backend
        )
        _atomic_write_text(Path(args.out), dumps(report))
        print(dumps(report))
        return 5

    reason = None
    findings: list[dict[str, str]] = []
    if not args.stdout_file or not os.path.isfile(args.stdout_file):
        reason = "diagnostic script produced no stdout capture file"
    else:
        raw_text = Path(args.stdout_file).read_text(encoding="utf-8", errors="replace")
        if not raw_text.strip():
            reason = "diagnostic script produced empty stdout"
        else:
            try:
                raw = json.loads(raw_text)
            except Exception as exc:  # noqa: BLE001
                reason = f"stdout was not valid JSON: {exc}"
            else:
                try:
                    findings = validate_findings(raw, allowed_evidence_refs=allowed_refs)
                except SchemaError as exc:
                    reason = str(exc)

    if reason is not None:
        report = build_failure_report(event_id=args.event_id, reason=reason, tmpfs_used=tmpfs_used, sandbox_backend=args.sandbox_backend)
        _atomic_write_text(Path(args.out), dumps(report))
        print(dumps(report))
        return 5

    report = build_success_report(
        event_id=args.event_id, findings=findings, tmpfs_used=tmpfs_used, sandbox_backend=args.sandbox_backend
    )
    _atomic_write_text(Path(args.out), dumps(report))
    print(dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
