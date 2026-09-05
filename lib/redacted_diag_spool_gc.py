#!/usr/bin/env python3
"""docich#33: TTL purge for the restricted raw-comment spool.

The spool (see redacted_diag_ingest.py) is the only place a viewer's raw
comment text is ever written. This script deletes entries once their
``expires_at`` has passed, so the raw text is never retained permanently.

Fail-safe by design: any entry that cannot be parsed, or that is missing
``expires_at``, is treated as already expired and purged rather than kept
around indefinitely.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def purge_expired(spool_dir: Path, *, now: int | None = None, dry_run: bool = False) -> dict[str, int]:
    if now is None:
        now = int(time.time())
    purged = 0
    kept = 0
    corrupt_purged = 0

    if not spool_dir.exists():
        return {"purged": 0, "kept": 0, "corrupt_purged": 0}

    for path in sorted(spool_dir.glob("*.json")):
        expires_at = None
        corrupt = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            expires_at = data.get("expires_at")
            if not isinstance(expires_at, (int, float)):
                corrupt = True
        except Exception:
            corrupt = True

        expired = corrupt or expires_at is None or now > expires_at
        if expired:
            if not dry_run:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            purged += 1
            if corrupt:
                corrupt_purged += 1
        else:
            kept += 1

    return {"purged": purged, "kept": kept, "corrupt_purged": corrupt_purged}


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spool-dir", required=True)
    p.add_argument("--now", type=int, default=None, help="override current time (tests only)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    result = purge_expired(Path(args.spool_dir), now=args.now, dry_run=args.dry_run)
    # Intentionally metadata-only: never print file names or content, which
    # could otherwise re-leak the very raw text this script exists to expire.
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
