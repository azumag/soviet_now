"""v760 CEILING_RAISE の重み関数と天井計測を固定する。

なぜ「天井の押し上げ」を罰するのか (実測 2026-08-30):
  * 終了理由が記録された 122 試合のうち 121 件 (99.2%) が deadline_crossed。
    盤面を殺すのは総面積ではなく、どこか 1 列が y=3.38 に届くこと。
  * 終局の列別天端は中央 -2 が 3.41 に対し両端は 1.88 / 2.20 で、端に 1.2〜1.5 の余白が残る。

一度失敗した設計も併せて固定しておく: 「表面中央値より高い着地を罰する」形は、
中央値がその手の中で定数であり next_type も固定なので、既存の height_penalty と
順位付けが等価になる (実質、高さ罰の係数を上げただけ)。天井との差なら、天井より下の
候補がすべて 0 で並ぶため順位付けが異なる。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CAND = os.path.join(ROOT, "candidates", "strategy_v760_bda6d8c5901d.py")


def _load():
    sys.path.insert(0, os.path.abspath(ROOT))
    spec = importlib.util.spec_from_file_location("v760_cand", CAND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WeightTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("V760_CEILING_RAISE_W")
        self.mod = _load()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("V760_CEILING_RAISE_W", None)
        else:
            os.environ["V760_CEILING_RAISE_W"] = self._saved

    def _w(self, value):
        if value is None:
            os.environ.pop("V760_CEILING_RAISE_W", None)
        else:
            os.environ["V760_CEILING_RAISE_W"] = value
        return self.mod._v760_weight()

    def test_disabled_by_default(self):
        self.assertEqual(self._w(None), 0.0)
        for off in ("", "0", "off", "no", "false"):
            self.assertEqual(self._w(off), 0.0, off)

    def test_bad_values_disable(self):
        for bad in ("abc", "-3", "nan"):
            self.assertEqual(self._w(bad), 0.0, bad)
        self.assertEqual(self._w("99"), 8.0)   # 上限で飽和


class CeilingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_empty_board_is_the_floor(self):
        self.assertEqual(self.mod._v760_ceiling([]), self.mod._V760_FLOOR_Y)
        self.assertEqual(self.mod._v760_ceiling(None), self.mod._V760_FLOOR_Y)

    def test_ceiling_is_the_highest_top(self):
        pieces = [{"type": 1, "x": 0.0, "y": 0.0}, {"type": 1, "x": 2.0, "y": 1.5}]
        self.assertAlmostEqual(self.mod._v760_ceiling(pieces),
                               1.5 + self.mod.board_stats.seed_top_radius(1), places=6)

    def test_lower_centre_can_still_be_the_ceiling(self):
        # 天端 = y + 上半径。中心が低くても上半径が大きければ天井になる。
        # 上半径 (UNITY_PREFAB_DEADLINE_RADII 由来) は型に対して単調ではない:
        # T11 が 0.981 なのに T14 は 0.807 で、スプライト形状で決まる。
        small = {"type": 1, "x": 0.0, "y": 1.2}    # 天端 1.2 + 0.368 = 1.568
        big = {"type": 11, "x": 2.0, "y": 0.6}     # 天端 0.6 + 0.981 = 1.581
        top_small = 1.2 + self.mod.board_stats.seed_top_radius(1)
        top_big = 0.6 + self.mod.board_stats.seed_top_radius(11)
        self.assertGreater(top_big, top_small)
        self.assertAlmostEqual(self.mod._v760_ceiling([small, big]), top_big, places=6)

    def test_top_radius_is_not_monotonic_in_type(self):
        # 面積は物理半径 TYPE_RADII (単調)、デッドライン判定はスプライト由来の上半径 (非単調)。
        # 混同すると天井計算が壊れるので、非単調であること自体を固定しておく。
        bs = self.mod.board_stats
        self.assertGreater(bs.seed_top_radius(11), bs.seed_top_radius(14))

    def test_broken_pieces_are_skipped_not_fatal(self):
        pieces = [{"type": "x", "x": 0.0, "y": 0.0}, {"type": 1, "x": 0.0, "y": 2.0}]
        self.assertAlmostEqual(self.mod._v760_ceiling(pieces),
                               2.0 + self.mod.board_stats.seed_top_radius(1), places=6)


if __name__ == "__main__":
    unittest.main()
