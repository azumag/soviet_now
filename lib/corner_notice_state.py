"""Read docich's next-corner notice for Soren's prediction/improvement gates.

The corner rotation publishes ``next_corner.json`` next to its ledger. While a
clock is promised there, the 48-game prediction must not start: its corner
boundary would hold the announced corner back and make the clock a lie. After a
corner the rotation may publish ``hold="prediction-pending"`` so the next
prediction starts before a new clock is promised, and while the prediction is
in flight it publishes ``hold="prediction"`` plus the round's remaining games.

This module deliberately does not import docich: the Soren worker only needs
the bounded public projection, and an unreadable/missing notice means there is
no promise (clear), so a broken projection cannot stall the game.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


FILE = "next_corner.json"
SCHEMA = 1
# The rotation refreshes every minute; this is the same staleness bound as the
# broadcast adapter. A stopped timer must not gate the game forever.
MAX_AGE_SECONDS = 600.0
SOURCES = frozenset({"auto", "manual"})
# Known holds. An unknown hold is treated as blocking (fail-safe) rather than
# silently ignoring a new promise the rotation may have started publishing.
HOLDS = frozenset({"prediction", "prediction-pending"})
DEFAULT_DOCICH_STATE_DIR = Path("/home/ubuntu/docich/run-soren-live")
CORNER_LIMIT = 64
LABEL_LIMIT = 40
WHEN_LIMIT = 16


def resolve_state_dir(value=None) -> Path:
    """Resolve docich's public state directory.

    Same precedence as the status dashboard: explicit override, environment,
    the conventional sibling checkout, then the VM default.
    """
    if value is not None:
        return Path(value).expanduser()
    configured = os.getenv("DOCICH_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    sibling = Path.cwd().parent / "docich" / "run-soren-live"
    if sibling.is_dir():
        return sibling
    return DEFAULT_DOCICH_STATE_DIR


def _text(value, limit):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    return text


def load(state_dir=None, *, now=None):
    """Return the bounded notice record, or ``None`` when there is no promise.

    A missing, unreadable, stale, or invalid record is clear: no viewer saw a
    promise, so the game must not be held back by it.
    """
    now = time.time() if now is None else float(now)
    path = resolve_state_dir(state_dir) / FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    corner = _text(raw.get("corner"), CORNER_LIMIT)
    label = _text(raw.get("label"), LABEL_LIMIT)
    when = _text(raw.get("when"), WHEN_LIMIT)
    source = raw.get("source")
    hold = raw.get("hold")
    due_at = raw.get("due_at")
    updated_at = raw.get("updated_at")
    if (corner is None or label is None or when is None
            or source not in SOURCES
            or (hold is not None and not isinstance(hold, str))
            or type(due_at) is not int or isinstance(due_at, bool)
            or type(updated_at) not in (int, float) or isinstance(updated_at, bool)):
        return None
    if not 0 <= now - float(updated_at) <= MAX_AGE_SECONDS:
        return None
    if hold is not None and len(hold) > 32:
        return None
    return {
        "corner": corner,
        "label": label,
        "when": when,
        "source": source,
        "hold": hold,
        "due_at": due_at,
        "updated_at": float(updated_at),
    }


def mode(state_dir=None, *, now=None) -> str:
    """Return ``clear`` / ``time`` / ``pending`` / ``prediction`` / ``blocked``."""
    notice = load(state_dir, now=now)
    if notice is None:
        return "clear"
    hold = notice["hold"]
    if hold is None:
        return "time"
    if hold not in HOLDS:
        # A future hold is still a promise-shaped record: do not start a cycle
        # that could hold the announced corner back.
        return "blocked"
    if hold == "prediction-pending":
        return "pending"
    return "prediction"


def prediction_blocked(state_dir=None, *, now=None) -> bool:
    """True when a new 48-game prediction must not start.

    ``pending`` is the one held state that permits a start: the rotation wants
    the next prediction to begin before it promises another clock. ``clear``
    keeps the existing prediction gates in the worker/script.
    """
    return mode(state_dir, now=now) not in ("clear", "pending")


def improvement_blocked(state_dir=None, *, now=None) -> bool:
    """True while any live notice is out, so no new improvement may start."""
    return mode(state_dir, now=now) != "clear"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "mode"
    if command == "mode":
        print(mode())
        return 0
    if command == "blocked":
        target = argv[1] if len(argv) > 1 else ""
        if target == "prediction":
            blocked = prediction_blocked()
        elif target == "improvement":
            blocked = improvement_blocked()
        else:
            print("1")
            return 2
        print("1" if blocked else "0")
        return 0
    print("1")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
