"""Which match does the founding marker belong to?

``tmp/markers/.soviet_created`` only proves that *some* match reached Soviet
founding.  It never said *which* match, so a marker left behind by an earlier
match used to keep the shared terminal predicate from recognising the next
match's real GAME OVER (docich#2345: the board sat on ``STOP`` for ~15h40m and
no retry was ever sent).

The bridge already writes an independent per-match identity: the observation
record at ``tmp/state/game_observation.json`` carries a ``game_id`` nonce that
rotates on RETRY, on a bridge relaunch and on every new match
(``lib/game_observation.mjs``; ``docs/founding-stop-boundary.md``).  The runner
records that id inside the marker, and this module compares the two.

Markers written before this identity existed, and moments where no fresh bridge
observation is available, fall back to the documented board-mtime rule:
``game_state.json`` only advances when the board changes, so a marker that is
older than the board it is read next to cannot belong to that board.

Both rules answer exactly one question: may this marker suppress the terminal
decision for the board we are looking at right now?  They never authorise game
input, a retry, or a boundary ACK on their own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MARKER = Path("tmp/markers/.soviet_created")
OBSERVATION = Path("tmp/state/game_observation.json")

# Clock/file-granularity slack for the legacy mtime rule.  The runner writes the
# marker immediately after it observes the founding board, so a current marker
# is never older than the board it was written next to.  This is far below the
# 30s quiet window that a legacy STOP needs before it is called terminal.
MTIME_GRACE_SECONDS = 5.0


def _identity(value) -> str | None:
    """Accept only a plain, bounded nonce; anything else is not an identity."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > 64 or any(character.isspace() for character in token):
        return None
    return token


def observation_game_id(observation_path=OBSERVATION) -> str | None:
    """Per-match identity from the bridge's own observation record, if it can be read."""
    try:
        record = json.loads(Path(observation_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    return _identity(record.get("game_id"))


def marker_game_id(marker_path) -> str | None:
    """Identity recorded inside the marker; legacy ``"<turn>"`` payloads have none."""
    try:
        payload = Path(marker_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if "|" not in payload:
        return None
    return _identity(payload.split("|")[-1])


def marker_payload(turn, game_id) -> str:
    """Marker body.  Keep the legacy ``"<turn>"`` line when no identity is known."""
    token = _identity(game_id)
    return f"{turn}|{token}\n" if token else f"{turn}\n"


def write_founding_marker(marker_path, turn, game_id) -> None:
    """Record the founding match identity where the terminal check can re-read it.

    The write is atomic (same-directory temp + ``os.replace``): the shell loop
    and the lifecycle broker read this file independently, and a torn read of a
    half-written identity would look like a marker owned by another match.
    """
    path = Path(marker_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(marker_payload(turn, game_id), encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _mtime(path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def founding_marker_is_current(root=Path("."), *, marker_path=None, state_path=None,
                              observation_path=None) -> bool:
    """True only when the marker belongs to the board it is being read next to.

    Identity wins whenever both sides have it: a marker recorded for another
    match is stale even if it is recent, and a marker that matches the current
    match is current even if the board changed after it.  Without both sides the
    marker must not predate the board by more than ``MTIME_GRACE_SECONDS``.  An
    unresolvable comparison keeps the marker current, which preserves the
    pre-existing "never end a board on unclear evidence" behaviour.
    """

    root = Path(root)
    marker = Path(marker_path) if marker_path is not None else root / MARKER
    state = Path(state_path) if state_path is not None else root / "game_state.json"
    observation = Path(observation_path) if observation_path is not None else root / OBSERVATION

    if not marker.is_file():
        return False

    recorded = marker_game_id(marker)
    observed = observation_game_id(observation)
    if recorded is not None and observed is not None:
        return recorded == observed

    marker_mtime, state_mtime = _mtime(marker), _mtime(state)
    if marker_mtime is None or state_mtime is None:
        return True
    return marker_mtime + MTIME_GRACE_SECONDS >= state_mtime
