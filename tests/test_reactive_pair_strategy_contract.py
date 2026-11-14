"""Contract test for wiring the reactive-pair helper into strategy.py."""

import unittest
from pathlib import Path


class ReactivePairStrategyContractTests(unittest.TestCase):
    def test_strategy_uses_extracted_helper(self):
        source = (Path(__file__).resolve().parents[1] / "strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from strategy_helpers import reactive_pair", source)
        self.assertIn("reactive_pair.blocking_penalty(", source)
        self.assertNotIn("len(rp) >= 6", source)
        self.assertNotIn("blocking_penalty = -0.0798", source)


if __name__ == "__main__":
    unittest.main()
