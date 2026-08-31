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


class AbDecideTest(unittest.TestCase):
    def test_continue_below_min_blocks(self):
        v = dec.decide(_rows(0, 5))
        self.assertEqual(v["verdict"], "CONTINUE")

    def test_harm_stops_early(self):
        ks = []
        for s in range(10):
            t = dec.trail(_rows(-500, 12, seed=s))
            first = next((k for (i, k, verdict, m, u) in t if verdict == "REJECT_HARM"), None)
            ks.append(first)
        self.assertTrue(all(k is not None and k <= 9 for k in ks), ks)

    def test_null_never_adopts_and_stops_by_futility_or_final(self):
        verdicts = []
        for s in range(60):
            v = dec.decide(_rows(0, 37, seed=s))
            verdicts.append(v["verdict"])
        self.assertNotIn("ADOPT", verdicts)
        self.assertTrue(all(v in ("REJECT_FUTILE", "REJECT_INCONCLUSIVE", "REJECT_HARM") for v in verdicts), set(verdicts))

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

    def test_instadeath_asymmetry_aborts(self):
        rows = _rows(0, 6, dead_b=6)
        v = dec.decide(rows)
        self.assertEqual(v["verdict"], "ABORT", v)

    def test_guardrail_vetoes_adopt(self):
        rows = _rows(600, 19, seed=1)
        for r in rows:
            if r["arm"] == "A":
                r["t15"] = 1
        v = dec.decide(rows)
        self.assertNotEqual(v["verdict"], "ADOPT")
        self.assertTrue(any("guardrail" in x for x in v["reasons"]))

    def test_replay_v738_history_rejects_harm_early(self):
        path = os.path.join(ROOT, "tests", "fixtures", "ab_history_v738_games.jsonl")
        rows = rep.load_games(path)
        self.assertEqual(len(rows), 60)
        t = dec.trail(rows)
        first = next((i for (i, k, verdict, m, u) in t if verdict == "REJECT_HARM"), None)
        self.assertIsNotNone(first)
        self.assertLessEqual(first, 30, t[:30])
        final = dec.decide(rows)
        self.assertIn(final["verdict"], ("REJECT_HARM", "REJECT_FUTILE", "REJECT_INCONCLUSIVE"))


if __name__ == "__main__":
    unittest.main()
