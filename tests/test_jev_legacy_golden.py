import json
from pathlib import Path
import sys
import tempfile
import types
import unittest


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


if __name__ == "__main__":
    unittest.main()
