#!/usr/bin/env python3
"""docich#33: allowlisted evidence collector -> redacted snapshot.

This is the ONLY component in the diagnostic pipeline allowed to read real
service state / log files. It never runs inside the diagnostic sandbox and
is never reachable by a diagnostic agent directly -- an agent only ever sees
what this script already redacted and wrote out as a snapshot file, fetched
for it later via redacted_diag_broker.py.

Collection is allowlist-only: the caller supplies a short *name*, never a
path. Unknown names are rejected. Even though allowlist paths are trusted
(hard-coded below, not attacker input), every resolved path is still checked
for containment under --root as defense in depth against a future mistaken
allowlist entry.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import redacted_diag_redact as redact  # noqa: E402

MAX_READ_BYTES = 200_000

# name -> collection spec. Every entry reads local, already-on-disk state
# (log tails / small state files / a content hash) -- nothing here shells
# out to a live worker, opens a network socket, or reaches the VM directly.
ALLOWLIST: dict[str, dict[str, Any]] = {
    "chat_worker_log_tail": {"kind": "log_tail", "relpath": "logs/chat_worker.log", "tail_lines": 200},
    "youtube_worker_log_tail": {"kind": "log_tail", "relpath": "logs/youtube_worker.log", "tail_lines": 200},
    "audio_worker_log_tail": {"kind": "log_tail", "relpath": "logs/audio_worker.log", "tail_lines": 200},
    "codex_bug_queue_depth": {"kind": "dir_count", "relpath": "tmp/codex_bug_queue", "pattern": "*.json"},
    "codex_bug_dispatch_last_ts": {"kind": "file_read", "relpath": "tmp/state/codex_bug_dispatch_last.ts"},
    "strategy_py_hash": {"kind": "file_hash", "relpath": "strategy.py"},
}


class CollectorError(Exception):
    pass


def _resolve_in_root(root: Path, relpath: str) -> Path:
    root_real = root.resolve()
    candidate = (root_real / relpath).resolve()
    try:
        candidate.relative_to(root_real)
    except ValueError as exc:
        raise CollectorError(f"resolved path escapes root: {relpath}") from exc
    return candidate


def _tail_lines(path: Path, n: int) -> list[str]:
    with path.open("rb") as f:
        data = f.read(MAX_READ_BYTES)
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-n:]


def collect(name: str, *, root: Path, event_id: str, now: int | None = None) -> dict[str, Any]:
    if name not in ALLOWLIST:
        raise CollectorError(f"unknown collection source (not in allowlist): {name}")
    spec = ALLOWLIST[name]
    if now is None:
        now = int(time.time())
    path = _resolve_in_root(root, spec["relpath"])

    kind = spec["kind"]
    status = "ok"
    content: Any = None

    if kind == "log_tail":
        if path.exists():
            raw_lines = _tail_lines(path, int(spec.get("tail_lines", 200)))
            content = redact.redact_lines(raw_lines)
        else:
            status = "unavailable"
            content = []
    elif kind == "file_read":
        if path.exists():
            raw = path.read_bytes()[:MAX_READ_BYTES].decode("utf-8", errors="replace")
            content = redact.redact_text(raw)
        else:
            status = "unavailable"
            content = None
    elif kind == "file_hash":
        if path.exists():
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            status = "unavailable"
            content = None
    elif kind == "dir_count":
        if path.is_dir():
            content = len(list(path.glob(spec.get("pattern", "*"))))
        else:
            status = "unavailable"
            content = 0
    else:  # pragma: no cover - guarded by ALLOWLIST authoring
        raise CollectorError(f"unsupported collection kind: {kind}")

    content_json = json.dumps(content, ensure_ascii=False, sort_keys=True)
    snapshot = {
        "event_id": event_id,
        "source": name,
        "kind": kind,
        "status": status,
        "captured_at": now,
        "evidence_ref": f"{event_id}/{name}",
        "content": content,
        "content_sha256": hashlib.sha256(content_json.encode("utf-8")).hexdigest(),
    }
    return snapshot


def write_snapshot(snapshot: dict[str, Any], out_dir: Path) -> Path:
    event_dir = out_dir / snapshot["event_id"]
    event_dir.mkdir(parents=True, exist_ok=True)
    dest = event_dir / f"{snapshot['source']}.json"
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(event_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return dest


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--list", action="store_true", help="print allowlisted source names and exit")
    p.add_argument("--event-id")
    p.add_argument("--source")
    p.add_argument("--root", default=".")
    p.add_argument("--out-dir")
    args = p.parse_args(argv)

    if args.list:
        print(json.dumps(sorted(ALLOWLIST.keys())))
        return 0

    if not args.event_id or not args.source or not args.out_dir:
        print("error: --event-id, --source and --out-dir are required (or use --list)", file=sys.stderr)
        return 2

    try:
        snapshot = collect(args.source, root=Path(args.root), event_id=args.event_id)
    except CollectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    dest = write_snapshot(snapshot, Path(args.out_dir))
    print(json.dumps({"evidence_ref": snapshot["evidence_ref"], "path": str(dest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
