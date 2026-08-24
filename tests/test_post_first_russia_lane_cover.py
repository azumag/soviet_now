from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy


class PostFirstRussiaLaneCoverTest(unittest.TestCase):
    def test_lone_turkmenistan_is_not_covered_after_first_kazakhstan(self):
        pieces = [
            {"id": 10, "type": 10, "x": 2.6, "y": 0.35, "r": 0.95},
            {"id": 11, "type": 11, "x": -1.8, "y": 0.5, "r": 1.6},
            {"id": 12, "type": 12, "x": -0.1, "y": -0.6, "r": 1.2},
            {"id": 14, "type": 14, "x": 1.9, "y": -2.0, "r": 1.2},
        ]
        results = [
            {
                "x": -0.09,
                "merge_grade": "NO",
                "crosses_deadline": False,
                "merge_result_crosses_deadline": False,
                "risk_top_y_after_drop": 2.713,
                "deadline_margin": 0.667,
                "landing_hit_id": 12,
                "top_y_after_drop": 2.713,
            },
            {
                "x": 2.6,
                "merge_grade": "NO",
                "crosses_deadline": False,
                "merge_result_crosses_deadline": False,
                "risk_top_y_after_drop": 2.306,
                "deadline_margin": 1.074,
                "landing_hit_id": 10,
                "top_y_after_drop": 2.117,
            },
        ]
        decision = {"x": -0.09, "reason": "safe enforced drop"}

        finalized = strategy.finalize_decision(
            copy.deepcopy({"pieces": pieces, "next": {"type": 8}}),
            copy.deepcopy({"results": results}),
            copy.deepcopy(decision),
        )

        self.assertEqual(finalized["x"], 2.6)
        self.assertEqual(finalized["reason"], "POST_FIRST_RUSSIA_LANE_COVER_AVOID")

    def test_ordinary_or_unsafe_states_keep_the_enforced_decision(self):
        pieces = [
            {"id": 10, "type": 10, "x": 2.6, "y": 0.35, "r": 0.95},
            {"id": 12, "type": 12, "x": -0.1, "y": -0.6, "r": 1.2},
            {"id": 14, "type": 14, "x": 1.9, "y": -2.0, "r": 1.2},
        ]
        selected = {
            "x": -0.09,
            "merge_grade": "NO",
            "crosses_deadline": False,
            "merge_result_crosses_deadline": False,
            "risk_top_y_after_drop": 2.31,
            "deadline_margin": 0.81,
            "landing_hit_id": 12,
            "top_y_after_drop": 2.31,
        }
        alternative = dict(selected, x=2.6, landing_hit_id=10)

        # A materially worse alternative must not displace the enforced cover.
        no_better_risk = strategy.finalize_decision(
            {"pieces": pieces, "next": {"type": 8}},
            {
                "results": [
                    selected,
                    dict(
                        alternative,
                        risk_top_y_after_drop=selected["risk_top_y_after_drop"] + 0.06,
                        deadline_margin=selected["deadline_margin"] - 0.01,
                    ),
                ]
            },
            {"x": -0.09},
        )
        for decision in (no_better_risk,):
            with self.subTest(decision=decision):
                self.assertNotIn("POST_FIRST_RUSSIA_LANE_COVER_AVOID", decision.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
