"""issue #132 P0-5: A/B レポートのブロック完全性と母集団の一致。

修正前の `blocks()` は「pattern 長ぶんの記録があり、A と B が 1 件以上」だけを見ていたため、
AAAB のような偏り、idx の欠番・重複を含むブロックも採用され得た。`arm_summary()` は
tainted を除外していなかったため、表示平均と検定の母集団も食い違っていた。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ab_report  # noqa: E402


def rec(idx, arm, score, tainted=False, turns=100):
    return {"idx": idx, "arm": arm, "score": score, "turns": turns, "tainted": tainted}


class AbReportBlockTests(unittest.TestCase):
    def test_valid_abba_block_counts(self):
        rows = [rec(0, "A", 100), rec(1, "B", 200), rec(2, "B", 200), rec(3, "A", 100)]
        self.assertEqual(ab_report.blocks(rows, "ABBA", key="score"), [100.0])

    def test_skewed_arms_rejected(self):
        """AAAB は ABBA ブロックとして採用しない。"""
        rows = [rec(0, "A", 100), rec(1, "A", 100), rec(2, "A", 100), rec(3, "B", 200)]
        self.assertEqual(ab_report.blocks(rows, "ABBA", key="score"), [])

    def test_duplicate_idx_rejected(self):
        """同じ idx が 2 度入ったブロックは (重複除去後に数が足りず) 採用しない。"""
        rows = [rec(0, "A", 100), rec(1, "B", 200), rec(1, "B", 900), rec(3, "A", 100)]
        self.assertEqual(ab_report.blocks(rows, "ABBA", key="score"), [])

    def test_gap_in_idx_rejected(self):
        rows = [rec(0, "A", 100), rec(1, "B", 200), rec(2, "B", 200), rec(5, "A", 100)]
        self.assertEqual(ab_report.blocks(rows, "ABBA", key="score"), [])

    def test_tainted_excluded_from_blocks_and_summary(self):
        rows = [rec(0, "A", 100), rec(1, "B", 200), rec(2, "B", 200), rec(3, "A", 100, tainted=True)]
        self.assertEqual(ab_report.blocks(rows, "ABBA", key="score"), [])
        s = ab_report.arm_summary(rows, key="score")
        self.assertEqual(s["A"]["n"], 1, "tainted が腕の要約から除外されていない")
        self.assertEqual(s["B"]["n"], 2)

    def test_summary_dedupes_idx(self):
        rows = [rec(0, "A", 100), rec(0, "A", 999), rec(1, "B", 200)]
        s = ab_report.arm_summary(rows, key="score")
        self.assertEqual(s["A"]["n"], 1, "idx 重複が腕の要約で二重計上されている")
        self.assertEqual(s["A"]["mean"], 100)

    def test_two_valid_blocks(self):
        rows = [rec(0, "A", 100), rec(1, "B", 200), rec(2, "B", 200), rec(3, "A", 100),
                rec(4, "A", 300), rec(5, "B", 100), rec(6, "B", 100), rec(7, "A", 300)]
        self.assertEqual(ab_report.blocks(rows, "ABBA", key="score"), [100.0, -200.0])


if __name__ == "__main__":
    unittest.main()
