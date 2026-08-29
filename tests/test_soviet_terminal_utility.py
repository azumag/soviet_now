"""issue #132 P0-0: ソ連建国 (type 16) の評価が、建国直前 (T15×2) より必ず高いこと。

修正前は TYPE_BONUS が type 15 で終わっていたため、T15 を 2 個持ったまま終局 (2×12096=24192) より
実際に建国した局面 (raw +136 と soviet ボーナス +800) のほうが 23,256 点低く評価され、
自動探索・候補選抜・回帰判定が「建国しないほうが良い」向きに働いていた。

ここでは eloop.sh と wildcard_parallel.py の両方の表が同じで、かつ
「建国 > T15×2 > T15 1個 > それ以下」の順序が壊れないことを固定する。
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _eloop_table():
    """eloop.sh の EVAL_SCORE 用インライン表を取り出す。"""
    src = open(os.path.join(ROOT, "eloop.sh"), encoding="utf-8").read()
    m = re.search(r"^TB = (\{[^}]*\})", src, re.M)
    assert m, "eloop.sh の TB 表が見つからない"
    return eval(m.group(1))  # noqa: S307 - テスト内の固定リテラル


def _wildcard_table():
    import wildcard_parallel

    return wildcard_parallel.TYPE_BONUS


def _eval(table, final_types, soviet):
    return sum(table.get(t, 0) for t in final_types) + (800 if soviet else 0)


class SovietTerminalUtilityTests(unittest.TestCase):
    def setUp(self):
        self.tables = {"eloop.sh": _eloop_table(), "wildcard_parallel.py": _wildcard_table()}

    def test_tables_agree(self):
        a, b = self.tables["eloop.sh"], self.tables["wildcard_parallel.py"]
        self.assertEqual(a, b, "eloop.sh と wildcard_parallel.py の評価表が一致していない")

    def test_type16_present(self):
        for name, t in self.tables.items():
            self.assertIn(16, t, "%s に type 16 (ソ連) が無い" % name)

    def test_founding_beats_two_russias(self):
        """建国した局面が、T15 を 2 個持ったまま終局した局面より高く評価される。"""
        for name, t in self.tables.items():
            before = _eval(t, [15, 15, 7, 3], soviet=False)
            # T15×2 が併合して T16 になった後の盤面 (raw score の増分は 136)
            after = _eval(t, [16, 7, 3], soviet=True) + 136
            self.assertGreater(after, before, "%s: 建国 %d <= 建国直前 %d" % (name, after, before))

    def test_monotonic_in_tier(self):
        """同じ枚数なら高 tier ほど高い。隣接比も単調 (>=2) を保つ。"""
        for name, t in self.tables.items():
            for k in range(3, 16):
                self.assertGreater(t[k + 1], t[k], "%s: type %d の値が type %d 以下" % (name, k + 1, k))
                self.assertGreaterEqual(t[k + 1], 2 * t[k], "%s: type %d の隣接比が 2 未満" % (name, k + 1))

    def test_founding_beats_any_unfounded_board(self):
        """建国は、T15 を 2 個までしか置けない盤面のどの未建国状態よりも高い。"""
        t = self.tables["wildcard_parallel.py"]
        founded = _eval(t, [16], soviet=True) + 136
        worst_case_unfounded = _eval(t, [15, 15, 14, 14, 13, 13], soviet=False)
        self.assertGreater(
            founded + _eval(t, [14, 14, 13, 13], soviet=False),
            worst_case_unfounded,
            "建国盤面 (残り駒同一) が未建国盤面より低い",
        )


if __name__ == "__main__":
    unittest.main()
