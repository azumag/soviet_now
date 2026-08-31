from __future__ import annotations

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
    return {"id": piece_id, "type": piece_type, "x": x, "y": -2.0, "r": 0.5}


def candidate(
    x: float,
    *,
    risk: float = 1.0,
    hit_id: str | None = "low",
    grade: str = "NO",
    crosses: bool = False,
    merge_crosses: bool = False,
) -> dict:
    return {
        "x": x,
        "landing_y": -1.0,
        "drift_x": 0.0,
        "drift_unc": 0.0,
        "merge_grade": grade,
        "crosses_deadline": crosses,
        "merge_result_crosses_deadline": merge_crosses,
        "risk_top_y_after_drop": risk,
        "landing_hit_id": hit_id,
        "deadline_margin": 1.0,
        "merges": [],
    }


class FirstRussiaUzbekistanLaneGuardTest(unittest.TestCase):
    def setUp(self):
        self.pieces = [
            piece("kazakhstan", 14, -2.0),
            piece("turkmenistan", 12, 1.5),
            piece("uzbekistan", 11, -1.0),
            piece("low", 8, 0.5),
        ]
        self.selected = candidate(
            -1.0, risk=2.647, hit_id="uzbekistan"
        )
        self.alternative = candidate(0.35, risk=2.564, hit_id="low")

    def select(
        self,
        *,
        pieces=None,
        results=None,
        next_type=1,
        selected=None,
        chosen_x=-1.0,
    ):
        candidates = [self.selected, self.alternative] if results is None else results
        return strategy._select_first_russia_t11_lane_avoidance(
            self.pieces if pieces is None else pieces,
            candidates,
            next_type,
            self.selected if selected is None else selected,
            chosen_x,
        )

    def test_uses_safe_low_country_lane_in_exact_first_russia_position(self):
        for next_type in range(1, 10):
            with self.subTest(next_type=next_type):
                self.assertIs(self.select(next_type=next_type), self.alternative)

    def test_does_not_apply_when_next_country_is_belarus(self):
        self.assertIsNone(self.select(next_type=10))

    def test_requires_exact_high_country_inventory(self):
        invalid_positions = {
            "two_uzbekistans": self.pieces + [piece("uzbekistan-2", 11)],
            "ukraine_present": self.pieces + [piece("ukraine", 13)],
            "no_kazakhstan": [p for p in self.pieces if p["type"] != 14],
            "russia_present": self.pieces + [piece("russia", 15)],
            "no_turkmenistan": [p for p in self.pieces if p["type"] != 12],
            "soviet_present": self.pieces + [piece("soviet", 16)],
        }

        for case, pieces in invalid_positions.items():
            with self.subTest(case=case):
                self.assertIsNone(self.select(pieces=pieces))

    def test_selected_lane_must_be_explicit_safe_no_merge_on_uzbekistan(self):
        for grade in ("DIRECT", "NEAR"):
            with self.subTest(grade=grade):
                selected = candidate(
                    -0.991,
                    risk=2.647,
                    hit_id="uzbekistan",
                    grade=grade,
                )
                self.assertIsNone(
                    self.select(
                        results=[selected, self.alternative], selected=selected
                    )
                )

    def test_nearby_uzbekistan_candidate_is_not_mistaken_for_selected_lane(self):
        actual_selected = candidate(0.0, risk=2.5, hit_id="low")
        nearby_uzbekistan = candidate(0.15, risk=2.4, hit_id="uzbekistan")
        alternative = candidate(0.35, risk=2.3, hit_id="low")

        self.assertIsNone(
            self.select(
                results=[actual_selected, nearby_uzbekistan, alternative],
                selected=actual_selected,
                chosen_x=0.0,
            )
        )

        for field in ("crosses_deadline", "merge_result_crosses_deadline"):
            with self.subTest(field=field):
                selected = dict(self.selected)
                selected[field] = True
                self.assertIsNone(
                    self.select(
                        results=[selected, self.alternative], selected=selected
                    )
                )

    def test_alternative_must_be_explicit_safe_no_merge(self):
        for grade in ("DIRECT", "NEAR"):
            with self.subTest(grade=grade):
                alternative = dict(self.alternative, merge_grade=grade)
                self.assertIsNone(self.select(results=[self.selected, alternative]))

        for field in ("crosses_deadline", "merge_result_crosses_deadline"):
            with self.subTest(field=field):
                alternative = dict(self.alternative)
                alternative[field] = True
                self.assertIsNone(self.select(results=[self.selected, alternative]))

    def test_any_safe_immediate_merge_keeps_normal_merge_priority(self):
        immediate = candidate(1.2, risk=2.0, hit_id="low", grade="DIRECT")
        self.assertIsNone(
            self.select(results=[self.selected, self.alternative, immediate])
        )
        for missing_field in ("landing_hit_id", "risk_top_y_after_drop"):
            with self.subTest(missing_field=missing_field):
                incomplete_immediate = dict(immediate)
                incomplete_immediate.pop(missing_field)
                self.assertIsNone(
                    self.select(
                        results=[
                            self.selected,
                            self.alternative,
                            incomplete_immediate,
                        ]
                    )
                )

    def test_missing_or_nonfinite_hit_and_risk_data_fail_closed(self):
        invalid_result_sets = []
        for candidate_index in (0, 1):
            for field in ("landing_hit_id", "risk_top_y_after_drop"):
                results = [dict(self.selected), dict(self.alternative)]
                results[candidate_index].pop(field)
                invalid_result_sets.append(
                    (f"candidate_{candidate_index}_missing_{field}", results)
                )

            results = [dict(self.selected), dict(self.alternative)]
            results[candidate_index]["landing_hit_id"] = math.nan
            invalid_result_sets.append(
                (f"candidate_{candidate_index}_nan_landing_hit_id", results)
            )

            for invalid_risk in (math.nan, math.inf, -math.inf):
                results = [dict(self.selected), dict(self.alternative)]
                results[candidate_index]["risk_top_y_after_drop"] = invalid_risk
                invalid_result_sets.append(
                    (f"candidate_{candidate_index}_risk_{invalid_risk}", results)
                )

            for field in ("x", "risk_top_y_after_drop"):
                results = [dict(self.selected), dict(self.alternative)]
                results[candidate_index][field] = True
                invalid_result_sets.append(
                    (f"candidate_{candidate_index}_bool_{field}", results)
                )

        for case, results in invalid_result_sets:
            with self.subTest(case=case):
                self.assertIsNone(self.select(results=results, selected=results[0]))

        malformed_pieces = [dict(p) for p in self.pieces]
        malformed_pieces[1]["id"] = []
        self.assertIsNone(self.select(pieces=malformed_pieces))

        for invalid_id in ("   ", 1.0, math.inf, True):
            with self.subTest(invalid_id=invalid_id):
                malformed_pieces = [dict(p) for p in self.pieces]
                malformed_pieces[1]["id"] = invalid_id
                self.assertIsNone(self.select(pieces=malformed_pieces))

    def test_high_country_type_ids_must_be_real_integers(self):
        for invalid_type in (11.0, "11", True):
            with self.subTest(invalid_type=invalid_type):
                malformed_pieces = [dict(p) for p in self.pieces]
                malformed_pieces[2]["type"] = invalid_type
                alternative = candidate(
                    0.35,
                    risk=2.564,
                    hit_id="uzbekistan",
                )
                self.assertIsNone(
                    self.select(
                        pieces=malformed_pieces,
                        results=[self.selected, alternative],
                    )
                )

    def test_every_country_type_must_be_in_the_real_game_range(self):
        for invalid_type in (0, -1, 17):
            with self.subTest(invalid_type=invalid_type):
                malformed_pieces = self.pieces + [
                    piece(f"invalid-{invalid_type}", invalid_type)
                ]
                self.assertIsNone(self.select(pieces=malformed_pieces))

    def test_risk_tolerance_includes_point_150_but_excludes_point_151(self):
        selected = candidate(-1.0, risk=1.0, hit_id="uzbekistan")
        at_boundary = candidate(0.35, risk=1.150, hit_id="low")
        over_boundary = candidate(0.35, risk=1.151, hit_id="low")

        self.assertIs(
            self.select(
                results=[selected, at_boundary], selected=selected, chosen_x=-1.0
            ),
            at_boundary,
        )
        self.assertIsNone(
            self.select(
                results=[selected, over_boundary], selected=selected, chosen_x=-1.0
            )
        )

    def test_alternative_cannot_land_on_high_country_chain(self):
        for hit_id in ("uzbekistan", "turkmenistan", "kazakhstan"):
            with self.subTest(hit_id=hit_id):
                alternative = candidate(0.35, risk=2.564, hit_id=hit_id)
                self.assertIsNone(self.select(results=[self.selected, alternative]))

    def test_alternative_may_use_the_left_board_but_not_beyond_the_wall(self):
        # v733: the pre-Russia drop range is the whole board; a left-side
        # alternative is a legal replacement, anything past the wall is not.
        left = candidate(-0.992, risk=2.0, hit_id="low")
        self.assertIs(self.select(results=[self.selected, left]), left)
        beyond = candidate(-3.0002, risk=2.0, hit_id="low")
        self.assertIsNone(self.select(results=[self.selected, beyond]))

    def test_selected_analysis_must_remain_close_to_the_clipped_drop(self):
        far_selected = candidate(-1.05, risk=2.647, hit_id="uzbekistan")
        self.assertIsNone(
            self.select(
                results=[far_selected, self.alternative],
                selected=far_selected,
            )
        )

    def test_tie_break_is_deterministic_when_candidates_are_reversed(self):
        selected = candidate(0.0, risk=1.0, hit_id="uzbekistan")
        left = candidate(-0.5, risk=0.9, hit_id="low")
        right = candidate(0.5, risk=0.9, hit_id="low")

        forward = self.select(
            results=[selected, right, left], selected=selected, chosen_x=0.0
        )
        reverse = self.select(
            results=[left, right, selected], selected=selected, chosen_x=0.0
        )

        self.assertIs(forward, left)
        self.assertIs(reverse, left)

    def test_real_turn_67_state_uses_the_open_lane_without_mocks(self):
        fixture_path = (
            REPO_ROOT
            / "tests"
            / "fixtures"
            / "first_russia_uzbekistan_turn67.json"
        )
        game_state = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(
            game_state["source"],
            "game_history/20260824_041652_score1403.jsonl turn 67",
        )

        analysis = strategy_runner.build_analysis(game_state)
        strategy_runner.enrich_game_state_deadline_fields(game_state, analysis)
        covered_lane = min(
            analysis["results"], key=lambda result: abs(result["x"] - (-1.0))
        )
        open_lane = min(
            analysis["results"], key=lambda result: abs(result["x"] - 0.35)
        )

        self.assertEqual(covered_lane["landing_hit_id"], 101)
        self.assertAlmostEqual(covered_lane["risk_top_y_after_drop"], 2.647)
        self.assertEqual(open_lane["landing_hit_id"], 102)
        self.assertAlmostEqual(open_lane["risk_top_y_after_drop"], 2.564)
        self.assertEqual(
            strategy.decide(game_state, analysis),
            {
                "x": 0.35,
                "reason": "FIRST_RUSSIA_T11_LANE_COVER_AVOID",
            },
        )

    def test_decide_calls_guard_after_final_drop_range_clip(self):
        events = []
        replacement = candidate(0.35, risk=0.9, hit_id="low")

        def clip(value, has_russia, fallback=False):
            events.append(("clip", fallback))
            return 0.1234

        def guard(pieces, results, next_type, selected_candidate, chosen_x):
            self.assertTrue(
                any(selected_candidate is candidate for candidate in analysis["results"])
            )
            events.append(("guard", chosen_x))
            return replacement

        game_state = {
            "pieces": self.pieces,
            "next": {"type": 1, "r": 0.25},
            "nextNext": {"type": 2, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": [
                candidate(-0.9, risk=1.0, hit_id="uzbekistan"),
                candidate(0.4, risk=0.9, hit_id="low"),
            ],
            "reactor": {
                "deadline_margin": 2.0,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        with (
            mock.patch.object(strategy, "_clip_final_drop_x", side_effect=clip),
            mock.patch.object(
                strategy,
                "_select_first_russia_t11_lane_avoidance",
                side_effect=guard,
            ),
        ):
            decision = strategy.decide(game_state, analysis)

        self.assertEqual(events, [("clip", False), ("guard", 0.1234)])
        self.assertEqual(
            decision,
            {"x": 0.35, "reason": "FIRST_RUSSIA_T11_LANE_COVER_AVOID"},
        )


if __name__ == "__main__":
    unittest.main()
