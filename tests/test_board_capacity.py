"""board_capacity の第一原理モデルを実測へ固定する (issue #132 P1)。

実測は 2026-08-30 の実戦 75 試合 / 7041 手 (v752 30 / v757 31 / 旧 14)。
盤面 snapshot の r は視覚スプライト由来で単調でないため、面積は analyze_board の
物理半径 TYPE_RADII から計算している。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import board_capacity as bc


class ObservedFitTests(unittest.TestCase):
    def test_reproduces_the_measured_end_state(self):
        area, end, merges = bc.cascade(bc.OBSERVED_TURNS, bc.OBSERVED_RESIDUE)
        self.assertAlmostEqual(area, 49.3, delta=1.0)                       # 実測 49.3
        self.assertAlmostEqual(sum(merges.values()) / bc.OBSERVED_TURNS, 0.5637, delta=0.01)
        self.assertAlmostEqual(sum(end[t] for t in range(1, 16)), 41.0, delta=1.0)

    def test_value_is_conserved_by_merging(self):
        _, end, _ = bc.cascade(bc.OBSERVED_TURNS, bc.OBSERVED_RESIDUE)
        held = sum(end[t] * bc.VALUE[t] for t in end if t in bc.VALUE)
        supplied = bc.OBSERVED_TURNS * bc.MEAN_DROP_VALUE
        self.assertAlmostEqual(held / supplied, 1.0, delta=0.05)

    def test_turns_equal_merges_plus_end_pieces(self):
        # 駒は盤面から消えない: 落下 T 個 - 併合 M 回 = 終局駒数。
        _, end, merges = bc.cascade(bc.OBSERVED_TURNS, bc.OBSERVED_RESIDUE)
        self.assertAlmostEqual(
            bc.OBSERVED_TURNS - sum(merges.values()),
            sum(end[t] for t in range(1, 17)),
            delta=0.5,
        )


class LeverageTests(unittest.TestCase):
    def test_area_relief_grows_with_tier(self):
        rel = bc.AREA_RELIEF
        for t in range(1, 14):
            self.assertLess(rel[t], rel[t + 1], "tier %d" % t)
        # T11 併合は T1 併合の 40 倍以上の面積を解放する
        self.assertGreater(rel[11] / rel[1], 40.0)

    def test_high_tier_residue_costs_far_more_turns(self):
        _, lev = bc.residue_leverage()
        for t in range(1, 14):
            self.assertLessEqual(lev[t], lev[t + 1] + 1e-9, "tier %d" % t)
        self.assertGreater(lev[11], 5.0)      # 高 tier 残留 1 個で 5 手以上失う
        self.assertLess(lev[1], 1.0)          # 低 tier は 1 手未満

    def test_less_residue_buys_more_turns(self):
        base = bc.sustainable_turns(bc.OBSERVED_RESIDUE)
        half = bc.sustainable_turns({t: v * 0.5 for t, v in bc.OBSERVED_RESIDUE.items()})
        worse = bc.sustainable_turns({t: v * 1.5 for t, v in bc.OBSERVED_RESIDUE.items()})
        self.assertGreater(half, base)
        self.assertLess(worse, base)
        self.assertAlmostEqual(base, 94.0, delta=4.0)   # 実測平均手数

    def test_soviet_founding_needs_roughly_double_the_game_length(self):
        # 2xT15 の価値 32 T11 相当 / 供給 0.1817 per turn -> 176 手が理論下限。
        self.assertAlmostEqual(32.0 / bc.MEAN_DROP_VALUE, 176.0, delta=3.0)
        _, end, _ = bc.cascade(176, bc.scale_residue({t: v * 0.62 for t, v in bc.OBSERVED_RESIDUE.items()}, 176))
        self.assertGreater(end[16], 0.0)


if __name__ == "__main__":
    unittest.main()
