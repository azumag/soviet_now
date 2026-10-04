"""Conservative terminal-state check shared by the player and lifecycle broker."""

from __future__ import annotations

import time
from collections.abc import Mapping


STOP_QUIET_SECONDS = 30


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
