"""soviet_now#877 Phase 1: runner records both reason string and tags.

The legacy golden test stays untouched; this characterization test pins the new
history field and the fact that a runtime-appended reason suffix cannot leave
stale tags on the record.
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy_runner as runner  # noqa: E402
from lib import reason_tags  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "jev_legacy_runner_contract.json"


class ReasonTagsRecordTests(unittest.TestCase):
    def _record(self, decision, analysis, game_state):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history.jsonl"
            with history.open("w", encoding="utf-8") as stream:
                runner.record_turn(
                    stream,
                    7,
                    game_state,
                    decision,
                    analysis,
                    strategy_hash="reason-tags-fixture-hash",
                    score_delta=0,
                )
            return json.loads(history.read_text(encoding="utf-8"))

    def test_record_keeps_string_and_structured_form(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        game_state = fixture["game_state"]
        analysis = runner.build_analysis(game_state)
        reason = (
            "NO_MERGE_DEADLINE_GUARD_NO_VALID"
            "|OPEN_TWIN_MERGE_DESPERATE"
        )
        record = self._record({"x": 0.0, "reason": reason}, analysis, game_state)

        self.assertEqual(record["decision_reason"], reason)
        self.assertEqual(record["reason_tags"], reason_tags.parse_reason(reason))
        self.assertEqual(
            reason_tags.render_reason(record["reason_tags"]),
            record["decision_reason"],
        )

    def test_stale_tags_are_reparsed_after_runtime_suffix(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        game_state = fixture["game_state"]
        analysis = runner.build_analysis(game_state)
        old_reason = "HEIGHT_CONTROL"
        final_reason = (
            "HEIGHT_CONTROL_RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NEAR_"
            "runtime_minrisk"
        )
        decision = {
            "x": 0.2,
            "reason": final_reason,
            # decide() attached tags before enforce_deadline_safety appended.
            "reason_tags": reason_tags.parse_reason(old_reason),
        }
        record = self._record(decision, analysis, game_state)

        self.assertEqual(record["decision_reason"], final_reason)
        self.assertTrue(
            reason_tags.has_marker(record["reason_tags"], "RUNTIME_DEADLINE_SAFETY_OVERRIDE")
        )
        self.assertEqual(
            reason_tags.render_reason(record["reason_tags"]),
            final_reason,
        )

    def test_decide_attaches_tags(self):
        import strategy

        decision = strategy.decide(
            {
                "state": "MOVE",
                "score": 0,
                "pieces": [],
                "next": {"type": 1, "r": 0.3},
                "nextNext": {"type": 2, "r": 0.3},
            },
            {"results": [], "same_type": [], "reactor": {}, "deadline": {}},
        )
        self.assertIn("reason_tags", decision)
        self.assertEqual(
            reason_tags.render_reason(decision["reason_tags"]),
            decision["reason"],
        )


if __name__ == "__main__":
    unittest.main()
