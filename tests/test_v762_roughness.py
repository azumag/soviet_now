"""v762 SURFACE_ROUGHNESS の重み関数・列天端・凸凹計算を固定する。

なぜ凸凹を罰するのか (実測 2026-08-31):
  * 盤面に本当に露出した同型がある型は 1..11 のうち平均 4.37 個しかなく、落下型は一様なので
    併合機会が生じるのは 39.6% の手だけ (4.37/11 = 39.8% と一致)。
  * 表面が平坦なほど露出型は多い: 列天端 std の平坦上位 1/4 で 4.84 型、凸凹下位 1/4 で 4.12 型。
    +0.72 型は機会率 +6.5pt に相当し、露出同型への落下は 86.5% で併合する。

なぜ height_penalty の焼き直しではないのか:
  1 手の中で候補を並べたときの Spearman 順位相関が
    landing_y vs 落下後の凸凹      : median +0.733（>0.95 は 6.2% の手だけ）
    landing_y vs 天井押し上げ量(v760): median +1.000（92.2% の手で >0.95）
  であり、凸凹項だけが既存の高さ罰と異なる順位付けを与える。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CAND = os.path.join(ROOT, "candidates", "strategy_v762_d45e8b912dbe.py")


def _load():
    sys.path.insert(0, os.path.abspath(ROOT))
    spec = importlib.util.spec_from_file_location("v762_cand", CAND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WeightTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("V762_ROUGHNESS_W")
        self.mod = _load()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("V762_ROUGHNESS_W", None)
        else:
            os.environ["V762_ROUGHNESS_W"] = self._saved

    def _w(self, value):
        if value is None:
            os.environ.pop("V762_ROUGHNESS_W", None)
        else:
            os.environ["V762_ROUGHNESS_W"] = value
        return self.mod._v762_weight()

    def test_disabled_by_default(self):
        self.assertEqual(self._w(None), 0.0)
        for off in ("", "0", "off", "no", "false"):
            self.assertEqual(self._w(off), 0.0, off)

    def test_bad_values_disable_and_large_values_saturate(self):
        for bad in ("abc", "-2", "nan"):
            self.assertEqual(self._w(bad), 0.0, bad)
        self.assertEqual(self._w("99"), 8.0)


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_empty_board_is_flat_at_the_floor(self):
        tops = self.mod._v762_column_tops([])
        self.assertEqual(set(tops), set(range(-3, 4)))
        self.assertEqual(set(tops.values()), {self.mod._V762_FLOOR_Y})
        self.assertEqual(self.mod._v762_roughness(tops), 0.0)

    def test_column_top_is_the_highest_piece_top_in_that_column(self):
        pieces = [{"type": 1, "x": 0.1, "y": 0.0}, {"type": 1, "x": -0.2, "y": 2.0}]
        tops = self.mod._v762_column_tops(pieces)
        self.assertAlmostEqual(tops[0], 2.0 + self.mod.board_stats.seed_top_radius(1), places=6)
        self.assertEqual(tops[3], self.mod._V762_FLOOR_Y)

    def test_pieces_outside_the_seven_columns_are_ignored(self):
        tops = self.mod._v762_column_tops([{"type": 1, "x": 9.0, "y": 5.0}])
        self.assertEqual(set(tops.values()), {self.mod._V762_FLOOR_Y})

    def test_one_tall_column_is_rougher_than_an_even_surface(self):
        even = {b: 0.0 for b in range(-3, 4)}
        spike = dict(even)
        spike[0] = 3.0
        self.assertEqual(self.mod._v762_roughness(even), 0.0)
        self.assertGreater(self.mod._v762_roughness(spike), 1.0)

    def test_filling_a_valley_reduces_roughness(self):
        # 谷を埋める手は凸凹を減らす。高さ罰だけでは表現できない向き。
        board = {b: 1.0 for b in range(-3, 4)}
        board[-2] = -3.0
        before = self.mod._v762_roughness(board)
        board[-2] = 0.5
        self.assertLess(self.mod._v762_roughness(board), before)

    def test_broken_pieces_do_not_break_the_surface(self):
        tops = self.mod._v762_column_tops([{"type": "x", "x": 0.0, "y": 0.0},
                                           {"type": 1, "x": 0.0, "y": 1.0}])
        self.assertAlmostEqual(tops[0], 1.0 + self.mod.board_stats.seed_top_radius(1), places=6)


if __name__ == "__main__":
    unittest.main()
