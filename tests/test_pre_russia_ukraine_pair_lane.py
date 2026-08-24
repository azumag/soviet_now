from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import analyze_board
import strategy
import strategy_runner


class PreRussiaUkrainePairLaneTest(unittest.TestCase):
    def test_second_ukraine_birth_lane_stays_reason_gated(self):
        pieces = [
            {"id": "ukraine", "type": 13, "x": 0.0, "y": -2.0, "r": 1.235},
            {
                "id": "turkmenistan-left",
                "type": 12,
                "x": -2.0,
                "y": -2.0,
                "r": 1.068,
            },
            {
                "id": "turkmenistan-right",
                "type": 12,
                "x": 2.0,
                "y": -2.0,
                "r": 1.068,
            },
        ]
        analysis = {
            "deadline": {
                "deadline_y": 3.38,
                "top_edge_y": -1.0,
                "deadline_crossed": False,
                "danger_piece_count": 0,
            },
            "reactor": {"reactive_pairs": []},
            "results": [
                {
                    "x": 3.0,
                    "crosses_deadline": False,
                    "merge_grade": "NO",
                    "risk_top_y_after_drop": 2.5,
                },
                {
                    "x": 0.0,
                    "crosses_deadline": False,
                    "merge_grade": "NO",
                    "risk_top_y_after_drop": 2.6,
                },
            ],
        }
        game_state = {
            "pieces": pieces,
            "next": {"type": 10, "r": 0.846},
        }

        ordinary = strategy_runner.enforce_deadline_safety(
            {"x": 3.0, "reason": "ordinary"},
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
            strategy,
        )
        birth = strategy_runner.enforce_deadline_safety(
            {"x": 3.0, "reason": "ANCHOR_SECOND_T13_CONTACT_SHOT"},
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
            strategy,
        )

        self.assertEqual(ordinary["x"], 3.0)
        self.assertEqual(birth["x"], 0.0)
        self.assertIn("pre_russia_t13_pair_lane", birth["reason"])

    def test_real_turn_61_restores_state_only_ukraine_pair_lane(self):
        fixture_dir = REPO_ROOT / "tests" / "fixtures"
        fixture = json.loads(
            (fixture_dir / "pre_russia_ukraine_pair_turn61.json").read_text(
                encoding="utf-8"
            )
        )
        shape_donor = json.loads(
            (fixture_dir / "pre_russia_chain_cover_turn57.json").read_text(
                encoding="utf-8"
            )
        )
        game_state = {
            "source": fixture["source"],
            "state": "MOVE",
            "score": fixture["score"],
            "next": {
                "type": fixture["next"]["type"],
                "r": analyze_board.TYPE_RADII[fixture["next"]["type"]],
                "x": 0,
            },
            "nextNext": {
                "type": fixture["nextNext"]["type"],
                "r": analyze_board.TYPE_RADII[fixture["nextNext"]["type"]],
            },
            "pieces": fixture["pieces"],
            "shapes": shape_donor["shapes"],
        }
        self.assertIn("rounded history board", game_state["source"])
        self.assertEqual(
            sum(piece["type"] == 13 for piece in game_state["pieces"]),
            2,
            "the replay must contain the two observed Ukraine pieces",
        )

        analysis = strategy_runner.build_analysis(copy.deepcopy(game_state))
        strategy_runner.enrich_game_state_deadline_fields(game_state, analysis)
        initial = strategy.decide(
            copy.deepcopy(game_state), copy.deepcopy(analysis)
        )
        self.assertEqual(initial["x"], -0.15)

        legacy = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(initial),
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
        )
        mismatched = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(initial),
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
            SimpleNamespace(
                pre_russia_ukraine_pair_policy_id=lambda: "wrong-policy"
            ),
        )
        self.assertNotEqual(legacy["x"], -3.0)
        self.assertNotEqual(mismatched["x"], -3.0)
        self.assertNotIn("pre_russia_t13_pair_lane", legacy["reason"])
        self.assertNotIn("pre_russia_t13_pair_lane", mismatched["reason"])

        previous_lane = min(
            analysis["results"], key=lambda result: abs(result["x"] - 3.0)
        )
        restored_lane = min(
            analysis["results"], key=lambda result: abs(result["x"] + 3.0)
        )
        self.assertAlmostEqual(
            previous_lane["risk_top_y_after_drop"],
            fixture["observedDecision"]["riskTopYAfterDrop"],
        )
        self.assertAlmostEqual(
            previous_lane["deadline_margin"],
            fixture["observedDecision"]["deadlineMargin"],
        )
        self.assertAlmostEqual(restored_lane["risk_top_y_after_drop"], 3.028)
        self.assertAlmostEqual(restored_lane["deadline_margin"], 0.352)

        enforced = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(initial),
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
            strategy,
        )
        self.assertEqual(enforced["x"], -3.0)
        self.assertIn("pre_russia_t13_pair_lane", enforced["reason"])

        enforced_twice = strategy_runner.apply_strategy_final_decision(
            strategy,
            copy.deepcopy(enforced),
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
        )
        self.assertEqual(enforced_twice, enforced)

        hard_deadline_analysis = copy.deepcopy(analysis)
        hard_deadline_analysis["deadline"]["top_edge_y"] = 3.33
        hard_deadline_analysis["deadline"]["danger_piece_count"] = 1
        for result in hard_deadline_analysis["results"]:
            if abs(result["x"] + 3.0) <= 0.011:
                result["crosses_deadline"] = True
        hard_deadline = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(initial),
            hard_deadline_analysis,
            copy.deepcopy(game_state),
            strategy,
        )
        self.assertNotEqual(hard_deadline["x"], -3.0)

        guarded_states = {
            "one Ukraine": {
                **copy.deepcopy(game_state),
                "pieces": [
                    piece
                    for piece in copy.deepcopy(game_state["pieces"])
                    if piece["id"] != 80
                ],
            },
            "Kazakhstan present": {
                **copy.deepcopy(game_state),
                "pieces": copy.deepcopy(game_state["pieces"])
                + [
                    {
                        "id": 1000,
                        "type": 14,
                        "x": 0.0,
                        "y": -4.0,
                        "r": 1.385,
                    }
                ],
            },
            "Russia present": {
                **copy.deepcopy(game_state),
                "pieces": copy.deepcopy(game_state["pieces"])
                + [
                    {
                        "id": 1001,
                        "type": 15,
                        "x": 0.0,
                        "y": -4.0,
                        "r": 1.6,
                    }
                ],
            },
        }
        for label, guarded_state in guarded_states.items():
            with self.subTest(label=label):
                guarded = strategy_runner.enforce_deadline_safety(
                    copy.deepcopy(initial),
                    copy.deepcopy(analysis),
                    guarded_state,
                    strategy,
                )
                self.assertNotIn("pre_russia_t13_pair_lane", guarded["reason"])

        missing_lane_analysis = copy.deepcopy(analysis)
        missing_lane_analysis["results"] = [
            result
            for result in missing_lane_analysis["results"]
            if result["x"] > -1.55
        ]
        missing_lane = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(initial),
            missing_lane_analysis,
            copy.deepcopy(game_state),
            strategy,
        )
        self.assertNotIn("pre_russia_t13_pair_lane", missing_lane["reason"])


if __name__ == "__main__":
    unittest.main()
