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



def _final_rows_with_t15(t15_a, t15_b, diff=300):
    """37 ブロック (各腕 74 試合) の最終判定。B が eval で上回り、T15 到達数だけを指定する。"""
    rows = _rows_from_block_diffs([diff] * 37)
    a_rows = [r for r in rows if r["arm"] == "A"]
    b_rows = [r for r in rows if r["arm"] == "B"]
    for i, r in enumerate(a_rows):
        r["t15"] = 1 if i < t15_a else 0
    for i, r in enumerate(b_rows):
        r["t15"] = 1 if i < t15_b else 0
    return rows


def _rule(version, **extra):
    rule = {"version": version, "looks": [19, 37], "max_blocks": 37}
    rule.update(extra)
    return {"pattern": "ABBA", "primary": "eval", "decision_rule_version": version, "decision_rule": rule}


class T15GuardrailTest(unittest.TestCase):
    """T15 到達ガードは rule v3 から片側 Fisher。v2 の state は件数規則のまま。"""

    def test_v2_state_keeps_count_rule(self):
        # v770 1fb96e38ea8f の実例: eval で上回ったが T15 3 対 5 で棄却された。
        cfg = dec.config_from_state(_rule(2))
        v = dec.decide(_final_rows_with_t15(5, 3), cfg)
        self.assertEqual(v["verdict"], "REJECT_INCONCLUSIVE", v)
        self.assertIn("T15 reach B=3 < A=5-1", " ".join(v["reasons"]))

    def test_v3_state_does_not_reject_on_noise(self):
        cfg = dec.config_from_state(_rule(3, t15_guard_alpha=0.05))
        self.assertAlmostEqual(cfg["t15_guard_alpha"], 0.05)
        # 差 +100 は MDE 未満 (有意ではない) なので、通れば最終判定の暫定採用になる。
        v = dec.decide(_final_rows_with_t15(5, 3, diff=100), cfg)
        self.assertEqual(v["verdict"], "ADOPT", v)
        self.assertEqual(v["decision_class"], "provisional_win", v)

    def test_v3_state_still_rejects_a_clear_t15_loss(self):
        cfg = dec.config_from_state(_rule(3, t15_guard_alpha=0.05))
        v = dec.decide(_final_rows_with_t15(12, 2), cfg)
        self.assertEqual(v["verdict"], "REJECT_INCONCLUSIVE", v)
        self.assertIn("Fisher", " ".join(v["reasons"]))

    def test_v3_false_reject_rate_under_equal_rates_is_at_most_alpha(self):
        # 到達率が同じ腕どうしで、ガードが採用を止める確率 (各腕 74 試合、二項分布で厳密計算)。
        from math import comb
        n = 74
        a_rows = [{} for _ in range(n)]
        b_rows = [{} for _ in range(n)]
        blocked = {}
        for ta in range(0, 21):
            for tb in range(0, 21):
                for i, r in enumerate(a_rows):
                    r["t15"] = 1 if i < ta else 0
                for i, r in enumerate(b_rows):
                    r["t15"] = 1 if i < tb else 0
                blocked[(ta, tb)] = (
                    not dec._guardrails(a_rows, b_rows, 0.05)[0],
                    not dec._guardrails(a_rows, b_rows, None)[0],
                )
        for rate in (0.04, 0.054, 0.068):
            pmf = [comb(n, k) * rate ** k * (1 - rate) ** (n - k) for k in range(21)]
            new = sum(pmf[a] * pmf[b] for (a, b), (nb, _) in blocked.items() if nb)
            old = sum(pmf[a] * pmf[b] for (a, b), (_, ob) in blocked.items() if ob)
            self.assertLessEqual(new, 0.05, (rate, new))
            self.assertGreater(old, 0.25, (rate, old))

    def test_default_without_alpha_is_count_rule(self):
        self.assertIsNone(dec.DEFAULTS["t15_guard_alpha"])
        self.assertFalse(dec._guardrails([{"t15": 1}] * 5 + [{}] * 69, [{"t15": 1}] * 3 + [{}] * 71)[0])


if __name__ == "__main__":
    unittest.main()
