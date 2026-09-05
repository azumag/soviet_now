#!/usr/bin/env python3
"""docich#33: read-only broker between a diagnostic agent and the snapshot store.

This is the *only* interface a diagnostic agent/runner is meant to use to
obtain evidence. It never touches the live repository, VM, process table, or
any credential -- it only serves copies of snapshots that
redacted_diag_collector.py already wrote (and already redacted).

Structural read-only guarantee: this CLI has exactly two verbs, ``list`` and
``get``. There is no ``set``/``put``/``delete`` verb, so there is nothing to
audit-away later -- a write capability was simply never implemented here.

Both verbs validate ``--event-id`` / ``--evidence-ref`` against a strict
token pattern (no path separators, no ``..``) and re-check that the resolved
path stays under ``--snapshot-dir`` before touching the filesystem, so a
request that tries to walk out of the snapshot store is rejected before any
I/O happens.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class BrokerError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _validate_token(value: str, label: str) -> str:
    if not value or not _TOKEN_RE.match(value) or ".." in value:
        raise BrokerError(f"rejected: invalid {label}: {value!r}", code=3)
    return value


def _resolve_snapshot_path(snapshot_dir: Path, event_id: str, evidence_ref: str) -> Path:
    _validate_token(event_id, "event_id")
    _validate_token(evidence_ref, "evidence_ref")
    root = snapshot_dir.resolve()
    candidate = (root / event_id / f"{evidence_ref}.json").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BrokerError(f"rejected: evidence_ref escapes snapshot store: {evidence_ref!r}", code=3) from exc
    return candidate


def list_evidence(snapshot_dir: Path, event_id: str) -> list[dict[str, Any]]:
    _validate_token(event_id, "event_id")
    event_dir = (snapshot_dir.resolve() / event_id)
    try:
        event_dir.relative_to(snapshot_dir.resolve())
    except ValueError as exc:
        raise BrokerError(f"rejected: invalid event_id: {event_id!r}", code=3) from exc
    if not event_dir.is_dir():
        return []
    out = []
    for path in sorted(event_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append(
            {
                "evidence_ref": data.get("evidence_ref", path.stem),
                "kind": data.get("kind"),
                "status": data.get("status"),
                "captured_at": data.get("captured_at"),
                "content_sha256": data.get("content_sha256"),
            }
        )
    return out


def get_evidence(snapshot_dir: Path, event_id: str, evidence_ref: str, dest_dir: Path) -> Path:
    src = _resolve_snapshot_path(snapshot_dir, event_id, evidence_ref)
    if not src.is_file():
        raise BrokerError(f"rejected: no such evidence: {event_id}/{evidence_ref}", code=4)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{evidence_ref}.json"
    # Read-then-write a fresh copy: never touches/moves the source snapshot.
    shutil.copyfile(src, dest)
    os.chmod(dest, 0o400)
    return dest


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="verb", required=True)

    p_list = sub.add_parser("list", help="list evidence_refs available for an event (metadata only)")
    p_list.add_argument("--snapshot-dir", required=True)
    p_list.add_argument("--event-id", required=True)

    p_get = sub.add_parser("get", help="copy one redacted snapshot into a read-only staging dir")
    p_get.add_argument("--snapshot-dir", required=True)
    p_get.add_argument("--event-id", required=True)
    p_get.add_argument("--evidence-ref", required=True)
    p_get.add_argument("--dest", required=True)

    args = p.parse_args(argv)
    try:
        if args.verb == "list":
            result = list_evidence(Path(args.snapshot_dir), args.event_id)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.verb == "get":
            dest = get_evidence(Path(args.snapshot_dir), args.event_id, args.evidence_ref, Path(args.dest))
            print(json.dumps({"path": str(dest)}, sort_keys=True))
            return 0
    except BrokerError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
