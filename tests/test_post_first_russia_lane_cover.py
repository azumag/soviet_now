from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy


class PostFirstRussiaLaneCoverTest(unittest.TestCase):
    def test_lone_turkmenistan_is_not_covered_after_first_kazakhstan(self):
        pieces = [
            {"id": 10, "type": 10, "x": 2.6, "y": 0.35, "r": 0.95},
            {"id": 11, "type": 11, "x": -1.8, "y": 0.5, "r": 1.6},
            {"id": 12, "type": 12, "x": -0.1, "y": -0.6, "r": 1.2},
            {"id": 14, "type": 14, "x": 1.9, "y": -2.0, "r": 1.2},
        ]
        results = [
            {
                "x": -0.09,
                "merge_grade": "NO",
                "crosses_deadline": False,
                "merge_result_crosses_deadline": False,
                "risk_top_y_after_drop": 2.713,
                "deadline_margin": 0.667,
                "landing_hit_id": 12,
                "top_y_after_drop": 2.713,
            },
            {
                "x": 2.6,
                "merge_grade": "NO",
                "crosses_deadline": False,
                "merge_result_crosses_deadline": False,
                "risk_top_y_after_drop": 2.306,
                "deadline_margin": 1.074,
                "landing_hit_id": 10,
                "top_y_after_drop": 2.117,
            },
        ]
        decision = {"x": -0.09, "reason": "safe enforced drop"}

        finalized = strategy.finalize_decision(
            copy.deepcopy({"pieces": pieces, "next": {"type": 8}}),
            copy.deepcopy({"results": results}),
            copy.deepcopy(decision),
        )

        self.assertEqual(finalized["x"], 2.6)
        self.assertEqual(finalized["reason"], "POST_FIRST_RUSSIA_LANE_COVER_AVOID")

    PIECES = [
        {"id": 10, "type": 10, "x": 2.6, "y": 0.35, "r": 0.95},
        {"id": 11, "type": 11, "x": -1.8, "y": 0.5, "r": 1.6},
        {"id": 12, "type": 12, "x": -0.1, "y": -0.6, "r": 1.2},
        {"id": 14, "type": 14, "x": 1.9, "y": -2.0, "r": 1.2},
    ]

    @staticmethod
    def cand(x, risk, margin, hit, grade="NO", crosses=False, merge_crosses=False):
        return {
            "x": x,
            "merge_grade": grade,
            "crosses_deadline": crosses,
            "merge_result_crosses_deadline": merge_crosses,
            "risk_top_y_after_drop": risk,
            "deadline_margin": margin,
            "landing_hit_id": hit,
            "top_y_after_drop": risk,
        }

    def finalize(self, results, decision_x=-0.09):
        return strategy.finalize_decision(
            copy.deepcopy({"pieces": self.PIECES, "next": {"type": 8}}),
            copy.deepcopy({"results": results}),
            {"x": decision_x, "reason": "safe enforced drop"},
        )

    def test_ineligible_candidates_are_skipped_not_fatal(self):
        # v727: a deadline-crossing candidate or a true floor landing elsewhere
        # in the results must not abort the whole selector.  Replayed
        # production states showed the v725/v726 abort dropped an otherwise
        # valid recovery back to the blind center fallback.
        results = [
            self.cand(-0.09, 2.713, 0.667, hit=12),
            self.cand(-3.0, 3.5, -0.1, hit=11, crosses=True),
            self.cand(1.0, 2.0, 1.8, hit=None),
            self.cand(2.6, 2.306, 1.074, hit=10),
        ]
        finalized = self.finalize(results)
        self.assertEqual(finalized["x"], 2.6)
        self.assertEqual(finalized["reason"], "POST_FIRST_RUSSIA_LANE_COVER_AVOID")

    def test_safe_floor_landing_still_bounds_replacement_risk(self):
        # A safe floor landing is not a replacement target but it stays in the
        # risk quality floor: a replacement clearly riskier than the safest
        # available lane must be rejected (review H1).
        results = [
            self.cand(-0.09, 2.713, 0.667, hit=12),
            self.cand(1.0, 1.5, 1.8, hit=None),
            self.cand(2.6, 2.306, 1.074, hit=10),
        ]
        finalized = self.finalize(results)
        self.assertNotIn("POST_FIRST_RUSSIA_LANE_COVER_AVOID", finalized.get("reason", ""))

    def test_replacement_respects_pre_russia_drop_clamp(self):
        # This selector only runs pre-Russia; a replacement outside the proven
        # pre-Russia drop clamp (-0.991 left bound) must not be emitted.
        results = [
            self.cand(-0.09, 2.713, 0.667, hit=12),
            self.cand(-1.65, 2.0, 1.2, hit=10),
        ]
        finalized = self.finalize(results)
        self.assertNotIn("POST_FIRST_RUSSIA_LANE_COVER_AVOID", finalized.get("reason", ""))

    def test_unsafe_selected_decision_is_never_replaced(self):
        # Review M2: a selected decision that crosses the deadline itself or
        # whose merge result crosses must stay untouched by this selector.
        for kwargs in ({"crosses": True, "margin": -0.1}, {"merge_crosses": True}):
            with self.subTest(kwargs=kwargs):
                selected = self.cand(
                    -0.09,
                    2.713,
                    kwargs.get("margin", 0.667),
                    hit=12,
                    crosses=kwargs.get("crosses", False),
                    merge_crosses=kwargs.get("merge_crosses", False),
                )
                results = [selected, self.cand(2.6, 2.306, 1.074, hit=10)]
                finalized = self.finalize(results)
                self.assertNotIn(
                    "POST_FIRST_RUSSIA_LANE_COVER_AVOID", finalized.get("reason", "")
                )

    def test_malformed_candidate_data_still_fails_closed(self):
        base = self.cand(-0.09, 2.713, 0.667, hit=12)
        good_alt = self.cand(2.6, 2.306, 1.074, hit=10)
        for broken in (
            dict(base, x=1.5, deadline_margin=float("nan")),
            dict(base, x=1.5, crosses_deadline="yes"),
            dict(base, x=1.5, merge_result_crosses_deadline="no"),
            dict(base, x=-0.09),  # duplicate x
            dict(base, x=1.5, merge_grade="MAYBE"),
            dict(base, x=1.5, landing_hit_id=-5),
            dict(base, x=1.5, landing_hit_id=""),
            dict(base, x=1.5, landing_hit_id=True),
            dict(base, x=1.5, landing_hit_id=99),  # unknown piece id
            "not-a-dict",
        ):
            with self.subTest(broken=broken):
                finalized = self.finalize([base, good_alt, broken])
                self.assertNotIn(
                    "POST_FIRST_RUSSIA_LANE_COVER_AVOID", finalized.get("reason", "")
                )

    def test_ordinary_or_unsafe_states_keep_the_enforced_decision(self):
        pieces = [
            {"id": 10, "type": 10, "x": 2.6, "y": 0.35, "r": 0.95},
            {"id": 12, "type": 12, "x": -0.1, "y": -0.6, "r": 1.2},
            {"id": 14, "type": 14, "x": 1.9, "y": -2.0, "r": 1.2},
        ]
        selected = {
            "x": -0.09,
            "merge_grade": "NO",
            "crosses_deadline": False,
            "merge_result_crosses_deadline": False,
            "risk_top_y_after_drop": 2.31,
            "deadline_margin": 0.81,
            "landing_hit_id": 12,
            "top_y_after_drop": 2.31,
        }
        alternative = dict(selected, x=2.6, landing_hit_id=10)

        # A materially worse alternative must not displace the enforced cover.
        no_better_risk = strategy.finalize_decision(
            {"pieces": pieces, "next": {"type": 8}},
            {
                "results": [
                    selected,
                    dict(
                        alternative,
                        risk_top_y_after_drop=selected["risk_top_y_after_drop"] + 0.06,
                        deadline_margin=selected["deadline_margin"] - 0.01,
                    ),
                ]
            },
            {"x": -0.09},
        )
        for decision in (no_better_risk,):
            with self.subTest(decision=decision):
                self.assertNotIn("POST_FIRST_RUSSIA_LANE_COVER_AVOID", decision.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
