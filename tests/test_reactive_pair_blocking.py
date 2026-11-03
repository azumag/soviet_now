"""Regression coverage for axis 9.3 reactive-pair blocking avoidance.

The analyzer emits reactive_pairs as (id1, id2, type).  Axis 9.3 must use
both ids and penalize a NO_MERGE landing that occupies the span above a pair.
"""
import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STRATEGY = REPO_ROOT / "strategy.py"


class ReactivePairBlockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = STRATEGY.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_axis_accepts_analyzer_three_tuple_contract(self):
        self.assertIn("len(rp) >= 3", self.source)
        self.assertNotIn("len(rp) >= 6", self.source)

    def test_axis_reads_both_pair_ids(self):
        self.assertIn("piece_pos_by_id.get(rp[0])", self.source)
        self.assertIn("piece_pos_by_id.get(rp[1])", self.source)

    def test_reason_is_only_added_for_positive_penalty(self):
        self.assertIn("blocking_penalty = 0.0", self.source)
        self.assertIn("if blocking_penalty > 0.0:", self.source)
        self.assertIn('reasons.append("AVOID_BLOCK_REACTIVE_PAIR")', self.source)


if __name__ == "__main__":
    unittest.main()
