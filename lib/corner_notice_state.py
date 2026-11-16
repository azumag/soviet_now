"""Read docich's corner state for Soren's prediction/improvement gates.

The corner rotation publishes ``next_corner.json`` next to its ledger. While a
clock is promised there, the 48-game prediction must not start: its corner
boundary would hold the announced corner back and make the clock a lie. After a
corner the rotation may publish ``hold="prediction-pending"`` so the next
prediction starts before a new clock is promised, and while the prediction is
in flight it publishes ``hold="prediction"`` plus the round's remaining games.

While a corner actually runs, ``game_switch.json`` names its view (for example
``paper-view``) and no ``next_corner.json`` is out. A new prediction must wait
there too: the Soren game is parked, so nobody is playing the promised 48
matches. See :func:`corner_active`.

The improvement gate is deliberately narrower than the prediction gate. The
rotation publishes the next corner as soon as it is decided, often hours before
``due_at``; treating the notice's mere presence as an improvement blocker
freezes the 48-game improvement cycle for as long as a promise is out (which
is effectively always). Only a prediction in flight (a non-null ``hold``) or a
notice due within :data:`LEAD_SECONDS` blocks a new improvement.

This module deliberately does not import docich: the Soren worker only needs
the bounded public projections, and an unreadable/missing projection means
there is no promise or corner proof (clear), so a broken projection cannot
stall the game.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
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
# Canonical as-on-air projection. The Soren game is the only active game that
# keeps its own 48-game cycle playable; every other active game is a corner
# (or a failed/transitioning switch) that has parked the Soren loop.
GAME_SWITCH_FILE = "game_switch.json"
GAME_SWITCH_LIMIT = 262144
SOREN_GAME = "sorengame"
# Canonical rotation ledger. A confirmed slot keeps ``pending`` (or
# ``manual_pending``) until the corner actually starts, and the next-corner
# notice is withdrawn at the same moment; this is the only projection that
# covers the gap between the two.
ROTATION_FILE = "corner_rotation.json"
ROTATION_SCHEMA = 1
# A corner due within this lead is imminent even when the notice projection is
# missing or late (the dispatcher starts it in the next tick). Starting a new
# 48-game prediction there would leave it open when the corner begins.
LEAD_SECONDS = 300.0
# Phases from the GameSwitch contract. An unknown phase means the file is not
# a state this module understands, so it is treated as absent (fail-open)
# rather than as a corner that would block every new prediction.
GAME_SWITCH_PHASES = frozenset({
    "idle",
    "validating",
    "preparing",
    "draining",
    "quiescing",
    "starting",
    "probing",
    "committing",
    "ready",
    "stopping",
    "rolling_back",
    "failed",
    "recovery_required",
})


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


def _read_state_file(state_dir, name, limit):
    """Read one bounded state projection, or ``None``.

    A missing, unreadable, oversized, symlinked or non-object file is absent:
    it is not proof of a corner.
    """
    path = resolve_state_dir(state_dir) / name
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _read_game_switch(state_dir=None):
    return _read_state_file(state_dir, GAME_SWITCH_FILE, GAME_SWITCH_LIMIT)


def corner_active(state_dir=None) -> bool:
    """True when a corner (not the Soren game) owns the on-air screen.

    ``game_switch.json`` is the canonical projection of what docich is showing.
    Only a readable record with a known phase counts:

    * ``ready`` with ``active.game == "sorengame"`` is the playable Soren game;
    * every other known phase is a corner, a recovery, or a switch in progress,
      where a 48-game prediction has no game to cover;
    * an unknown phase is treated as absent so a future/invalid shape cannot
      block predictions forever (the next_corner gate still applies).
    """
    record = _read_game_switch(state_dir)
    if record is None:
        return False
    phase = record.get("phase")
    if phase not in GAME_SWITCH_PHASES:
        return False
    if phase != "ready":
        return True
    active = record.get("active")
    if not isinstance(active, dict):
        return True
    game = active.get("game")
    return not (isinstance(game, str) and game == SOREN_GAME)


def corner_pending(state_dir=None, *, now=None) -> bool:
    """True while a corner slot is confirmed or imminently due.

    The dispatcher sets ``pending``/``manual_pending`` in
    ``corner_rotation.json`` and withdraws ``next_corner.json`` at the same
    time, then waits for the game boundary / audio before the game switch.
    A new prediction created in that gap would be open when the corner appears.
    An interval slot due within :data:`LEAD_SECONDS` is treated the same way:
    the notice projection can be missing/late and the dispatcher would start
    the corner in the next tick. A missing/invalid ledger is not proof of a
    slot (fail-open); queue mode has no clock and relies on pending/active.
    """
    record = _read_state_file(state_dir, ROTATION_FILE, GAME_SWITCH_LIMIT)
    if record is None or record.get("schema_version") != ROTATION_SCHEMA:
        return False
    for key in ("pending", "manual_pending"):
        value = record.get(key)
        if isinstance(value, dict) and value:
            return True
    if record.get("schedule_mode") != "interval" or record.get("status") != "waiting":
        return False
    due_at = record.get("next_due_at")
    if (type(due_at) not in (int, float) or isinstance(due_at, bool)
            or not math.isfinite(float(due_at))):
        return False
    now = time.time() if now is None else float(now)
    return 0 <= float(due_at) - now <= LEAD_SECONDS


def prediction_blocked(state_dir=None, *, now=None) -> bool:
    """True when a new 48-game prediction must not start.

    ``pending`` is the one held notice state that permits a start: the rotation
    wants the next prediction to begin before it promises another clock.
    ``clear`` keeps the existing prediction gates in the worker/script. A
    running corner (``corner_active``) or a confirmed slot that has not started
    yet (``corner_pending``) always blocks, including during that notice hold.
    """
    if corner_active(state_dir) or corner_pending(state_dir, now=now):
        return True
    return mode(state_dir, now=now) not in ("clear", "pending")


def improvement_blocked(state_dir=None, *, now=None) -> bool:
    """True only when a new improvement cycle could push the next corner.

    ``next_corner.json`` is a viewer-facing promise and stays out between
    corners. Blocking on its bare presence would never let the 48-game batch
    start, so only a prediction in flight (a non-null ``hold``) or a notice due
    within :data:`LEAD_SECONDS` blocks the improvement cycle. Missing or
    invalid projections are fail-open, matching :func:`load`.
    """
    notice = load(state_dir, now=now)
    if notice is None:
        return False
    if notice["hold"] is not None:
        return True
    now_value = time.time() if now is None else float(now)
    return float(notice["due_at"]) - now_value <= LEAD_SECONDS


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "mode"
    if command == "mode":
        print(mode())
        return 0
    if command == "corner":
        print("1" if (corner_active() or corner_pending()) else "0")
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
