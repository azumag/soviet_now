from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy_runner


class SovietContinuationTest(unittest.TestCase):
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
        states = iter(
            [
                ({"state": "MOVE", "score": 100, "makeSorenCount": 1}, True),
                ({"state": "MOVE", "score": 110, "makeSorenCount": 1}, True),
                ({"state": "GAMEOVER", "score": 120, "makeSorenCount": 1}, False),
            ]
        )
        decisions = []
        drops = []
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
                ), mock.patch.object(
                    strategy_runner, "commands_empty", return_value=True
                ), mock.patch.object(
                    strategy_runner, "wait_commands_done", return_value=True
                ), mock.patch.object(
                    strategy_runner,
                    "write_drop_command",
                    side_effect=lambda x: drops.append(x),
                ), mock.patch.object(strategy_runner.time, "sleep"):
                    result = strategy_runner.run_game()

                self.assertTrue(result["soviet_created"])
                self.assertEqual(result["state"], "GAMEOVER")
                self.assertEqual(result["turns"], 2)
                self.assertEqual(len(decisions), 2)
                self.assertEqual(drops, [0.5, 0.5])

                history = [
                    json.loads(line)
                    for line in Path("game_history/latest.jsonl").read_text().splitlines()
                ]
                self.assertEqual(len(history), 2)
                self.assertTrue(history[0]["soviet_created"])
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
