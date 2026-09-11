"""tools/ab_report.py: ブロック差・並べ替え p 値・必要 n・tainted/不完全ブロックの除外。"""
import json
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ab_report as ab  # noqa: E402


def _rows(effect, n_blocks, pattern="ABBA", seed=0, tainted_idx=()):
    rng = random.Random(seed)
    rows = []
    idx = 0
    for k in range(n_blocks):
        base = rng.gauss(1600, 200)  # ブロック共通の漂流
        for ch in pattern:
            v = base + rng.gauss(0, 100) + (effect if ch == "B" else 0)
            rows.append({"idx": idx, "arm": ch, "eval": v, "score": v - 50, "turns": 90, "tainted": idx in tainted_idx, "archive": ""})
            idx += 1
    return rows


class AbReportTest(unittest.TestCase):
    def test_blocks_recover_known_effect(self):
        rows = _rows(300, 12)
        d = ab.blocks(rows, "ABBA")
        self.assertEqual(len(d), 12)
        self.assertAlmostEqual(sum(d) / len(d), 300, delta=120)
        p = ab.sign_flip_p(d)
        self.assertLess(p, 0.05)

    def test_null_effect_not_significant(self):
        ps = [ab.sign_flip_p(ab.blocks(_rows(0, 10, seed=s), "ABBA"), draws=2000, seed=s) for s in range(6)]
        self.assertGreater(sum(1 for p in ps if p > 0.05), 3)

    def test_tainted_and_incomplete_blocks_excluded(self):
        rows = _rows(0, 5, tainted_idx=(1,))
        rows = rows[:-1]  # 最後のブロックを欠けさせる
        d = ab.blocks(rows, "ABBA")
        self.assertEqual(len(d), 3)

    def test_required_n_and_mde(self):
        self.assertEqual(ab.required_n(650, 150), 295)
        self.assertEqual(ab.required_n(650, 300), 74)
        self.assertEqual(ab.required_n(650, 150, two_sided=False), 233)
        self.assertAlmostEqual(ab.mde(650, 50), 364, delta=2)

    def test_arm_summary_and_cli(self):
        rows = _rows(100, 3)
        s = ab.arm_summary(rows)
        self.assertEqual(s["A"]["n"], 6)
        self.assertEqual(s["B"]["n"], 6)
        with tempfile.TemporaryDirectory() as d:
            g = os.path.join(d, "g.jsonl")
            with open(g, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            st = os.path.join(d, "s.json")
            json.dump({"a_hash": "a" * 12, "b_hash": "b" * 12, "pattern": "ABBA"}, open(st, "w"))
            import subprocess
            out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ab_report.py"), "--games", g, "--state", st, "--history", d, "--json"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            rep = json.loads(out.stdout)
            self.assertEqual(rep["eval_blocks"]["k"], 3)
            self.assertEqual(rep["required_n_per_arm"]["150"], 295)

    def test_state_primary_defaults_to_legacy_score(self):
        """primary 未記録の進行中実験は raw score のまま (途中で事前登録を変えない)。"""
        self.assertEqual(ab.state_primary({}), "score")
        self.assertEqual(ab.state_primary({"primary": "bogus"}), "score")
        self.assertEqual(
            ab.state_primary({"primary": "score", "primary_sd": 800}), "score"
        )
        # 指標別の SD 既定もここで固定する (eval は実測較正)。
        self.assertAlmostEqual(ab.state_primary_sd({}, "eval"), 3700.0)
        self.assertAlmostEqual(ab.state_primary_sd({}, "score"), 650.0)
        self.assertAlmostEqual(
            ab.state_primary_sd({"primary": "eval", "primary_sd": 4200.0}), 4200.0
        )

    def test_primary_value_prefers_metric_then_falls_back(self):
        row = {"eval": 12000.0, "score": 900.0}
        self.assertEqual(ab.primary_value(row, "eval"), 12000.0)
        self.assertEqual(ab.primary_value(row, "score"), 900.0)
        # eval 欠測なら score で補い、両方欠測なら None。
        self.assertEqual(ab.primary_value({"score": 900.0}, "eval"), 900.0)
        self.assertIsNone(ab.primary_value({}, "eval"))
        self.assertIsNone(ab.primary_value({"eval": 900.0}, "score"))

    def test_primary_appears_in_json_report(self):
        with tempfile.TemporaryDirectory() as d:
            g = os.path.join(d, "g.jsonl")
            rows = _rows(100, 3)
            with open(g, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            st = os.path.join(d, "s.json")
            json.dump({"a_hash": "a" * 12, "b_hash": "b" * 12, "pattern": "ABBA",
                       "primary": "eval", "primary_sd": 3000.0}, open(st, "w"))
            import subprocess
            out = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "ab_report.py"),
                 "--games", g, "--state", st, "--history", d, "--json"],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            rep = json.loads(out.stdout)
            self.assertEqual(rep["primary_name"], "eval")
            self.assertAlmostEqual(rep["primary_sd"], 3000.0)
            self.assertEqual(rep["primary_blocks"]["k"], 3)
            # eval=score+(-50)... _rows は eval=v, score=v-50 なので eval が正準。
            self.assertAlmostEqual(rep["primary_blocks"]["mean_diff"], 100, delta=120)


if __name__ == "__main__":
    unittest.main()
