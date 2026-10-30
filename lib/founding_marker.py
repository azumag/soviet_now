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

import enum
import json
import os
import time
from pathlib import Path

MARKER = Path("tmp/markers/.soviet_created")
OBSERVATION = Path("tmp/state/game_observation.json")

# Clock/file-granularity slack for the legacy mtime rule.  The runner writes the
# marker immediately after it observes the founding board, so a current marker
# is never older than the board it was written next to.  This is far below the
# 30s quiet window that a legacy STOP needs before it is called terminal.
MTIME_GRACE_SECONDS = 5.0

# soviet_now#714: an observation record only proves the match identity while it
# is intact and fresh.  The bridge heartbeats it every <=1s while it sees the
# game; anything older is a leftover, not live evidence.
OBSERVATION_SCHEMA = 1
OBSERVATION_FRESH_SECONDS = 15.0
OBSERVATION_FUTURE_SECONDS = 5.0


class FoundingMarkerVerdict(enum.Enum):
    """Whether the marker may suppress the terminal decision for this board.

    ``UNKNOWN`` exists because weak evidence must never end a board: a marker
    that cannot be attributed either way still suppresses ``is_terminal``,
    exactly like a current one, so no automatic retry is ever authorised on
    unclear evidence (soviet_now#714).
    """

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


def _identity(value) -> str | None:
    """Accept only a plain, bounded nonce; anything else is not an identity."""
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > 64 or any(character.isspace() for character in token):
        return None
    return token


def _observation_record(observation_path) -> dict | None:
    """Raw observation record; callers that need trust must validate it."""
    try:
        record = json.loads(Path(observation_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def observation_game_id(observation_path=OBSERVATION) -> str | None:
    """Per-match identity from the bridge's own observation record, if it can be read.

    Permissive accessor used where the *last reported* id is what matters
    (e.g. recording it into the marker).  The terminal verdict path uses
    :func:`_fresh_observation` instead, which also checks schema, epoch and
    board consistency before trusting the id.
    """
    record = _observation_record(observation_path)
    if not isinstance(record, dict):
        return None
    return _identity(record.get("game_id"))


_BOARD_FIELDS = ("state", "score", "makeSorenCount", "pieces")


def _board_view(state):
    """The board fields the bridge records, projected out of a full state."""
    if not isinstance(state, dict):
        return None
    return {field: state.get(field) for field in _BOARD_FIELDS}


def _live_observation(observation_path, *, now=None):
    """Observation record that is intact and fresh, or ``None``.

    soviet_now#714 HIGH: an unchecked read could match a marker against a
    stale, foreign or half-written record.  A record only counts as live
    evidence when its schema is known, the epoch is neither future-dated nor
    stale, and it carries a usable match id and board.  Board-vs-board
    agreement is checked by the caller with a re-read guard.
    """
    now = time.time() if now is None else now
    record = _observation_record(observation_path)
    if record is None or record.get("schema") != OBSERVATION_SCHEMA:
        return None
    if _identity(record.get("game_id")) is None:
        return None
    try:
        epoch = float(record.get("observed_epoch"))
    except (TypeError, ValueError):
        return None
    if epoch > now + OBSERVATION_FUTURE_SECONDS or now - epoch > OBSERVATION_FRESH_SECONDS:
        return None
    if not isinstance(record.get("board"), dict):
        return None
    return record


def _fresh_observation(observation_path, state, *, now=None):
    """Live observation describing ``state``'s board, or a verdict signal.

    Returns the record when the live observation's board is the board being
    decided on, ``False`` when a live observation exists but describes a
    different board (contradictory evidence, not absence of evidence), and
    ``None`` when no usable live observation exists at all.
    """
    wanted = _board_view(state)
    record = _live_observation(observation_path, now=now)
    if record is not None and (wanted is None or record["board"] == wanted):
        return record
    # Torn read guard: re-read once.  A stable mismatch means the observation
    # really describes a different board; a torn first read does not.
    record = _live_observation(observation_path, now=now)
    if record is None:
        return None
    return record if (wanted is None or record["board"] == wanted) else False


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


def _state_json(state_path):
    try:
        value = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def founding_marker_verdict(root=Path("."), *, marker_path=None, state_path=None,
                            observation_path=None, now=None) -> FoundingMarkerVerdict:
    """Attribute the marker to the board being decided on, in three values.

    ``CURRENT`` — live evidence says the marker belongs to this match.
    ``STALE``   — live evidence says it belongs to another match (or there is
                  no marker at all); it must not suppress the terminal decision.
    ``UNKNOWN`` — evidence is absent, torn, stale or contradictory; soviet_now#714
                  requires this to behave like CURRENT for suppression so an
                  automatic retry is never authorised on unclear evidence.

    Only a *fresh, schema-valid observation whose board is the board being
    decided on* may authorise STALE.  A marker-vs-board mtime gap alone can no
    longer do so: the board advancing past the marker does not prove a new
    match (soviet_now#714 HIGH).
    """

    root = Path(root)
    marker = Path(marker_path) if marker_path is not None else root / MARKER
    state = Path(state_path) if state_path is not None else root / "game_state.json"
    observation = Path(observation_path) if observation_path is not None else root / OBSERVATION

    if not marker.is_file():
        return FoundingMarkerVerdict.STALE

    state_json = _state_json(state)
    live = _fresh_observation(observation, state_json, now=now)
    if live is False:
        # A live bridge sees a different board: this board's marker cannot be
        # attributed either way and must keep suppressing.
        return FoundingMarkerVerdict.UNKNOWN
    if live is not None:
        recorded = marker_game_id(marker)
        if recorded is None:
            # A legacy marker cannot be tied to the live match id; a board that
            # matches the observation does not prove the marker either.
            return FoundingMarkerVerdict.UNKNOWN
        observed = _identity(live.get("game_id"))
        return (FoundingMarkerVerdict.CURRENT if recorded == observed
                else FoundingMarkerVerdict.STALE)

    marker_mtime, state_mtime = _mtime(marker), _mtime(state)
    if marker_mtime is None or state_mtime is None:
        return FoundingMarkerVerdict.UNKNOWN
    if marker_mtime + MTIME_GRACE_SECONDS >= state_mtime:
        return FoundingMarkerVerdict.CURRENT
    return FoundingMarkerVerdict.UNKNOWN


def founding_marker_is_current(root=Path("."), *, marker_path=None, state_path=None,
                              observation_path=None, now=None) -> bool:
    """True when the marker may suppress the terminal decision for this board.

    Compatibility wrapper over :func:`founding_marker_verdict`: CURRENT and
    UNKNOWN both suppress (weak evidence never ends a board); only STALE frees
    the terminal decision.
    """
    return founding_marker_verdict(
        root, marker_path=marker_path, state_path=state_path,
        observation_path=observation_path, now=now,
    ) is not FoundingMarkerVerdict.STALE
