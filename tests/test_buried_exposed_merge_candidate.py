"""Regression for #605: do not bury an exposed same-type merge candidate.

When an exposed same-type pair A (a large-type merge lane that a future
next/nextNext piece can still reach) is visible and a different-type piece B can
be wedged on top of it, the placement must not be chosen while a safe
alternative landing exists.  Exercises the policy hook
(merge_opportunity_alternatives) through the runtime safety pass.

The first test is the case named by the issue and fails without the burial
loss term; the remaining tests pin the exception paths (no alternative landing,
immediate merge on top) and the trace that records the evaluation reason.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import strategy
import strategy_runner as runner


def piece(i, t, x, y=-3.0, radius=0.6):
    return {'id': i, 'type': t, 'x': x, 'y': y, 'r': radius, 'rx': radius, 'ry': radius}


def candidate(x, y=-2.0, grade='NO', **extra):
    return dict(x=x, landing_y=y, merge_grade=grade, crosses_deadline=False,
                merge_result_crosses_deadline=False, wall_rotation_risk=False,
                risk_top_y_after_drop=y + 0.3, top_y_after_drop=y + 0.3,
                deadline_y=3.38, deadline_margin=3.38 - y - 0.3, **extra)


def state(pieces, nt=2, nn=9):
    return {'pieces': pieces, 'next': {'type': nt, 'r': .3, 'rx': .3, 'ry': .3},
            'nextNext': {'type': nn}, 'deadline_crossed': False}


def analysis(results):
    return {'results': results, 'reactor': {'deadline_crossed': False, 'deadline_margin': 3.0},
            'deadline': {'deadline_crossed': False, 'deadline_y': 3.38, 'top_edge_y': -2.0}}


class BuryExposedMergeCandidateTests(unittest.TestCase):
    def choose(self, gs, results, x=0, reason='HEIGHT_CONTROL'):
        return runner.apply_strategy_final_decision(
            strategy, {'x': x, 'reason': reason}, analysis(results), gs)

    def test_strategy_avoids_burying_exposed_merge_candidate(self):
        # Two exposed type-9 pieces form a merge lane; the different-type piece
        # (type 2) can be wedged on top of the lane, but a safe side landing
        # also exists -> the lane must not be buried.
        gs = state([piece(1, 9, -0.8), piece(2, 9, 0.8)])
        self.assertEqual(self.choose(gs, [candidate(0), candidate(2, -3.5)])['x'], 2)

    def test_single_exposed_candidate_keeps_existing_redirect(self):
        # A lone exposed type-9 piece has no pair lane, so the pre-existing
        # loss terms still decide: the safe side landing wins.
        gs = state([piece(1, 9, 0)])
        self.assertEqual(self.choose(gs, [candidate(0), candidate(2, -3.5)])['x'], 2)

    def test_no_safe_alternative_keeps_burying_choice(self):
        # Only one landing exists: burying is accepted (game-over avoidance).
        gs = state([piece(1, 9, 0)])
        self.assertEqual(self.choose(gs, [candidate(0)])['x'], 0)

    def test_immediate_merge_on_top_is_not_redirected(self):
        # B itself merges immediately: the hook leaves the runtime choice alone.
        gs = state([piece(1, 9, -0.8), piece(2, 9, 0.8)])
        self.assertEqual(
            self.choose(gs, [candidate(0, grade='DIRECT'), candidate(2, -3.5)])['x'], 0)

    def test_burial_redirect_carries_trace(self):
        # The evaluation reason stays inspectable in the decision trace.
        gs = state([piece(1, 9, -0.8), piece(2, 9, 0.8)])
        out = self.choose(gs, [candidate(0), candidate(2, -3.5)])
        self.assertEqual(out['x'], 2)
        trace = out.get('merge_opportunity_trace')
        self.assertIsInstance(trace, dict)
        self.assertEqual(trace.get('outcome'), 'selected')
        self.assertTrue(trace.get('attempts'))


if __name__ == '__main__':
    unittest.main()
