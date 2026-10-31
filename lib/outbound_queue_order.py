#!/usr/bin/env python3
"""Select the next outbound chat item by priority, enqueue time, then id."""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_PRIORITY = 5
_PRIORITY_RE = re.compile(r"^[0-9]+$")
_LEADING_TIMESTAMP_RE = re.compile(r"^([0-9]+)")


def pending_sort_key(path: Path) -> tuple[int, int, str]:
    """Return (priority, enqueue_time, id) for an outbound queue item.

    Current items use ``{timestamp}_{source}_{priority}.msg``.  The source may
    contain underscores, so priority is parsed from the right.  Legacy or
    damaged items without a usable priority are drained at the default
    priority instead of being stranded.
    """

    name = path.name
    stem = name[:-4] if name.endswith(".msg") else name
    timestamp_token, separator, remainder = stem.partition("_")

    match = _LEADING_TIMESTAMP_RE.match(timestamp_token)
    if match:
        enqueue_time = int(match.group(1))
    else:
        try:
            enqueue_time = path.stat().st_mtime_ns
        except OSError:
            enqueue_time = 0

    priority = DEFAULT_PRIORITY
    if separator and "_" in remainder:
        _source, _separator, priority_token = remainder.rpartition("_")
        if _PRIORITY_RE.fullmatch(priority_token):
            priority = int(priority_token)

    return priority, enqueue_time, name


def select_next_pending(pending_dir: Path) -> Path | None:
    candidates = [path for path in pending_dir.glob("*.msg") if path.is_file()]
    if not candidates:
        return None
    return min(candidates, key=pending_sort_key)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} PENDING_DIR", file=sys.stderr)
        return 2

    selected = select_next_pending(Path(argv[1]))
    if selected is None:
        return 1
    print(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
