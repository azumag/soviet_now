import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy_runner as runner  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "jev_legacy_runner_contract.json"


class LegacyRunnerGoldenTests(unittest.TestCase):
    def test_existing_decision_safety_and_bookkeeping_shape(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        game_state = fixture["game_state"]
        analysis = runner.build_analysis(game_state)

        # A strategy without Jev hooks follows the existing safety/finalizer
        # path.  The contract work must not silently insert player metadata or
        # alter this path before the later runner integration PR.
        strategy = types.SimpleNamespace()
        decision = runner.enforce_deadline_safety(
            dict(fixture["decision"]), analysis, game_state, strategy
        )
        decision = runner.apply_strategy_final_decision(
            strategy, decision, analysis, game_state
        )
        self.assertEqual(decision["x"], fixture["expected"]["decision_x"])
        self.assertEqual(decision["reason"], fixture["expected"]["decision_reason"])

        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "legacy.jsonl"
            with history.open("w", encoding="utf-8") as stream:
                runner.record_turn(
                    stream,
                    fixture["expected"]["turn"],
                    game_state,
                    decision,
                    analysis,
                    strategy_hash="legacy-fixture-hash",
                    score_delta=fixture["expected"]["score_delta"],
                )
            record = json.loads(history.read_text(encoding="utf-8"))

        for field, expected in fixture["expected"].items():
            self.assertEqual(record[field], expected, field)
        self.assertNotIn("player_policy", record)
        self.assertNotIn("run_id", record)

    def test_existing_run_dispatches_post_finalizer_x(self):
        """The command/history x must be the decision after the finalizer."""
        strategy = types.SimpleNamespace(
            decide=lambda _gs, _analysis: {"x": -1.0, "reason": "initial"}
        )
        move = {
            "state": "MOVE",
            "score": 0,
            "pieces": [],
            "makeSorenCount": 0,
            "next": {"type": 1},
            "nextNext": {"type": 2},
        }
        gameover = {
            "state": "GAMEOVER",
            "score": 0,
            "pieces": [],
            "makeSorenCount": 0,
        }
        dispatched = []
        recorded = []

        def capture_record(_stream, _turn, _gs, decision, _analysis, **_kwargs):
            recorded.append(dict(decision))

        def finalize(_strategy, decision, _analysis, _gs):
            return dict(decision, x=1.0, reason="final")

        with tempfile.TemporaryDirectory() as directory:
            history = str(Path(directory) / "latest.jsonl")
            with (
                patch.dict(os.environ, {"SOREN_PLAYER_POLICY": "existing"}, clear=False),
                patch.object(runner, "HISTORY_DIR", directory),
                patch.object(runner, "HISTORY_FILE", history),
                patch.object(runner, "STOP_FILE", str(Path(directory) / "stop")),
                patch.object(runner, "load_strategy_module", return_value=strategy),
                patch.object(runner, "get_strategy_hash", return_value="strategy-hash"),
                patch.object(runner, "get_strategy_file_hash", return_value="file-hash"),
                patch.object(runner, "strategy_fast_drop_deadline_contact_enabled", return_value=False),
                patch.object(
                    runner,
                    "wait_for_move_state",
                    side_effect=[(move, True), (gameover, False)],
                ),
                patch.object(runner, "has_deadline_contact", return_value=False),
                patch.object(
                    runner,
                    "build_analysis",
                    return_value={"results": [], "same_type": [], "reactor": {}, "deadline": {}},
                ),
                patch.object(runner, "enforce_deadline_safety", side_effect=lambda decision, *_args: decision),
                patch.object(runner, "apply_strategy_final_decision", side_effect=finalize),
                patch.object(runner, "record_turn", side_effect=capture_record),
                patch.object(runner, "commands_empty", return_value=True),
                patch.object(runner, "write_drop_command", side_effect=dispatched.append),
                patch.object(runner, "wait_commands_done", return_value=True),
                patch.object(runner.time, "sleep", return_value=None),
            ):
                result = runner.run_game()

        self.assertEqual(result["turns"], 1)
        self.assertEqual(dispatched, [1.0])
        self.assertEqual(recorded[0]["x"], 1.0)
        self.assertEqual(recorded[0]["reason"], "final")

    def test_outcome_unknown_ack_is_terminal_and_never_waits_for_timeout(self):
        # The bridge writes `outcome_unknown` when it dispatched a drop but
        # could not observe the state transition.  That is a terminal
        # operational outcome, not a missing ack, so the runner must see it
        # immediately instead of burning the whole ack timeout.
        identity = {
            "run_id": "11111111-1111-4111-8111-111111111111",
            "game_instance_id": "22222222-2222-4222-8222-222222222222",
            "game_generation": 18,
            "player_generation": 3,
            "opportunity_seq": 7,
            "frame_seq": 120,
            "observed_at": "2026-09-20T04:00:00Z",
            "drop_piece_id": 17,
            "board_bounds": {"drop_x_min": -3.0, "drop_x_max": 3.0},
        }
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / identity["run_id"] / identity["game_instance_id"]
            run_dir.mkdir(parents=True)
            ack = dict(identity, status="outcome_unknown", candidate_id="c12")
            (run_dir / "opportunity_00000007.json").write_text(
                json.dumps(ack), encoding="utf-8"
            )
            with patch.object(runner, "JEV_ACK_ROOT", directory):
                result = runner.wait_jev_drop_ack(identity, "c12", timeout=0.5)
        self.assertEqual(result["status"], "outcome_unknown")

    def test_jev_ack_path_is_scoped_by_game_instance(self):
        # opportunity_seq restarts per game, so a second game in the same run
        # must not reuse the first game's ack path.
        base = {
            "run_id": "11111111-1111-4111-8111-111111111111",
            "game_generation": 1,
            "player_generation": 13,
            "opportunity_seq": 1,
        }
        first = dict(base, game_instance_id="22222222-2222-4222-8222-222222222222")
        second = dict(base, game_instance_id="33333333-3333-4333-8333-333333333333")
        self.assertNotEqual(runner._jev_ack_path(first), runner._jev_ack_path(second))
        self.assertEqual(runner._jev_ack_path(first), runner._jev_ack_path(dict(first)))


if __name__ == "__main__":
    unittest.main()
