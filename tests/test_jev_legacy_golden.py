import json
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
            run_dir = Path(directory) / identity["run_id"]
            run_dir.mkdir(parents=True)
            ack = dict(identity, status="outcome_unknown", candidate_id="c12")
            (run_dir / "opportunity_00000007.json").write_text(
                json.dumps(ack), encoding="utf-8"
            )
            with patch.object(runner, "JEV_ACK_ROOT", directory):
                result = runner.wait_jev_drop_ack(identity, "c12", timeout=0.5)
        self.assertEqual(result["status"], "outcome_unknown")


if __name__ == "__main__":
    unittest.main()
