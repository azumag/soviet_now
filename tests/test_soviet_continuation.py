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

import strategy_runner
from lib.game_terminal import STOP_QUIET_SECONDS, is_terminal


class SovietContinuationTest(unittest.TestCase):
    def test_quiet_non_founding_stop_is_terminal_but_founding_stop_is_not(self):
        stop = {"state": "STOP", "score": 1470, "makeSorenCount": 0}
        self.assertFalse(is_terminal(stop, state_mtime=100, now=100 + STOP_QUIET_SECONDS - 1))
        self.assertTrue(is_terminal(stop, state_mtime=100, now=100 + STOP_QUIET_SECONDS))
        self.assertFalse(is_terminal(stop, state_mtime=None, now=1000))
        self.assertFalse(is_terminal(stop, state_mtime=100, now=1000, founding_seen=True))
        self.assertFalse(is_terminal({**stop, "makeSorenCount": 1}, state_mtime=100, now=1000))

    def test_runner_ends_quiet_non_founding_stop(self):
        stop = {"state": "STOP", "score": 1470, "makeSorenCount": 0}
        with mock.patch.object(strategy_runner, "load_game_state", return_value=stop), mock.patch.object(
            strategy_runner.os.path, "getmtime", return_value=100.0
        ), mock.patch.object(strategy_runner.time, "time", return_value=131.0):
            state, is_move = strategy_runner.wait_for_move_state(False)
        self.assertIs(state, stop)
        self.assertFalse(is_move)

    def test_outer_loop_recognizes_quiet_non_founding_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "game_state.json"
            state_path.write_text(json.dumps({"state": "STOP", "score": 1470, "makeSorenCount": 0}))
            old = int(time.time()) - STOP_QUIET_SECONDS - 1
            os.utime(state_path, (old, old))
            env = {**os.environ, "GAME_STATE": str(state_path), "TMP_MARKERS_DIR": temp}
            command = "source core/game_state.sh; is_game_over"
            result = subprocess.run(["bash", "-c", command], cwd=REPO_ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            (Path(temp) / ".soviet_created").touch()
            result = subprocess.run(["bash", "-c", command], cwd=REPO_ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 1)

    def test_soviet_success_does_not_enable_automatic_loop_halt(self):
        loop = (REPO_ROOT / "eloop.sh").read_text(encoding="utf-8")
        config = (REPO_ROOT / "core/config.sh").read_text(encoding="utf-8")
        game_state = (REPO_ROOT / "core/game_state.sh").read_text(encoding="utf-8")
        clip_script = (REPO_ROOT / "twitch_clip.sh").read_text(encoding="utf-8")
        clip_queue = (REPO_ROOT / "core/version.sh").read_text(encoding="utf-8")
        clip_worker = (REPO_ROOT / "workers/chat_worker.sh").read_text(encoding="utf-8")
        strategy_runner = (REPO_ROOT / "strategy_runner.py").read_text(encoding="utf-8")
        success_block = loop[loop.index('if [ "$LAST_SOVIET" = "true" ]'):]
        success_block = success_block[: success_block.index('elif [ "$LAST_RUSSIA"')]

        self.assertNotRegex(success_block, r"(?m)^\s*HALT_STRATEGY_AFTER_SOVIET=1")
        self.assertIn("[CONTINUE] ソ連建国達成後も通常のstrategy/retryを継続", success_block)
        self.assertIn('SOVIET_HOLD_SEC="${SOVIET_HOLD_SEC:-0}"', config)
        self.assertIn('hold_sec="${SOVIET_HOLD_SEC:-0}"', game_state)
        self.assertIn('TWITCH_CLIP_POLL_MAX:-20', clip_script)
        self.assertIn('SOVIET_CELEBRATION_BLUESKY_ENABLED="${SOVIET_CELEBRATION_BLUESKY_ENABLED:-1}"', config)
        self.assertIn('event_kind="${4:-generic}"', clip_queue)
        self.assertIn('event_kind', clip_worker)
        self.assertIn('"soviet"', loop[loop.index('_create_twitch_clip "☭ ソ連建国!'):])
        self.assertIn("0 'soviet'", strategy_runner)
        self.assertIn('EVENT_KIND="${2:-generic}"', clip_script)
        self.assertIn('--clip-id "$clip_id"', clip_script)

    def test_runner_keeps_dropping_after_soviet_is_created(self):
        self._check_runner_continuation(0)

    def test_runner_holds_five_minutes_then_reobserves_same_board(self):
        self._check_runner_continuation(300)

    def test_configured_hold_cannot_exceed_five_minutes(self):
        self._check_runner_continuation(900)

    def test_nonterminal_timeout_keeps_same_game_open(self):
        self._check_runner_continuation(0, prelude=[
            ({"state": "STOP", "score": 95, "makeSorenCount": 0}, False),
            (None, False),
        ])

    def test_stop_file_interrupts_celebration_without_drop(self):
        self._check_runner_continuation(300, interrupt=True)

    def _check_runner_continuation(self, pause, prelude=None, interrupt=False):
        states = iter(
            (prelude or []) + [
                ({"state": "MOVE", "score": 100, "makeSorenCount": 1}, True),
                ({"state": "MOVE", "score": 110, "makeSorenCount": 1}, True),
                ({"state": "GAMEOVER", "score": 120, "makeSorenCount": 1}, False),
            ]
        )
        decisions = []
        drops = []
        clock = [0.0]
        drop_times = []

        def advance(seconds):
            clock[0] += seconds
            if interrupt and clock[0] >= 1:
                Path("tmp/stop").touch()
        strategy = types.SimpleNamespace(
            decide=lambda game_state, analysis: decisions.append(game_state) or {"x": 0.5}
        )

        with tempfile.TemporaryDirectory() as raw_dir:
            old_cwd = os.getcwd()
            os.chdir(raw_dir)
            try:
                with mock.patch.object(
                    strategy_runner,
                    "load_strategy_module",
                    return_value=strategy,
                ), mock.patch.object(
                    strategy_runner, "get_strategy_hash", return_value="strategy"
                ), mock.patch.object(
                    strategy_runner, "get_strategy_file_hash", return_value="file"
                ), mock.patch.object(
                    strategy_runner,
                    "strategy_fast_drop_deadline_contact_enabled",
                    return_value=False,
                ), mock.patch.object(
                    strategy_runner,
                    "wait_for_move_state",
                    side_effect=lambda *_args, **_kwargs: next(states),
                ), mock.patch.object(
                    strategy_runner,
                    "build_analysis",
                    return_value={"results": [], "same_type": [], "reactor": {}},
                ), mock.patch.object(
                    strategy_runner, "enrich_game_state_deadline_fields"
                ), mock.patch.object(
                    strategy_runner, "trigger_soviet_clip_now"
                ) as clip, mock.patch.object(
                    strategy_runner, "commands_empty", return_value=True
                ), mock.patch.object(
                    strategy_runner, "wait_commands_done", return_value=True
                ), mock.patch.object(
                    strategy_runner,
                    "write_drop_command",
                    side_effect=lambda x: (drops.append(x), drop_times.append(clock[0])),
                ), mock.patch.dict(os.environ, {"SOVIET_CELEBRATION_PAUSE_SEC": str(pause)}), mock.patch.object(
                    strategy_runner.time, "monotonic", side_effect=lambda: clock[0]
                ), mock.patch.object(strategy_runner.time, "sleep", side_effect=advance):
                    if interrupt:
                        with self.assertRaises(KeyboardInterrupt):
                            strategy_runner.run_game()
                        self.assertEqual(drops, [])
                        self.assertEqual(decisions, [])
                        clip.assert_called_once_with(100, 1)
                        self.assertEqual(clock[0], 1)
                        return
                    result = strategy_runner.run_game()

                self.assertTrue(result["soviet_created"])
                self.assertEqual(result["state"], "GAMEOVER")
                expected_turns = 1 if pause else 2
                self.assertEqual(result["turns"], expected_turns)
                self.assertEqual(len(decisions), expected_turns)
                self.assertEqual(drops, [0.5] * expected_turns)
                clip.assert_called_once_with(100, 1)
                if pause:
                    self.assertEqual(drop_times, [300.0])
                    self.assertEqual(decisions[0]["score"], 110)
                self.assertFalse(Path("commands.txt").exists())

                history = [
                    json.loads(line)
                    for line in Path("game_history/latest.jsonl").read_text().splitlines()
                ]
                self.assertEqual(len(history), expected_turns)
                self.assertTrue(history[0]["soviet_created"])
            finally:
                os.chdir(old_cwd)

    def test_founding_stop_is_observed_until_same_board_returns_to_move(self):
        states = [
            {"state": "STOP", "score": 5839, "makeSorenCount": 0},
            {"state": "STOP", "score": 6111, "makeSorenCount": 1},
            {"state": "MOVE", "score": 6111, "makeSorenCount": 1},
        ]
        observed = []
        with mock.patch.object(strategy_runner, "load_game_state", side_effect=states), mock.patch.object(
            strategy_runner, "is_board_settled", return_value=True
        ), mock.patch.object(strategy_runner, "SETTLE_REQUIRED", 1), mock.patch.object(
            strategy_runner.os.path, "exists", return_value=False
        ), mock.patch.object(strategy_runner.time, "sleep"):
            state, is_move = strategy_runner.wait_for_move_state(False, on_state=observed.append)
        self.assertTrue(is_move)
        self.assertEqual(state, states[-1])
        self.assertEqual(observed, states)


if __name__ == "__main__":
    unittest.main()
