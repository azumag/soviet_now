"""tools/ab_decide.py: 逐次判定ルール (害の早期停止 / 無益停止 / 採用 / 結論なし / abort) と v738 A/B の再生。"""
import json
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import ab_decide as dec  # noqa: E402
import ab_report as rep  # noqa: E402


def _rows(effect, n_blocks, seed=0, pattern="ABBA", sd_block=200, sd_game=100, tainted_idx=(), dead_b=0):
    rng = random.Random(seed)
    rows = []
    idx = 0
    b_seen = 0
    for k in range(n_blocks):
        base = rng.gauss(1600, sd_block)
        for ch in pattern:
            v = base + rng.gauss(0, sd_game) + (effect if ch == "B" else 0)
            if ch == "B" and b_seen < dead_b:
                v = 100.0
                b_seen += 1
            rows.append({"idx": idx, "arm": ch, "score": v, "eval": v + 5000, "turns": 90, "tainted": idx in tainted_idx})
            idx += 1
    return rows


def _constant_rows(effect, n_blocks, pattern="ABBA"):
    rows = []
    idx = 0
    for _ in range(n_blocks):
        for ch in pattern:
            v = 1600.0 + (effect if ch == "B" else 0.0)
            rows.append({"idx": idx, "arm": ch, "score": v, "eval": v + 5000, "turns": 90, "tainted": False})
            idx += 1
    return rows


class AbDecideTest(unittest.TestCase):
    def test_continue_below_min_blocks(self):
        v = dec.decide(_rows(0, 5))
        self.assertEqual(v["verdict"], "CONTINUE")

    def test_calibrated_harm_stops_from_k10(self):
        ks = []
        for s in range(10):
            t = dec.trail(_rows(-500, 12, seed=s))
            first = next((k for (i, k, verdict, m, u) in t if verdict == "REJECT_HARM"), None)
            ks.append(first)
        self.assertTrue(all(k is not None and 10 <= k <= 12 for k in ks), ks)

    def test_exact_tie_finishes_neutral(self):
        v = dec.decide(_constant_rows(0, 37))
        self.assertEqual(v["verdict"], "REJECT_INCONCLUSIVE", v)
        self.assertEqual(v["decision_class"], "neutral", v)

    def test_small_positive_effect_is_not_futile_and_adopts_provisionally(self):
        rows = _constant_rows(1, 37)
        v12 = dec.decide(rows[: 12 * 4])
        self.assertEqual(v12["k"], 12)
        self.assertEqual(v12["verdict"], "CONTINUE", v12)
        self.assertGreater(v12["mean_diff"], 0)
        self.assertEqual(v12["futility_threshold"], 0.0)

        final = dec.decide(rows)
        self.assertEqual(final["verdict"], "ADOPT", final)
        self.assertEqual(final["decision_class"], "provisional_win", final)
        self.assertGreater(final["mean_diff"], 0)
        self.assertTrue(final["p"] is None or final["p"] >= final["alpha_look"] or final["mean_diff"] < final["mde"])

    def test_small_negative_effect_is_not_adopted(self):
        v = dec.decide(_constant_rows(-1, 37))
        self.assertNotEqual(v["verdict"], "ADOPT", v)
        self.assertIn(v["verdict"], ("REJECT_HARM", "REJECT_FUTILE", "REJECT_INCONCLUSIVE"), v)
        self.assertEqual(v["decision_class"], "loss", v)

    def test_large_effect_adopts_at_a_look(self):
        adopted = 0
        for s in range(20):
            t = dec.trail(_rows(600, 37, seed=s))
            if any(verdict == "ADOPT" for (_, _, verdict, _, _) in t):
                adopted += 1
        self.assertGreaterEqual(adopted, 16)

    def test_adopt_only_at_looks_with_enough_n(self):
        rows = _rows(600, 19, seed=1)
        v = dec.decide(rows)
        self.assertEqual(v["k"], 19)
        self.assertEqual(v["n_a"], 38)
        self.assertEqual(v["verdict"], "ADOPT", v)
        self.assertEqual(v["decision_class"], "significant_win", v)
        v18 = dec.decide(rows[:-4])
        self.assertEqual(v18["k"], 18)
        self.assertNotEqual(v18["verdict"], "ADOPT")

    def test_tainted_excluded_and_abort(self):
        rows = _rows(600, 10, tainted_idx=(1, 5, 9))
        v = dec.decide(rows)
        self.assertEqual(v["verdict"], "ABORT")
        rows = _rows(600, 10, tainted_idx=(1,))
        v = dec.decide(rows)
        self.assertEqual(v["k"], 9)

    def test_recorded_primary_selects_the_metric(self):
        """primary=eval と primary=score で別の指標を読む (表示と同じ定義)。"""
        rows = []
        idx = 0
        for _ in range(19):
            for ch in "ABBA":
                rows.append({
                    "idx": idx, "arm": ch,
                    "score": 1600.0,
                    "eval": 1600.0 + (600.0 if ch == "B" else 0.0),
                    "turns": 90, "tainted": False,
                })
                idx += 1
        v_eval = dec.decide(rows, {"primary": "eval", "sd": 100})
        v_score = dec.decide(rows, {"primary": "score", "sd": 100})
        self.assertEqual(v_eval["primary"], "eval")
        self.assertEqual(v_score["primary"], "score")
        self.assertGreater(v_eval["mean_diff"], v_score["mean_diff"])
        self.assertAlmostEqual(v_score["mean_diff"], 0.0)
        self.assertAlmostEqual(rep.state_primary_sd({"primary": "eval"}, "eval"), 3700.0)
        self.assertAlmostEqual(rep.state_primary_sd({}, "score"), 650.0)

    def test_instadeath_asymmetry_aborts(self):
        rows = _rows(0, 6, dead_b=6)
        v = dec.decide(rows)
        self.assertEqual(v["verdict"], "ABORT", v)

    def test_primary_missing_rows_do_not_inflate_n_or_change_decision(self):
        """#288 レビュー: 正準指標 (_primary) が欠測した行は n_a/n_b/n_min に数えない。"""
        rows = []
        idx = 0
        for _ in range(19):
            for ch in "ABBA":
                rows.append({
                    "idx": idx, "arm": ch, "score": 1600.0,
                    "eval": 1600.0 + (600.0 if ch == "B" else 0.0),
                    "turns": 90, "tainted": False,
                })
                idx += 1
        baseline = dec.decide(list(rows), {"primary": "eval", "sd": 100})
        self.assertEqual(baseline["n_a"], 38)
        self.assertEqual(baseline["verdict"], "ADOPT", baseline)
        padding = [
            {"idx": 10_000 + i, "arm": "A" if i % 2 == 0 else "B", "score": 1600.0, "turns": 90, "tainted": False}
            for i in range(80)
        ]
        v = dec.decide(rows + padding, {"primary": "eval", "sd": 100})
        self.assertEqual(v["n_a"], 38, "eval 欠測行を n_a に数えてはいけない")
        self.assertEqual(v["n_b"], 38, "eval 欠測行を n_b に数えてはいけない")
        self.assertEqual(v["k"], baseline["k"])
        self.assertEqual(v["mean_diff"], baseline["mean_diff"])
        self.assertEqual(v["verdict"], baseline["verdict"])

    def test_instadeath_still_uses_full_population_when_primary_missing(self):
        """即死判定の分母は正準指標の欠測とは独立した非 tainted 母集団を使う。"""
        rows = []
        idx = 0
        for k in range(6):
            for ch in "ABBA":
                v = 1600.0
                if ch == "B":
                    v = 100.0
                rows.append({"idx": idx, "arm": ch, "score": v, "eval": v + 5000, "turns": 90, "tainted": False})
                idx += 1
        padding = [
            {"idx": 10_000 + i, "arm": "A" if i % 2 == 0 else "B", "score": 1600.0, "turns": 90, "tainted": False}
            for i in range(20)
        ]
        v = dec.decide(rows + padding, {"primary": "eval", "sd": 100})
        self.assertEqual(v["verdict"], "ABORT", v)

    def test_guardrail_vetoes_adopt(self):
        rows = _rows(600, 19, seed=1)
        for r in rows:
            if r["arm"] == "A":
                r["t15"] = 1
        v = dec.decide(rows)
        self.assertNotEqual(v["verdict"], "ADOPT")
        self.assertTrue(any("guardrail" in x for x in v["reasons"]))

    def test_guardrail_vetoes_provisional_adopt(self):
        rows = _constant_rows(1, 37)
        for r in rows:
            if r["arm"] == "A":
                r["t15"] = 1
        v = dec.decide(rows)
        self.assertEqual(v["verdict"], "REJECT_INCONCLUSIVE", v)
        self.assertNotEqual(v["decision_class"], "provisional_win", v)
        self.assertTrue(any("guardrail" in x for x in v["reasons"]), v)

    def test_replay_v738_history_rejects_harm_early_under_frozen_legacy_rule(self):
        path = os.path.join(ROOT, "tests", "fixtures", "ab_history_v738_games.jsonl")
        rows = rep.load_games(path)
        self.assertEqual(len(rows), 60)
        legacy = {"harm_min_blocks": 6, "harm_z": dec.Z90}
        t = dec.trail(rows, legacy)
        first = next((i for (i, k, verdict, m, u) in t if verdict == "REJECT_HARM"), None)
        self.assertIsNotNone(first)
        self.assertLessEqual(first, 30, t[:30])
        final = dec.decide(rows, legacy)
        self.assertIn(final["verdict"], ("REJECT_HARM", "REJECT_FUTILE", "REJECT_INCONCLUSIVE"))


if __name__ == "__main__":
    unittest.main()