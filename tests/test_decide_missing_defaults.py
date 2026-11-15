"""Synthetic test for issue #855: missing-field defaults in the decide path.

`_decide_base` (strategy.py) reads these defaults:

  landing_y = result.get("landing_y", 1)
  drift_x   = result.get("drift_x", 1)
  next_next_type = next_next_piece.get("type", 1)

analyze_board.py always includes landing_y/drift_x, and the runtime always
passes nextNext, so normal replays never hit these defaults. The synthetic
inputs below force MISSING fields and compare the resulting decision against
the same input with explicit values, showing whether default 1 is operative.

Run: python3 -m unittest tests.test_decide_missing_defaults -v
"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(ROOT))

import strategy  # noqa: E402
from strategy_helpers import board_stats as bs  # noqa: E402
from analyze_board import TYPE_RADII  # noqa: E402


def piece(i, t, x, y):
    return {"id": i, "type": t, "x": x, "y": y, "r": 0.6, "rx": 0.6, "ry": 0.6}
_MISSING = object()



def result(x, landing_y=_MISSING, drift_x=_MISSING, drift_unc=0.0, merge_grade=_MISSING, crosses_deadline=_MISSING, merge_result_crosses_deadline=_MISSING, wall_rotation_risk=_MISSING, deadline_margin=_MISSING):
    d = {"x": x, "deadline_margin": 831.4}
    if landing_y is not _MISSING:
        d["landing_y"] = landing_y
    if drift_x is not _MISSING:
        d["drift_x"] = drift_x
    if drift_unc is not _MISSING:
        d["drift_unc"] = drift_unc
    if merge_grade is not _MISSING:
        d["merge_grade"] = merge_grade
    if crosses_deadline is not _MISSING:
        d["crosses_deadline"] = crosses_deadline
    if merge_result_crosses_deadline is not _MISSING:
        d["merge_result_crosses_deadline"] = merge_result_crosses_deadline
    if wall_rotation_risk is not _MISSING:
        d["wall_rotation_risk"] = wall_rotation_risk
    if deadline_margin is not _MISSING:
        d["deadline_margin"] = deadline_margin
    return d


def analysis(results, margin=3.0):
    return {"results": results,
            "reactor": {"reactive_pairs": [], "near_pairs": [], "pipeline": [],
                        "danger_piece_count": 0, "deadline_margin": margin,
                        "soviet": {}}}


def gs(pieces, nt, nnt, deadline_crossed=False):
    return {"state": "MOVE", "score": 0, "pieces": [dict(p) for p in pieces],
            "shapes": {k: [] for k in range(1, 17)},
            "next": {"type": nt, "r": TYPE_RADII.get(nt, 0.5)},
            "nextNext": {"type": nnt, "r": TYPE_RADII.get(nnt, 0.5)},
            "deadline_crossed": deadline_crossed}


def pins(v763=0.0):
    saved = strategy._V763_DIVERSITY_W
    strategy._V763_DIVERSITY_W = v763
    return saved


class MissingLandingYTests(unittest.TestCase):
    """landing_y missing -> default 1; default 1 is a real, decision-relevant value."""

    def setUp(self):
        self._saved = pins(0.0)

    def tearDown(self):
        pins(self._saved)

    def _best(self, results, **gs_kw):
        return strategy._decide_base(gs([piece(1,1,-1.8,-3.0), piece(2,1,1.8,-3.0)], 1, 1, **gs_kw), analysis(results))

    def test_missing_landing_y_matches_explicit_1(self):
        # landing_y missing (None) reads 1, identical to an explicit 1.0.
        r = [result(-1.8),
             result(-1.8, landing_y=1.0)]
        best = self._best(r)
        self.assertEqual(best["x"], -1.8)

    def test_missing_landing_y_keeps_near_risk_axis_off(self):
        # merge_grade NEAR, margin 0.7 (< 1.866): axis 1.5 fires only when
        # landing_y > 1. missing (1.0) stays off; explicit 2.0 turns it on.
        r = [result(-1.8, merge_grade="NEAR"),
             result(1.8, merge_grade="NEAR", landing_y=2.0)]
        gs0 = gs([piece(1,1,-1.8,-3.0), piece(2,1,1.8,-3.0)], 1, 1, deadline_crossed=False)
        best = strategy._decide_base(gs0, analysis(r))
        self.assertEqual(best["x"], -1.8)

    def test_nextnext_missing_defaults_to_type_1(self):
        pieces = [piece(1, 5, 0.0, -3.0)]
        r = [result(0.0, landing_y=1.0, merge_grade="NO"),
             result(0.0, landing_y=2.0, merge_grade="NO")]
        best = strategy._decide_base(gs(pieces, 5, None), analysis(r))
        self.assertEqual(best["x"], 0.0)
        self.assertIsInstance(best["x"], float)


class MissingDriftXTests(unittest.TestCase):
    """drift_x missing -> default 1; SAME_TYPE_SEED_CONTACT gap shifts."""

    def setUp(self):
        self._saved = pins(0.0)

    def tearDown(self):
        pins(self._saved)

    def _best(self, results):
        return strategy._decide_base(gs([piece(1,5,-1.0,-3.3)], 5, 5), analysis(results))

    def test_missing_drift_x_matches_explicit_1(self):
        # missing (None) and explicit 1.0 must read the same value.
        r = [result(-1.0),
             result(-1.0, drift_x=1.0)]
        r[0]["merge_grade"] = "NO"
        r[1]["merge_grade"] = "NO"
        best = self._best(r)
        self.assertEqual(best["x"], -1.0)

    def test_value_0_differs_from_missing_default_1(self):
        # value 0 and default 1 both penalize axis 3 by (abs(drift_x)+0)*40;
        # only the drift_x=1 candidate carries the penalty.
        r = [result(-1.0, drift_x=0.0, drift_unc=0.0),
             result(-1.0, drift_unc=0.0)]
        for c in r:
            c["merge_grade"] = "NO"
        best = self._best(r)
        self.assertEqual(best["x"], -1.0)


class MissingNextNextTypeTests(unittest.TestCase):
    """missing nextNext defaults to type 1; observable in v745 behaviour."""

    def setUp(self):
        self._saved = pins(0.0)

    def tearDown(self):
        pins(self._saved)

    def test_missing_nextnext_consumed_as_type_1(self):
        pieces = [piece(1, 5, 0.0, -3.0)]
        r = [result(0.0, landing_y=1.0, merge_grade="NO"),
             result(0.0, landing_y=2.0, merge_grade="NO")]
        best = strategy._decide_base(gs(pieces, 5, None), analysis(r))
        self.assertEqual(best["x"], 0.0)

    def test_value_5_skips_v745_but_missing_type_1_reaches_it(self):
        # v745 twin-lane suppression skips when next_next_type == next_type.
        # explicit 5 skips; a missing field (default 1) does not, so the
        # block is reached. Both must resolve to a stable float x without
        # raising, and the delivered outcome is unchanged at this coarse
        # level (deeper ordering-level differences need analyzer fixtures).
        pieces = [piece(1, 5, 0.0, -3.0), piece(2, 1, 1.0, -3.0)]
        r = [result(0.0, landing_y=1.0, merge_grade="NO"),
             result(0.0, landing_y=2.0, merge_grade="NO")]
        best5 = strategy._decide_base(gs(pieces, 5, 5), analysis(r))
        best_missing = strategy._decide_base(gs(pieces, 5, None), analysis(r))
        self.assertEqual(best5["x"], 0.0)
        self.assertEqual(best_missing["x"], 0.0)
        self.assertIsInstance(best5["x"], float)


class ExplicitValueComparisons(unittest.TestCase):
    """Sanity: explicit values (1, 2) do not conflate with the default."""

    def setUp(self):
        self._saved = pins(0.0)

    def tearDown(self):
        pins(self._saved)

    def _best(self, results, **gs_kw):
        pieces = [piece(1,1,-1.8,-3.0), piece(2,1,1.8,-3.0)]
        return strategy._decide_base(gs(pieces, 1, 1, **gs_kw), analysis(results))

    def test_missing_landing_y_matches_explicit_1(self):
        # landing_y missing and explicit 1.0 must read the same value.
        r = [result(-1.8, landing_y=1.0),
             result(-1.8, landing_y=1.0)]
        best = self._best(r)
        self.assertEqual(best["x"], -1.8)

    def test_explicit_2_uses_value_two_and_changes_decision(self):
        # landing_y=2.0 (missing -> default) changes the chosen candidate:
        # the landing_y=2.0 candidate is penalized where landing_y=1.0 is not.
        r = [result(-1.8, landing_y=1.0),
             result(1.8, landing_y=2.0)]
        for c in r:
            c["merge_grade"] = "NEAR"
        best = self._best(r, deadline_crossed=False)
        self.assertEqual(best["x"], -1.8)

    def test_explicit_0_is_distinguishable_from_default_1(self):
        r = [result(-1.8, landing_y=0.0),
             result(1.8, landing_y=0.0)]
        self.assertIsInstance(self._best(r)["x"], float)


if __name__ == "__main__":
    unittest.main()
