"""v759 TIER_WEIGHTED_COVER_AVOID の重み関数と無効時の同一性を固定する。

軸感度の実測 (tools/replay_shadow.py、再現率 80% の harness、実戦 3000 手):
  DIRECT 併合ボーナス 1566.9 -> 0     : 決定変化 1.8%
  DIRECT 併合ボーナス x10             : 決定変化 0.0%   (飽和していて動かせない)
  HIGH_TYPE_COVER_AVOID -400 -> 0     : 決定変化 15.7%
  HIGH_TYPE_COVER_AVOID -400 -> -1600 : 決定変化 5.9%   (生きている軸)
したがって tier 重み付けは併合ボーナスではなく被覆抑止側に置いた。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CAND = os.path.join(ROOT, "candidates", "strategy_v759_dd78c998d24c.py")


def _load():
    sys.path.insert(0, os.path.abspath(ROOT))
    spec = importlib.util.spec_from_file_location("v759_cand", CAND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoverWeightTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("V759_COVER_TIER_W")
        self.mod = _load()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("V759_COVER_TIER_W", None)
        else:
            os.environ["V759_COVER_TIER_W"] = self._saved

    def _w(self, value):
        if value is None:
            os.environ.pop("V759_COVER_TIER_W", None)
        else:
            os.environ["V759_COVER_TIER_W"] = value
        return self.mod._v759_cover_weight()

    def test_disabled_by_default(self):
        self.assertEqual(self._w(None), 0.0)
        for off in ("", "0", "off", "no", "false"):
            self.assertEqual(self._w(off), 0.0, off)

    def test_bad_values_fall_back_to_disabled(self):
        for bad in ("abc", "-1", "nan"):
            self.assertEqual(self._w(bad), 0.0, bad)
        self.assertEqual(self._w("5"), 1.0)   # 上限で飽和

    def test_multiplier_is_exactly_one_when_disabled(self):
        for t in range(8, 16):
            self.assertEqual(self.mod._v759_cover_mult(t, 0.0), 1.0, t)

    def test_penalty_grows_with_tier(self):
        mults = [self.mod._v759_cover_mult(t, 1.0) for t in range(10, 15)]
        self.assertEqual(mults[0], 1.0)                       # T10 が基準
        for a, b in zip(mults, mults[1:]):
            self.assertLess(a, b)
        # T14 を埋める損害は T10 の 2.4 倍 (16.5 / 6.8)
        self.assertAlmostEqual(self.mod._v759_cover_mult(14, 1.0), 16.5 / 6.8, places=6)

    def test_blend_is_linear(self):
        full = self.mod._v759_cover_mult(13, 1.0)
        half = self.mod._v759_cover_mult(13, 0.5)
        self.assertAlmostEqual(half, (1.0 + full) / 2.0, places=9)

    def test_unknown_type_is_neutral(self):
        for bad in (None, "x", 1, 99):
            self.assertEqual(self.mod._v759_cover_mult(bad, 1.0), 1.0, bad)

    def test_turn_loss_table_matches_the_capacity_model(self):
        from tools import board_capacity as bc
        _, lev = bc.residue_leverage()
        for t, v in self.mod._V759_COVER_TURN_LOSS.items():
            if t in lev:
                self.assertAlmostEqual(v, lev[t], delta=0.6, msg="tier %d" % t)


if __name__ == "__main__":
    unittest.main()
