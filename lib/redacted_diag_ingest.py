#!/usr/bin/env python3
"""docich#33: viewer bug-report ingestion for the read-only diagnostic pipeline.

Splits every incoming report into two disjoint stores:

- ``events_dir``: long-lived, safe metadata only -- ``event_id / category /
  time / redacted_context_hash / source``. No raw comment text or viewer
  identity ever reaches this store.
- ``spool_dir``: the *restricted* short-TTL spool holding the raw comment
  text and viewer handle, so an operator can still look at the original
  report if genuinely needed. Every entry carries ``expires_at`` and is
  deleted by ``redacted_diag_spool_gc.py`` once it passes -- see that module
  for the purge logic and its tests for the TTL behaviour.

This module is intentionally NOT wired into ``broadcast/comment.sh`` by this
change (see docich#33 PR notes): it is a standalone, independently testable
building block. Wiring a production ingestion call site is a follow-up.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import redacted_diag_redact as redact  # noqa: E402

DEFAULT_TTL_SEC = 24 * 3600  # matches the existing 24h dedup window used elsewhere in this repo


def _atomic_write_json(path: Path, payload: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def ingest_report(
    *,
    category: str,
    user: str,
    comment: str,
    source: str,
    events_dir: Path,
    spool_dir: Path,
    ttl_sec: int = DEFAULT_TTL_SEC,
    now: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Ingest one raw viewer report; return the safe event record only.

    The caller (and anything downstream of the return value) never sees the
    raw comment/user -- those are only ever written to ``spool_dir``.
    """
    if now is None:
        now = int(time.time())
    if event_id is None:
        event_id = uuid.uuid4().hex

    comment = comment or ""
    user = user or ""
    category = category or "unknown"

    event = {
        "event_id": event_id,
        "category": category,
        "time": now,
        "redacted_context_hash": redact.redacted_context_hash(category, comment),
        "source": source or "unknown",
    }
    _atomic_write_json(Path(events_dir) / f"{event_id}.json", event, 0o644)

    restricted = {
        "event_id": event_id,
        "user": user,
        "comment": comment,
        "category": category,
        "source": source or "unknown",
        "ingested_at": now,
        "expires_at": now + int(ttl_sec),
    }
    restricted_path = Path(spool_dir) / f"{event_id}.json"
    _atomic_write_json(restricted_path, restricted, 0o600)
    try:
        os.chmod(spool_dir, stat.S_IRWXU)
    except OSError:
        pass

    return event


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--category", required=True)
    p.add_argument("--user", default="")
    p.add_argument("--comment", required=True)
    p.add_argument("--source", default="unknown")
    p.add_argument("--events-dir", required=True)
    p.add_argument("--spool-dir", required=True)
    p.add_argument("--ttl-sec", type=int, default=DEFAULT_TTL_SEC)
    args = p.parse_args(argv)

    event = ingest_report(
        category=args.category,
        user=args.user,
        comment=args.comment,
        source=args.source,
        events_dir=Path(args.events_dir),
        spool_dir=Path(args.spool_dir),
        ttl_sec=args.ttl_sec,
    )
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
