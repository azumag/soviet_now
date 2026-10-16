"""Lock the Soren game's terminal-state report (GAME OVER must stay GAMEOVER).

The Unity WebGL game used to leave ``GameState.GAMEOVER`` on the first frame of
the game-over screen and immediately move to ``GameState.STOP``.  Because
``SorenBridge.cs`` pushes ``game_state.json`` every 0.1 seconds, the external
observer only ever saw ``STOP``, which the shared terminal predicate cannot tell
apart from the founding animation / next-game MOVE wait.  The corner switch then
waited for a round boundary that never arrived.

The game-side fix (``sorengame/patches/0001-report-gameover-terminal-state.patch``)
keeps ``GAMEOVER`` until RETRY reloads the scene.  These tests fail if the patch
or the patched source regresses to the old STOP transition.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.game_terminal import is_terminal  # noqa: E402


PATCH = REPO_ROOT / "sorengame" / "patches" / "0001-report-gameover-terminal-state.patch"
DEFAULT_UNITY_SOURCE = (
    REPO_ROOT
    / "sorengame"
    / "_extracted"
    / "soren-game-fixed"
    / "Assets"
    / "SORENGAMEFIXED"
    / "Script"
    / "MainManager.cs"
)
UNITY_SOURCE = Path(os.environ.get("SORENGAME_UNITY_SOURCE", DEFAULT_UNITY_SOURCE))


def game_over_state_body(source: str) -> str:
    """Return the body of ``void GameOverState()`` with braces balanced."""

    marker = "void GameOverState()"
    start = source.index(marker)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1:index]
    raise AssertionError("unbalanced GameOverState() braces")


class GameOverPatchTest(unittest.TestCase):
    def test_patch_records_the_gameover_invariant(self):
        self.assertTrue(PATCH.is_file(), f"missing game patch: {PATCH}")
        patch = PATCH.read_text(encoding="utf-8")
        self.assertIn("MainManager.cs", patch)
        self.assertIn("-        gameState = GameState.STOP;", patch)
        self.assertIn("+        if(gameOverShown) return;", patch)
        self.assertIn("+    bool gameOverShown = false;", patch)

    @unittest.skipUnless(UNITY_SOURCE.is_file(), "Unity game source is not present")
    def test_patched_source_keeps_gameover_and_never_falls_back_to_stop(self):
        source = UNITY_SOURCE.read_text(encoding="utf-8")
        body = game_over_state_body(source)
        self.assertNotIn("gameState = GameState.STOP;", body)
        self.assertIn("if(gameOverShown) return;", body)
        self.assertIn("gameOverUI.SetActive(true);", body)
        self.assertIn("Time.timeScale = 0;", body)
        self.assertIn("bool gameOverShown = false;", source)

    def test_reported_gameover_is_terminal_even_while_a_founding_marker_exists(self):
        # The founding marker must not downgrade a real game over to a STOP wait.
        gameover = {"state": "GAMEOVER", "score": 1470, "makeSorenCount": 0}
        self.assertTrue(is_terminal(gameover, state_mtime=None, founding_seen=True))
        self.assertTrue(
            is_terminal(gameover, state_mtime=100, founding_seen=True, now=100)
        )


if __name__ == "__main__":
    unittest.main()
