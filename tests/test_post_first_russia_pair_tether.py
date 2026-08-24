from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy


class PostFirstRussiaPairTetherTest(unittest.TestCase):
    def test_moves_separated_kazakhstan_and_ukraine_together(self):
        pieces = [
            {"id": 1, "type": 14, "x": 1.3, "y": -0.7, "r": 1.7},
            {"id": 2, "type": 13, "x": -0.9, "y": 0.25, "r": 1.5},
        ]
        results = [
            {
                "x": -2.2,
                "merge_grade": "NO",
                "crosses_deadline": False,
                "merge_result_crosses_deadline": False,
                "risk_top_y_after_drop": 2.0,
                "deadline_margin": 1.2,
            },
            {
                "x": 0.0,
                "merge_grade": "NO",
                "crosses_deadline": False,
                "merge_result_crosses_deadline": False,
                "risk_top_y_after_drop": 2.0,
                "deadline_margin": 1.2,
            },
        ]
        decision = strategy.finalize_decision(
            {"pieces": pieces, "next": {"type": 9}},
            {"results": results},
            {"x": -2.2},
        )
        self.assertEqual(decision["x"], 0.0)
        self.assertEqual(decision["reason"], "POST_FIRST_RUSSIA_PAIR_TETHER")


if __name__ == "__main__":
    unittest.main()
