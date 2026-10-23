"""Ownership of the founding marker (docich#2345).

The 2026-10-16 incident: a match founded, the bridge relaunched a *new* match
while the old runner kept holding the founding flag, and the new match's real
GAME OVER was read as "founding STOP, keep waiting" for ~15h40m.  These tests
pin the two rules that make the marker's ownership explicit - the bridge's
per-match ``game_id`` and the board-mtime fallback for legacy markers - at the
shared helper, the outer shell gate, and the runner's own wait path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy_runner  # noqa: E402
from lib.founding_marker import (  # noqa: E402
    founding_marker_is_current,
    marker_game_id,
    write_founding_marker,
)
from lib.game_terminal import STOP_QUIET_SECONDS, is_terminal  # noqa: E402


MATCH_A = "11111111-1111-4111-8111-111111111111"
MATCH_B = "22222222-2222-4222-8222-222222222222"


def seed_match(root: Path, game_id: str, *, marker_age: float = 0.0, board_age: float = 0.0) -> Path:
    """Write the three runtime files the ownership rule reads."""
    (root / "tmp/markers").mkdir(parents=True, exist_ok=True)
    (root / "tmp/state").mkdir(parents=True, exist_ok=True)
    state_path = root / "game_state.json"
    state_path.write_text(json.dumps({"state": "STOP", "score": 1976, "makeSorenCount": 0, "pieces": []}))
    marker = root / "tmp/markers/.soviet_created"
    write_founding_marker(marker, 189, game_id or None)
    observation = {"schema": 1, "game_id": game_id, "stop_id": None,
                   "board": {"state": "MOVE", "score": 0, "makeSorenCount": 0, "pieces": []},
                   "observed_epoch": time.time()}
    (root / "tmp/state/game_observation.json").write_text(json.dumps(observation))
    now = time.time()
    os.utime(marker, (now - marker_age, now - marker_age))
    os.utime(state_path, (now - board_age, now - board_age))
    return state_path


class FoundingMarkerIdentityTest(unittest.TestCase):
    def test_marker_of_the_same_match_still_keeps_a_legacy_stop_open(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seed_match(root, MATCH_A, board_age=STOP_QUIET_SECONDS + 5)
            self.assertTrue(founding_marker_is_current(root))
            state = json.loads((root / "game_state.json").read_text())
            self.assertFalse(
                is_terminal(
                    state,
                    state_mtime=(root / "game_state.json").stat().st_mtime,
                    founding_seen=founding_marker_is_current(root),
                )
            )

    def test_marker_left_by_a_previous_match_cannot_mask_the_next_game_over(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # The marker belongs to match A; the bridge already observes match B.
            seed_match(root, MATCH_A, board_age=STOP_QUIET_SECONDS + 5)
            observation = json.loads((root / "tmp/state/game_observation.json").read_text())
            observation["game_id"] = MATCH_B
            (root / "tmp/state/game_observation.json").write_text(json.dumps(observation))

            self.assertFalse(founding_marker_is_current(root))
            state = json.loads((root / "game_state.json").read_text())
            self.assertTrue(
                is_terminal(
                    state,
                    state_mtime=(root / "game_state.json").stat().st_mtime,
                    founding_seen=founding_marker_is_current(root),
                )
            )

    def test_legacy_marker_older_than_the_board_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seed_match(root, MATCH_A, marker_age=STOP_QUIET_SECONDS + 60, board_age=0)
            (root / "tmp/state/game_observation.json").unlink()
            self.assertFalse(founding_marker_is_current(root))

    def test_legacy_marker_written_next_to_the_board_is_current(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seed_match(root, MATCH_A, marker_age=1, board_age=1)
            (root / "tmp/state/game_observation.json").unlink()
            self.assertTrue(founding_marker_is_current(root))

    def test_absent_marker_is_not_founding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seed_match(root, MATCH_A)
            (root / "tmp/markers/.soviet_created").unlink()
            self.assertFalse(founding_marker_is_current(root))

    def test_marker_payload_round_trips_the_match_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "marker"
            write_founding_marker(marker, 189, MATCH_A)
            self.assertEqual(marker_game_id(marker), MATCH_A)
            # The write is atomic: no temp sibling survives, and a reader never
            # sees a half-written identity.
            self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), ["marker"])
            # A pre-identity payload still parses, and yields no identity.
            marker.write_text("189\n")
            self.assertIsNone(marker_game_id(marker))
            write_founding_marker(marker, 189, None)
            self.assertIsNone(marker_game_id(marker))

    def test_outer_loop_retries_after_a_bridge_relaunch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seed_match(root, MATCH_A, board_age=STOP_QUIET_SECONDS + 5)
            env = {**os.environ, "GAME_STATE": str(root / "game_state.json"),
                   "TMP_MARKERS_DIR": str(root / "tmp/markers"), "TMP_STATE_DIR": str(root / "tmp/state")}
            command = "source core/game_state.sh; is_game_over"

            # The founding marker belongs to this board: keep waiting.
            current = subprocess.run(["bash", "-c", command], cwd=REPO_ROOT, env=env, capture_output=True)
            self.assertEqual(current.returncode, 1, current.stderr.decode())

            # The bridge relaunched a new match: the stale marker must not hide it.
            observation = json.loads((root / "tmp/state/game_observation.json").read_text())
            observation["game_id"] = MATCH_B
            (root / "tmp/state/game_observation.json").write_text(json.dumps(observation))
            relaunched = subprocess.run(["bash", "-c", command], cwd=REPO_ROOT, env=env, capture_output=True)
            self.assertEqual(relaunched.returncode, 0, relaunched.stderr.decode())


class RunnerFoundingHoldTest(unittest.TestCase):
    """The runner must release its own founding hold when the match changes."""

    def _run_two_observations(self, states):
        strategy = types.SimpleNamespace(decide=lambda game_state, analysis: {"x": 0.5})
        with mock.patch.object(strategy_runner, "load_strategy_module", return_value=strategy), \
                mock.patch.object(strategy_runner, "get_strategy_hash", return_value="strategy"), \
                mock.patch.object(strategy_runner, "get_strategy_file_hash", return_value="file"), \
                mock.patch.object(strategy_runner, "strategy_fast_drop_deadline_contact_enabled", return_value=False), \
                mock.patch.object(strategy_runner, "wait_for_move_state", side_effect=states), \
                mock.patch.object(strategy_runner, "build_analysis", return_value={"results": [], "same_type": [], "reactor": {}}), \
                mock.patch.object(strategy_runner, "enrich_game_state_deadline_fields"), \
                mock.patch.object(strategy_runner, "trigger_soviet_clip_now"), \
                mock.patch.object(strategy_runner, "commands_empty", return_value=True), \
                mock.patch.object(strategy_runner, "wait_commands_done", return_value=True), \
                mock.patch.object(strategy_runner, "write_drop_command"), \
                mock.patch.dict(os.environ, {"SOVIET_CELEBRATION_PAUSE_SEC": "0"}), \
                mock.patch.object(strategy_runner.time, "sleep"):
            return strategy_runner.run_game()

    def test_new_match_identity_releases_the_hold_and_ends_the_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observation_path = root / "tmp/state/game_observation.json"
            observation_path.parent.mkdir(parents=True, exist_ok=True)

            founding = {"state": "MOVE", "score": 5352, "makeSorenCount": 1, "pieces": []}
            new_match_stop = {"state": "STOP", "score": 0, "makeSorenCount": 0, "pieces": []}

            def publish(game_id, board):
                observation_path.write_text(json.dumps({
                    "schema": 1, "game_id": game_id, "stop_id": None, "board": board,
                    "observed_epoch": time.time(),
                }))

            def wait_for_move_state(*_args, **_kwargs):
                # 1st observation: this match founds. 2nd: the bridge already
                # shows a different match sitting on its own quiet STOP.
                if wait_for_move_state.calls == 0:
                    publish(MATCH_A, founding)
                else:
                    publish(MATCH_B, new_match_stop)
                    state_path = Path(strategy_runner.GAME_STATE)
                    state_path.write_text(json.dumps(new_match_stop))
                    old = time.time() - STOP_QUIET_SECONDS - 5
                    os.utime(state_path, (old, old))
                wait_for_move_state.calls += 1
                return (founding, True) if wait_for_move_state.calls == 1 else (new_match_stop, False)

            wait_for_move_state.calls = 0

            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                result = self._run_two_observations(wait_for_move_state)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(result["state"], "STOP")
            self.assertEqual(result["score"], 0)
            self.assertFalse(result["soviet_created"])
            self.assertFalse((root / "tmp/markers/.soviet_created").exists())


if __name__ == "__main__":
    unittest.main()
