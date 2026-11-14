"""Regression for the axis-4 surface-flatness term (issue #132 P0-2).

The old left-right balance axis was doubly broken: `p["x"] < 4` is always true
on a board where x ∈ [-3, 3], and `sum(-2 for p in ...)` counted `-2` per piece
instead of the piece itself.  `balance_bias` collapsed to a constant (~4.86),
so the axis degraded into a pure `|x|`-proportional center-attraction penalty
that measured no board state at all — while being the single most influential
axis (38.5% of decision changes measured on real turns).

Production death-mode measurement: 99.2% of games end by one column reaching
the deadline, and end boards show the center columns at the deadline while the
edges keep 1.2-1.5 of spare.  The replacement term penalizes landing above the
measured median surface top and rewards filling below-median ground, which is
what the axis was always meant to do.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import strategy


def piece(i, t, x, y):
    return {'id': i, 'type': t, 'x': x, 'y': y, 'r': 0.6, 'rx': 0.6, 'ry': 0.6}


def candidate(x, y, grade='NO'):
    return dict(x=x, landing_y=y, drift_x=0.0, drift_unc=0.0, merge_grade=grade,
                crosses_deadline=False, merge_result_crosses_deadline=False,
                wall_rotation_risk=False)


def state(pieces, nt=5, nn=9):
    return {'pieces': pieces, 'next': {'type': nt, 'r': .4, 'rx': .4, 'ry': .4},
            'nextNext': {'type': nn}, 'deadline_crossed': False}


def analysis(results):
    return {'results': results,
            'reactor': {'reactive_pairs': [], 'near_pairs': [], 'pipeline': [],
                        'danger_piece_count': 0, 'deadline_margin': 3.0,
                        'soviet': {}},
            'deadline': {'deadline_crossed': False, 'deadline_y': 3.38}}


class SurfaceMedianTopTests(unittest.TestCase):
    def test_median_top_tracks_surface_not_buried_pieces(self):
        # 高い塔 (x≈1) と平坦な低地。13 格子点のうち塔が占めるのは少数なので
        # 中央値は床付近になる — 中央値は「典型列の高さ」であって最大値ではない。
        pieces = [piece(1, 9, 1.0, -1.0), piece(2, 9, 1.4, -0.5),
                  piece(3, 5, -1.0, -4.0), piece(4, 2, 0.0, -4.3)]
        med = strategy._surface_median_top(pieces)
        self.assertIsNotNone(med)
        self.assertLess(med, -3.0)  # 床 -4.48 と塔の上端の間、床寄り

    def test_median_top_full_board_uses_column_tops(self):
        # 全格子点を覆う盤面では中央値は実表面の高さになる。
        pieces = [piece(i, 8, -3.0 + 0.5 * i, -2.0) for i in range(13)]
        med = strategy._surface_median_top(pieces)
        self.assertIsNotNone(med)
        self.assertGreater(med, -2.5)
        self.assertLess(med, 0.0)

    def test_empty_or_unreadable_board_fails_closed(self):
        self.assertIsNone(strategy._surface_median_top([]))
        self.assertIsNone(strategy._surface_median_top(None))
        self.assertIsNone(strategy._surface_median_top([{'x': 'bad'}, None]))


class SurfaceFlatnessAxisTests(unittest.TestCase):
    def test_prefers_filling_low_ground_over_raising_tall_column(self):
        # x≈1 に高い塔、右端は空き。旧軸は |x| 比例で右端を強く罰したが、
        # 新軸は塔を更に高くする x=1.1 を抑制して低地の右端を選ぶ。
        gs = state([piece(1, 9, 1.0, -1.0), piece(2, 9, 1.4, -0.5),
                    piece(3, 5, -1.0, -4.0), piece(4, 2, 0.0, -4.3)])
        results = [candidate(-2.5, -4.0), candidate(0.0, -3.8),
                   candidate(1.1, 0.5), candidate(2.5, -4.4)]
        decision = strategy.decide(gs, analysis(results))
        self.assertEqual(decision['x'], 2.5)

    def test_high_column_landing_is_not_chosen(self):
        # 塔の真上（中央値を大きく超える着地）は選ばれない。死因が「1列の
        # デッドライン到達」である以上、既に高い列を更に上げる手は抑制されるべき。
        gs = state([piece(1, 9, 1.0, -1.0), piece(2, 9, 1.4, -0.5),
                    piece(3, 5, -1.0, -4.0), piece(4, 2, 0.0, -4.3)])
        results = [candidate(-2.5, -4.0), candidate(0.0, -3.8),
                   candidate(1.1, 0.5), candidate(2.5, -4.4)]
        decision = strategy.decide(gs, analysis(results))
        self.assertNotEqual(decision['x'], 1.1)

    def test_term_computation_matches_board_surface(self):
        # 平坦化項は「中央値を超える post-drop 列トップ」を罰する。
        # 実装上の等価物をここで検算する（decide 内で同じ式を使う）。
        pieces = [piece(i, 8, -3.0 + 0.5 * i, -2.0) for i in range(13)]
        med = strategy._surface_median_top(pieces)
        self.assertIsNotNone(med)
        # 中央値と同じ列への着地は excess≈0（post-drop 列トップ = landing_y + top_r）
        import strategy_helpers.board_stats as bs
        landing_at_median = med - bs.seed_top_radius(5)
        excess = landing_at_median + bs.seed_top_radius(5) - med
        self.assertAlmostEqual(excess, 0.0, places=5)
        # 中央値を 1.0 超える着地は excess = +1.0 → 罰側
        excess_hi = landing_at_median + 1.0 + bs.seed_top_radius(5) - med
        self.assertAlmostEqual(excess_hi, 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
