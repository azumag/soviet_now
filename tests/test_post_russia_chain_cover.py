from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock


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
    margin: float = 0.8,
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


class PostRussiaChainCoverAvoidanceTest(unittest.TestCase):
    def setUp(self):
        self.pieces = [
            piece("russia", 15, -1.5),
            piece("uzbekistan-left", 11, -0.5),
            piece("uzbekistan-right", 11, 0.8),
            piece("kyrgyzstan", 9, 2.5),
        ]
        self.selected = candidate(
            0.0,
            hit_id="uzbekistan-left",
            risk=2.0,
            margin=0.8,
        )
        self.alternative = candidate(
            2.7,
            hit_id="kyrgyzstan",
            risk=1.8,
            margin=1.0,
        )

    def select(
        self,
        *,
        pieces=None,
        results=None,
        next_type=2,
        selected=None,
        chosen_x=0.0,
    ):
        candidates = [self.selected, self.alternative] if results is None else results
        return strategy._select_post_russia_chain_cover_avoidance(
            self.pieces if pieces is None else pieces,
            candidates,
            next_type,
            self.selected if selected is None else selected,
            chosen_x,
        )

    def test_uses_materially_safer_real_lane_after_russia(self):
        self.assertIs(self.select(), self.alternative)

    def test_only_low_incoming_countries_use_the_guard(self):
        for next_type in range(1, 8):
            with self.subTest(next_type=next_type):
                self.assertIs(self.select(next_type=next_type), self.alternative)

        for next_type in (0, 8, 16, 2.0, "2", True):
            with self.subTest(next_type=next_type):
                self.assertIsNone(self.select(next_type=next_type))

    def test_selected_contact_can_protect_each_high_chain_country_pair(self):
        for country_type in (11, 12, 13, 14):
            with self.subTest(country_type=country_type):
                pieces = [
                    piece("russia", 15),
                    piece("paired-left", country_type, -1.0),
                    piece("paired-right", country_type, 1.0),
                    piece("kyrgyzstan", 9, 2.5),
                ]
                selected = candidate(0.0, hit_id="paired-left")
                alternative = candidate(
                    2.7,
                    hit_id="kyrgyzstan",
                    risk=1.7,
                    margin=1.1,
                )
                self.assertIs(
                    self.select(
                        pieces=pieces,
                        results=[selected, alternative],
                        selected=selected,
                    ),
                    alternative,
                )

    def test_requires_exactly_one_russia_and_no_soviet(self):
        invalid_boards = {
            "no_russia": [p for p in self.pieces if p["type"] != 15],
            "two_russias": self.pieces + [piece("russia-two", 15)],
            "soviet_present": self.pieces + [piece("soviet", 16)],
        }
        for case, pieces in invalid_boards.items():
            with self.subTest(case=case):
                self.assertIsNone(self.select(pieces=pieces))

    def test_selected_hit_must_itself_be_paired_high_material(self):
        pieces = self.pieces + [
            piece("turkmenistan-left", 12),
            piece("turkmenistan-right", 12),
        ]
        selected = candidate(0.0, hit_id="kyrgyzstan")
        self.assertIsNone(
            self.select(
                pieces=pieces,
                results=[selected, self.alternative],
                selected=selected,
            )
        )

    def test_safe_immediate_merge_keeps_normal_priority(self):
        for grade in ("DIRECT", "NEAR"):
            with self.subTest(grade=grade):
                immediate = candidate(
                    -2.0,
                    hit_id="kyrgyzstan",
                    risk=1.5,
                    margin=1.2,
                    grade=grade,
                )
                self.assertIsNone(
                    self.select(
                        results=[self.selected, self.alternative, immediate]
                    )
                )

    def test_selected_lane_must_be_the_final_explicit_safe_no_merge(self):
        unrelated = candidate(
            0.1,
            hit_id="uzbekistan-left",
            risk=2.1,
            margin=0.7,
        )
        self.assertIsNone(
            self.select(
                results=[self.selected, self.alternative, unrelated],
                selected=unrelated,
                chosen_x=0.0,
            )
        )
        self.assertIsNone(self.select(selected=dict(self.selected)))

        for field in ("crosses_deadline", "merge_result_crosses_deadline"):
            with self.subTest(field=field):
                selected = dict(self.selected)
                selected[field] = True
                self.assertIsNone(
                    self.select(
                        results=[selected, self.alternative],
                        selected=selected,
                    )
                )

        selected = dict(self.selected, merge_grade="FAR")
        self.assertIsNone(
            self.select(
                results=[selected, self.alternative],
                selected=selected,
            )
        )

    def test_improvement_boundaries_are_inclusive_at_point_two(self):
        self.assertIs(self.select(), self.alternative)

        weak_risk = candidate(
            2.7,
            hit_id="kyrgyzstan",
            risk=1.801,
            margin=1.1,
        )
        self.assertIsNone(self.select(results=[self.selected, weak_risk]))

        weak_margin = candidate(
            2.7,
            hit_id="kyrgyzstan",
            risk=1.7,
            margin=0.999,
        )
        self.assertIsNone(self.select(results=[self.selected, weak_margin]))

    def test_absolute_deadline_margin_is_inclusive_at_point_seven(self):
        selected = candidate(
            0.0,
            hit_id="uzbekistan-left",
            risk=2.0,
            margin=0.49,
        )
        at_boundary = candidate(
            2.7,
            hit_id="kyrgyzstan",
            risk=1.8,
            margin=0.70,
        )
        below_boundary = candidate(
            2.7,
            hit_id="kyrgyzstan",
            risk=1.7,
            margin=0.699,
        )
        self.assertIs(
            self.select(
                results=[selected, at_boundary],
                selected=selected,
            ),
            at_boundary,
        )
        self.assertIsNone(
            self.select(
                results=[selected, below_boundary],
                selected=selected,
            )
        )

    def test_alternative_must_hit_real_non_russia_nonpaired_material(self):
        for hit_id in (
            "russia",
            "uzbekistan-left",
            "uzbekistan-right",
            "missing-piece",
        ):
            with self.subTest(hit_id=hit_id):
                alternative = dict(self.alternative, landing_hit_id=hit_id)
                self.assertIsNone(
                    self.select(results=[self.selected, alternative])
                )

    def test_alternative_must_be_explicit_safe_no_merge(self):
        mutations = (
            {"merge_grade": "FAR"},
            {"crosses_deadline": True},
            {"merge_result_crosses_deadline": True},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                alternative = dict(self.alternative, **mutation)
                self.assertIsNone(
                    self.select(results=[self.selected, alternative])
                )

    def test_malformed_or_nonfinite_analysis_fails_closed(self):
        for candidate_index in (0, 1):
            for field in (
                "x",
                "landing_y",
                "landing_hit_id",
                "risk_top_y_after_drop",
                "deadline_margin",
                "merge_grade",
                "crosses_deadline",
                "merge_result_crosses_deadline",
            ):
                with self.subTest(candidate_index=candidate_index, field=field):
                    results = [dict(self.selected), dict(self.alternative)]
                    results[candidate_index].pop(field)
                    self.assertIsNone(
                        self.select(results=results, selected=results[0])
                    )

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
                        self.assertIsNone(
                            self.select(results=results, selected=results[0])
                        )

    def test_duplicate_candidate_x_fails_closed(self):
        conflicting = dict(
            self.alternative,
            crosses_deadline=True,
            risk_top_y_after_drop=9.0,
        )
        self.assertIsNone(
            self.select(results=[self.selected, self.alternative, conflicting])
        )

    def test_malformed_or_duplicate_piece_data_fails_closed(self):
        malformed_boards = [
            self.pieces + [dict(self.pieces[0])],
            [dict(self.pieces[0], type=15.0)] + self.pieces[1:],
            [dict(self.pieces[0], x=math.nan)] + self.pieces[1:],
            [dict(self.pieces[0], r=0.0)] + self.pieces[1:],
            [dict(self.pieces[0], id=True)] + self.pieces[1:],
        ]
        for index, pieces in enumerate(malformed_boards):
            with self.subTest(index=index):
                self.assertIsNone(self.select(pieces=pieces))

    def test_tie_break_is_deterministic_and_prefers_inner_equal_risk_lane(self):
        inner = dict(self.alternative, x=2.7)
        outer = dict(self.alternative, x=3.0)
        forward = self.select(results=[self.selected, outer, inner])
        reverse = self.select(results=[inner, outer, self.selected])
        self.assertIs(forward, inner)
        self.assertIs(reverse, inner)

    def test_real_russia_turn_130_moves_moldova_off_paired_uzbekistan(self):
        fixture_path = (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "post_russia_chain_cover_turn130.json"
        )
        game_state = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(
            game_state["source"],
            "game_history/20260824_081228_score3288.jsonl turn 130",
        )
        self.assertTrue(
            {"2", "9", "11"}.issubset(game_state["shapes"]),
            "incoming Moldova and both collision countries need real polygons",
        )

        analysis = strategy_runner.build_analysis(game_state)
        strategy_runner.enrich_game_state_deadline_fields(game_state, analysis)
        with mock.patch.object(
            strategy,
            "_select_post_russia_chain_cover_avoidance",
            return_value=None,
        ):
            previous_decision = strategy.decide(
                copy.deepcopy(game_state), copy.deepcopy(analysis)
            )
        self.assertEqual(previous_decision["x"], -0.55)

        covered = min(
            analysis["results"], key=lambda result: abs(result["x"] - (-0.55))
        )
        open_lane = min(
            analysis["results"], key=lambda result: abs(result["x"] - 2.7)
        )
        self.assertEqual(covered["landing_hit_id"], 230)
        self.assertEqual(open_lane["landing_hit_id"], 84)
        self.assertAlmostEqual(
            covered["risk_top_y_after_drop"],
            game_state["observedDecision"]["riskTopYAfterDrop"],
            delta=0.01,
        )
        self.assertAlmostEqual(open_lane["risk_top_y_after_drop"], 2.654)
        self.assertAlmostEqual(open_lane["deadline_margin"], 0.726)
        self.assertGreaterEqual(
            covered["risk_top_y_after_drop"]
            - open_lane["risk_top_y_after_drop"],
            0.20,
        )
        self.assertGreaterEqual(
            open_lane["deadline_margin"] - covered["deadline_margin"],
            0.20,
        )

        decision = strategy.decide(game_state, analysis)
        self.assertEqual(
            decision,
            {"x": 2.7, "reason": "POST_RUSSIA_CHAIN_COVER_AVOID"},
        )
        final_decision = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(decision), copy.deepcopy(analysis), copy.deepcopy(game_state)
        )
        self.assertEqual(final_decision, decision)


if __name__ == "__main__":
    unittest.main()
