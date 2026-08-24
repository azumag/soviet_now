from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy
import strategy_runner


def piece(piece_id: str, piece_type: int, x: float = 0.0) -> dict:
    return {
        "id": piece_id,
        "type": piece_type,
        "x": x,
        "y": -2.0,
        "r": 0.5,
    }


def candidate(
    x: float,
    *,
    hit_id: str,
    risk: float = 2.0,
    margin: float = 0.7,
    grade: str = "NO",
    crosses: bool = False,
    merge_crosses: bool = False,
) -> dict:
    return {
        "x": x,
        "landing_y": -1.0,
        "landing_hit_id": hit_id,
        "risk_top_y_after_drop": risk,
        "deadline_margin": margin,
        "merge_grade": grade,
        "crosses_deadline": crosses,
        "merge_result_crosses_deadline": merge_crosses,
        "drift_x": 0.0,
        "drift_unc": 0.0,
        "merges": [],
    }


class PreRussiaChainCoverAvoidanceTest(unittest.TestCase):
    def setUp(self):
        self.pieces = [
            piece("kazakhstan", 14, -2.0),
            piece("uzbekistan-left", 11, -0.5),
            piece("uzbekistan-right", 11, 0.8),
            piece("lithuania", 5, 1.5),
        ]
        self.selected = candidate(
            0.0,
            hit_id="uzbekistan-left",
            risk=2.0,
            margin=0.7,
        )
        self.alternative = candidate(
            1.6,
            hit_id="lithuania",
            risk=1.8,
            margin=0.9,
        )

    def select(
        self,
        *,
        pieces=None,
        results=None,
        next_type=10,
        chosen_x=0.0,
    ):
        return strategy._select_pre_russia_chain_cover_avoidance(
            self.pieces if pieces is None else pieces,
            [self.selected, self.alternative] if results is None else results,
            next_type,
            chosen_x,
        )

    def test_uses_materially_safer_lower_country_lane(self):
        self.assertIs(self.select(), self.alternative)

    def test_only_incoming_countries_up_to_belarus_use_the_guard(self):
        for next_type in range(1, 11):
            with self.subTest(next_type=next_type):
                self.assertIs(self.select(next_type=next_type), self.alternative)

        for next_type in (0, 11, 16, 10.0, "10", True):
            with self.subTest(next_type=next_type):
                self.assertIsNone(self.select(next_type=next_type))

    def test_selected_contact_can_protect_each_second_chain_pair(self):
        for country_type in (11, 12, 13):
            with self.subTest(country_type=country_type):
                pieces = [
                    piece("kazakhstan", 14),
                    piece("pair-left", country_type, -1.0),
                    piece("pair-right", country_type, 1.0),
                    piece("lithuania", 5, 2.0),
                ]
                selected = candidate(0.0, hit_id="pair-left")
                alternative = candidate(
                    1.6,
                    hit_id="lithuania",
                    risk=1.7,
                    margin=1.0,
                )
                self.assertIs(
                    self.select(
                        pieces=pieces,
                        results=[selected, alternative],
                    ),
                    alternative,
                )

    def test_requires_exactly_one_kazakhstan_and_no_russia_or_soviet(self):
        invalid_boards = {
            "no_kazakhstan": [p for p in self.pieces if p["type"] != 14],
            "two_kazakhstans": self.pieces + [piece("kazakhstan-two", 14)],
            "russia_present": self.pieces + [piece("russia", 15)],
            "soviet_present": self.pieces + [piece("soviet", 16)],
        }
        for case, pieces in invalid_boards.items():
            with self.subTest(case=case):
                self.assertIsNone(self.select(pieces=pieces))

    def test_requires_a_paired_chain_country(self):
        pieces = [
            piece("kazakhstan", 14),
            piece("uzbekistan", 11),
            piece("turkmenistan", 12),
            piece("lithuania", 5),
        ]
        self.assertIsNone(self.select(pieces=pieces))

    def test_safe_immediate_merge_keeps_normal_priority(self):
        for grade in ("DIRECT", "NEAR"):
            with self.subTest(grade=grade):
                immediate = candidate(
                    -2.0,
                    hit_id="lithuania",
                    risk=1.5,
                    margin=1.2,
                    grade=grade,
                )
                self.assertIsNone(
                    self.select(
                        results=[self.selected, self.alternative, immediate]
                    )
                )

    def test_selected_lane_must_match_final_explicit_safe_no_merge(self):
        self.assertIsNone(self.select(chosen_x=0.2))

        for mutation in (
            {"merge_grade": "FAR"},
            {"crosses_deadline": True},
            {"merge_result_crosses_deadline": True},
            {"deadline_margin": -0.001},
        ):
            with self.subTest(mutation=mutation):
                selected = dict(self.selected, **mutation)
                self.assertIsNone(
                    self.select(results=[selected, self.alternative])
                )

    def test_improvement_boundaries_are_inclusive_at_point_two(self):
        self.assertIs(self.select(), self.alternative)

        weak_risk = candidate(
            1.6,
            hit_id="lithuania",
            risk=1.801,
            margin=1.0,
        )
        self.assertIsNone(self.select(results=[self.selected, weak_risk]))

        weak_margin = candidate(
            1.6,
            hit_id="lithuania",
            risk=1.7,
            margin=0.899,
        )
        self.assertIsNone(self.select(results=[self.selected, weak_margin]))

    def test_absolute_deadline_margin_is_inclusive_at_point_five(self):
        selected = candidate(
            0.0,
            hit_id="uzbekistan-left",
            risk=2.0,
            margin=0.30,
        )
        at_boundary = candidate(
            1.6,
            hit_id="lithuania",
            risk=1.8,
            margin=0.50,
        )
        below_boundary = candidate(
            1.6,
            hit_id="lithuania",
            risk=1.7,
            margin=0.499,
        )
        self.assertIs(
            self.select(results=[selected, at_boundary]),
            at_boundary,
        )
        self.assertIsNone(self.select(results=[selected, below_boundary]))

    def test_alternative_must_hit_a_real_lower_country(self):
        extra_pieces = self.pieces + [piece("belarus", 10)]
        for hit_id, expected in (
            ("lithuania", True),
            ("belarus", True),
            ("kazakhstan", False),
            ("uzbekistan-left", False),
            ("missing", False),
        ):
            with self.subTest(hit_id=hit_id):
                alternative = dict(self.alternative, landing_hit_id=hit_id)
                result = self.select(
                    pieces=extra_pieces,
                    results=[self.selected, alternative],
                )
                self.assertEqual(result is alternative, expected)

    def test_alternative_must_be_explicit_safe_no_merge(self):
        for mutation in (
            {"merge_grade": "FAR"},
            {"crosses_deadline": True},
            {"merge_result_crosses_deadline": True},
        ):
            with self.subTest(mutation=mutation):
                alternative = dict(self.alternative, **mutation)
                self.assertIsNone(
                    self.select(results=[self.selected, alternative])
                )

    def test_malformed_or_nonfinite_analysis_fails_closed(self):
        required_fields = (
            "x",
            "landing_y",
            "landing_hit_id",
            "risk_top_y_after_drop",
            "deadline_margin",
            "merge_grade",
            "crosses_deadline",
            "merge_result_crosses_deadline",
        )
        for candidate_index in (0, 1):
            for field in required_fields:
                with self.subTest(candidate_index=candidate_index, field=field):
                    results = [dict(self.selected), dict(self.alternative)]
                    results[candidate_index].pop(field)
                    self.assertIsNone(self.select(results=results))

            for field in (
                "x",
                "landing_y",
                "risk_top_y_after_drop",
                "deadline_margin",
            ):
                for bad_value in (math.nan, math.inf, -math.inf, True):
                    with self.subTest(
                        candidate_index=candidate_index,
                        field=field,
                        bad_value=bad_value,
                    ):
                        results = [dict(self.selected), dict(self.alternative)]
                        results[candidate_index][field] = bad_value
                        self.assertIsNone(self.select(results=results))

    def test_duplicate_candidate_x_fails_closed(self):
        duplicate = dict(self.alternative, risk_top_y_after_drop=1.0)
        self.assertIsNone(
            self.select(results=[self.selected, self.alternative, duplicate])
        )

    def test_malformed_or_duplicate_piece_data_fails_closed(self):
        malformed_boards = [
            self.pieces + [dict(self.pieces[0])],
            [dict(self.pieces[0], type=14.0)] + self.pieces[1:],
            [dict(self.pieces[0], x=math.nan)] + self.pieces[1:],
            [dict(self.pieces[0], r=0.0)] + self.pieces[1:],
            [dict(self.pieces[0], id=True)] + self.pieces[1:],
        ]
        for index, pieces in enumerate(malformed_boards):
            with self.subTest(index=index):
                self.assertIsNone(self.select(pieces=pieces))

    def test_tie_break_is_deterministic_and_prefers_inner_equal_risk_lane(self):
        inner = dict(self.alternative, x=1.6)
        outer = dict(self.alternative, x=2.8)
        forward = self.select(results=[self.selected, outer, inner])
        reverse = self.select(results=[inner, outer, self.selected])
        self.assertEqual(forward["x"], 1.6)
        self.assertEqual(reverse["x"], 1.6)

    def test_keeps_the_proven_pre_russia_left_boundary(self):
        boundary = candidate(
            -0.991,
            hit_id="lithuania",
            risk=1.7,
            margin=1.0,
        )
        outside = dict(boundary, x=-0.9911)

        self.assertIs(
            self.select(results=[self.selected, boundary]),
            boundary,
        )
        self.assertIsNone(self.select(results=[self.selected, outside]))
        self.assertIsNone(
            self.select(
                results=[dict(self.selected, x=-0.9911), self.alternative],
                chosen_x=-0.9911,
            )
        )

    def test_finalize_decision_changes_only_the_matching_state(self):
        game_state = {"pieces": self.pieces, "next": {"type": 10}}
        analysis = {"results": [self.selected, self.alternative]}
        decision = {"x": 0.0, "reason": "already safe"}
        self.assertEqual(
            strategy.finalize_decision(game_state, analysis, decision),
            {"x": 1.6, "reason": "PRE_RUSSIA_CHAIN_COVER_AVOID"},
        )

        no_pair_state = {
            "pieces": [p for p in self.pieces if p["id"] != "uzbekistan-right"],
            "next": {"type": 10},
        }
        self.assertIs(
            strategy.finalize_decision(no_pair_state, analysis, decision),
            decision,
        )

    def test_real_kazakhstan_turn_57_moves_belarus_off_paired_uzbekistan(self):
        fixture_path = (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "pre_russia_chain_cover_turn57.json"
        )
        game_state = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertIn("rounded history board", game_state["source"])
        self.assertTrue(
            {"5", "10", "11"}.issubset(game_state["shapes"]),
            "incoming Belarus and both collision countries need live polygons",
        )

        analysis = strategy_runner.build_analysis(copy.deepcopy(game_state))
        strategy_runner.enrich_game_state_deadline_fields(game_state, analysis)
        previous_decision = strategy.decide(
            copy.deepcopy(game_state), copy.deepcopy(analysis)
        )
        self.assertEqual(previous_decision["x"], 0.0)
        enforced_decision = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(previous_decision),
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
        )
        self.assertEqual(enforced_decision["x"], 0.0)

        covered = min(
            analysis["results"], key=lambda result: abs(result["x"] - 0.0)
        )
        open_lane = min(
            analysis["results"], key=lambda result: abs(result["x"] - 1.6)
        )
        self.assertEqual(covered["landing_hit_id"], 86)
        self.assertEqual(open_lane["landing_hit_id"], 83)
        self.assertAlmostEqual(covered["risk_top_y_after_drop"], 2.310)
        self.assertAlmostEqual(covered["deadline_margin"], 1.070)
        self.assertAlmostEqual(open_lane["risk_top_y_after_drop"], 1.176)
        self.assertAlmostEqual(open_lane["deadline_margin"], 2.204)

        final_decision = strategy_runner.apply_strategy_final_decision(
            strategy,
            copy.deepcopy(enforced_decision),
            copy.deepcopy(analysis),
            copy.deepcopy(game_state),
        )
        self.assertEqual(
            final_decision,
            {"x": 1.6, "reason": "PRE_RUSSIA_CHAIN_COVER_AVOID"},
        )


class StrategyFinalDecisionHookTest(unittest.TestCase):
    def setUp(self):
        self.decision = {"x": 0.0, "reason": "safe"}

    def apply(self, finalizer):
        return strategy_runner.apply_strategy_final_decision(
            SimpleNamespace(finalize_decision=finalizer),
            self.decision,
            {"results": []},
            {"pieces": []},
        )

    def test_absent_hook_keeps_enforced_decision(self):
        self.assertIs(
            strategy_runner.apply_strategy_final_decision(
                SimpleNamespace(), self.decision, {}, {}
            ),
            self.decision,
        )

    def test_valid_hook_result_is_applied(self):
        self.assertEqual(
            self.apply(lambda *_: {"x": 1.25, "reason": "postcondition"}),
            {"x": 1.25, "reason": "postcondition"},
        )

    def test_invalid_or_failing_hook_keeps_enforced_decision(self):
        def raising(*_):
            raise RuntimeError("broken")

        for finalizer in (
            raising,
            lambda *_: None,
            lambda *_: {"reason": "missing x"},
            lambda *_: {"x": math.nan},
            lambda *_: {"x": 3.01},
            lambda *_: {"x": True},
        ):
            with self.subTest(finalizer=finalizer):
                self.assertIs(self.apply(finalizer), self.decision)

    def test_hook_cannot_restore_a_deadline_crossing_lane(self):
        analysis = {
            "deadline": {
                "deadline_y": 3.38,
                "top_edge_y": 0.0,
                "deadline_crossed": False,
                "danger_piece_count": 0,
            },
            "reactor": {"reactive_pairs": []},
            "results": [
                candidate(0.0, hit_id="safe", risk=1.0, margin=2.0),
                candidate(
                    2.0,
                    hit_id="crossing",
                    risk=3.8,
                    margin=-0.4,
                    crosses=True,
                ),
            ],
        }
        final_decision = strategy_runner.apply_strategy_final_decision(
            SimpleNamespace(
                finalize_decision=lambda *_: {"x": 2.0, "reason": "unsafe hook"}
            ),
            {"x": 0.0, "reason": "safe"},
            analysis,
            {"pieces": [], "next": {"type": 10}},
        )

        self.assertEqual(final_decision["x"], 0.0)
        self.assertIn("RUNTIME_DEADLINE_SAFETY_OVERRIDE", final_decision["reason"])

    def test_hook_output_is_rechecked_by_independent_geometry(self):
        analysis = {
            "deadline": {
                "deadline_y": 3.38,
                "top_edge_y": 2.37,
                "deadline_crossed": False,
                "danger_piece_count": 0,
            },
            "reactor": {"reactive_pairs": []},
            "results": [
                {
                    "x": 0.25,
                    "crosses_deadline": False,
                    "merge_grade": "NO",
                    "risk_top_y_after_drop": 3.10,
                },
                {
                    "x": 3.0,
                    "crosses_deadline": False,
                    "merge_grade": "NO",
                    "risk_top_y_after_drop": 3.18,
                },
            ],
        }
        game_state = {
            "pieces": [
                {"id": 1, "type": 14, "x": 0.2, "y": 2.70, "r": 1.385},
                {"id": 2, "type": 9, "x": -1.5, "y": 1.30, "r": 0.746},
            ],
            "next": {"type": 10, "r": 0.846},
        }
        final_decision = strategy_runner.apply_strategy_final_decision(
            SimpleNamespace(
                finalize_decision=lambda *_: {"x": 0.25, "reason": "unsafe hook"}
            ),
            {"x": 3.0, "reason": "safe"},
            analysis,
            game_state,
        )

        self.assertEqual(final_decision["x"], 3.0)
        self.assertIn("geometry_underestimate_postcondition", final_decision["reason"])


if __name__ == "__main__":
    unittest.main()
