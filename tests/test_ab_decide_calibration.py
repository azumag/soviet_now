"""Automatic A/B gate calibration and frozen-rule compatibility."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import ab_decide as dec  # noqa: E402


def _rows_from_block_diffs(diffs, primary="eval"):
    rows = []
    idx = 0
    for diff in diffs:
        for arm in "ABBA":
            value = 5000.0 + (float(diff) if arm == "B" else 0.0)
            row = {
                "idx": idx,
                "arm": arm,
                "score": value,
                "eval": value,
                "turns": 90,
                "tainted": False,
            }
            rows.append(row)
            idx += 1
    return rows


class CalibratedHarmRuleTest(unittest.TestCase):
    def test_default_uses_calibrated_harm_rule(self):
        self.assertEqual(dec.DEFAULTS["harm_min_blocks"], 10)
        self.assertAlmostEqual(dec.DEFAULTS["harm_z"], 2.3263, places=4)
        self.assertAlmostEqual(dec.DEFAULTS["futility_z"], 1.2816, places=4)

    def test_candidate_old_rule_would_kill_at_k6_but_calibrated_rule_continues(self):
        rows = _rows_from_block_diffs([-500, -500, -500, -500, -500, 500])
        old = dec.decide(rows, {
            "harm_min_blocks": 6,
            "harm_z": 1.2816,
            "futility_k": 99,
        })
        new = dec.decide(rows, {
            "harm_min_blocks": 10,
            "harm_z": 2.3263,
            "futility_k": 99,
        })
        self.assertEqual(old["verdict"], "REJECT_HARM", old)
        self.assertEqual(new["verdict"], "CONTINUE", new)
        self.assertLess(old["harm_ucb"], 0)
        self.assertGreater(new["harm_ucb"], 0)

    def test_legacy_state_keeps_old_rule(self):
        cfg = dec.config_from_state({"pattern": "ABBA", "primary": "eval"})
        self.assertEqual(cfg["harm_min_blocks"], 6)
        self.assertAlmostEqual(cfg["harm_z"], 1.2816, places=4)
        self.assertTrue(cfg["legacy_rule"])

    def test_versioned_state_uses_frozen_rule_not_new_defaults(self):
        state = {
            "pattern": "ABBA",
            "primary": "eval",
            "decision_rule_version": 2,
            "decision_rule": {
                "version": 2,
                "looks": [11, 23],
                "max_blocks": 23,
                "harm_min_blocks": 12,
                "harm_z": 2.5,
                "futility_k": 14,
                "futility_z": 1.1,
                "futility_delta": 123.0,
            },
        }
        cfg = dec.config_from_state(state)
        self.assertFalse(cfg["legacy_rule"])
        self.assertEqual(tuple(cfg["looks"]), (11, 23))
        self.assertEqual(cfg["max_blocks"], 23)
        self.assertEqual(cfg["harm_min_blocks"], 12)
        self.assertAlmostEqual(cfg["harm_z"], 2.5)
        self.assertEqual(cfg["futility_k"], 14)
        self.assertAlmostEqual(cfg["futility_z"], 1.1)
        self.assertAlmostEqual(cfg["futility_delta"], 123.0)

    def test_harm_and_futility_bounds_are_independent(self):
        rows = _rows_from_block_diffs([-500] * 10 + [500, 500])
        v = dec.decide(rows, {
            "harm_min_blocks": 10,
            "harm_z": 2.3263,
            "futility_k": 12,
            "futility_z": 1.2816,
            "futility_delta": 1000.0,
        })
        self.assertIn("harm_ucb", v)
        self.assertIn("futility_ucb", v)
        self.assertNotEqual(v["harm_ucb"], v["futility_ucb"])


if __name__ == "__main__":
    unittest.main()
