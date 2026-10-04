"""Conservative terminal-state check shared by the player and lifecycle broker."""

from __future__ import annotations

import time
from collections.abc import Mapping


STOP_QUIET_SECONDS = 30
FOUNDING_STOP_QUIET_SECONDS = 300


def is_stale_founding_stop(
    game_state: Mapping[str, object],
    *,
    state_mtime: float | None,
    founding_seen: bool = False,
    now: float | None = None,
) -> bool:
    """Return true only for a completed founding STOP that stayed quiet.

    A fresh STOP can be the beginning of the Soviet founding animation.  The
    marker and a positive counter prove that founding actually happened, while
    the longer quiet window prevents the handover from cutting that animation
    short.  A missing timestamp is never boundary evidence.
    """
    if game_state.get("state") != "STOP" or not founding_seen:
        return False
    count = game_state.get("makeSorenCount")
    if isinstance(count, bool) or not isinstance(count, (int, float)) or count <= 0:
        return False
    if state_mtime is None:
        return False
    if now is None:
        now = time.time()
    return 0 <= now - state_mtime >= FOUNDING_STOP_QUIET_SECONDS


def is_terminal(
    game_state: Mapping[str, object],
    *,
    state_mtime: float | None,
    founding_seen: bool = False,
    now: float | None = None,
) -> bool:
    """Accept GAMEOVER or a conservatively quiet legacy STOP boundary."""
    state = game_state.get("state")
    if state == "GAMEOVER":
        return True
    if state != "STOP":
        return False
    if is_stale_founding_stop(
        game_state,
        state_mtime=state_mtime,
        founding_seen=founding_seen,
        now=now,
    ):
        return True
    if founding_seen:
        return False
    count = game_state.get("makeSorenCount")
    if isinstance(count, bool) or not isinstance(count, (int, float)) or count != 0:
        return False
    if state_mtime is None:
        return False
    if now is None:
        now = time.time()
    return 0 <= now - state_mtime >= STOP_QUIET_SECONDS
