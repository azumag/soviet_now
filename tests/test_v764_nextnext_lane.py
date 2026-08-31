"""v764 NEXTNEXT_LANE_KEPT の重み関数とレーン判定を固定する。

なぜ nextNext のレーンを狙うのか (実測 2026-08-31、実戦 304 手、候補ごとに post-state を評価):
  * 実際に選んだ手の後、nextNext の露出同型がある割合は 36.5%。
  * 同じ手の中で最良の候補なら 45.1%（+8.6pt）。「作れたのに作らなかった」が 8.6%。
  * 機会がある手と無い手の併合差は 1.291 対 0.169。+8.6pt は上界で +0.097 併合/手 に相当し、
    これまで測ったどのレバーより大きい。実際に v764 が取れるのは +3.7pt（W=1.0）。
  * これは横断比較ではなく「同じ手の中で候補を比べた」測定なので、交絡していない。

既存の AVOID_BLOCK_NEXTNEXT / NEXTNEXT_TWIN_COVER_AVOID は「既にあるレーンを潰さない」側の軸。
v764 は「レーンが残る置き方を選ぶ」側で、nextNext == next のときは落とした駒自身がレーンになる。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CAND = os.path.join(ROOT, "candidates", "strategy_v764_a26638fb7f2b.py")


def _load():
    sys.path.insert(0, os.path.abspath(ROOT))
    spec = importlib.util.spec_from_file_location("v764_cand", CAND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WeightTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("V764_NEXTNEXT_W")
        self.mod = _load()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("V764_NEXTNEXT_W", None)
        else:
            os.environ["V764_NEXTNEXT_W"] = self._saved

    def _w(self, value):
        if value is None:
            os.environ.pop("V764_NEXTNEXT_W", None)
        else:
            os.environ["V764_NEXTNEXT_W"] = value
        return self.mod._v764_weight()

    def test_disabled_by_default(self):
        self.assertEqual(self._w(None), 0.0)
        for off in ("", "0", "off", "no", "false"):
            self.assertEqual(self._w(off), 0.0, off)

    def test_bad_values_disable_and_large_values_saturate(self):
        for bad in ("abc", "-1", "nan"):
            self.assertEqual(self._w(bad), 0.0, bad)
        self.assertEqual(self._w("40"), 8.0)


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_dropping_the_same_type_always_makes_a_lane(self):
        # nextNext == next なら、落とした駒自身が露出同型になる。
        self.assertTrue(self.mod._v764_nn_open_after([], 5, 5, 0.0, 0.0))

    def test_no_lane_when_nothing_of_that_type_is_open(self):
        self.assertFalse(self.mod._v764_nn_open_after([], 5, 7, 0.0, 0.0))

    def test_a_distant_drop_keeps_the_lane(self):
        target = [{"id": 1, "type": 3, "x": -2.8, "y": -3.0}]
        self.assertTrue(self.mod._v764_nn_open_after(target, 3, 7, 2.8, 3.0))

    def test_dropping_on_the_only_lane_piece_closes_it(self):
        target = [{"id": 1, "type": 3, "x": 0.0, "y": -3.0}]
        self.assertFalse(self.mod._v764_nn_open_after(target, 3, 7, 0.0, 3.0))

    def test_a_second_lane_piece_survives_the_drop(self):
        targets = [{"id": 1, "type": 3, "x": 0.0, "y": -3.0},
                   {"id": 2, "type": 3, "x": -2.8, "y": -3.0}]
        self.assertTrue(self.mod._v764_nn_open_after(targets, 3, 7, 0.0, 3.0))

    def test_broken_input_is_treated_as_lane_kept(self):
        # 判定できないときに機会を潰したと決めつけない（安全側）。
        self.assertTrue(self.mod._v764_nn_open_after([{"id": 1, "type": 3, "x": "x", "y": 0.0}], 3, 7, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
