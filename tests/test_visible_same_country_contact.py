from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import strategy
import strategy_runner


def piece(
    piece_id: object,
    piece_type: int,
    x: float,
    *,
    y: float = -1.0,
    radius: float = 0.5,
) -> dict:
    return {
        "id": piece_id,
        "type": piece_type,
        "x": x,
        "y": y,
        "r": radius,
    }


def candidate(
    x: float = 1.0,
    *,
    target_id: object = "target",
    target_x: float = 1.0,
    hit_id: object | None = None,
    ratio: float = 0.80,
    margin: float = 1.20,
    target_top: float = -0.10,
    grade: str = "NO",
    crosses: bool = False,
    merge_crosses: bool = False,
    target_crosses: bool = False,
    target_danger: bool = False,
    risk: float = 0.40,
) -> dict:
    return {
        "x": x,
        "landing_y": -0.5,
        "landing_hit_id": target_id if hit_id is None else hit_id,
        "risk_top_y_after_drop": risk,
        "deadline_margin": margin,
        "crosses_deadline": crosses,
        "merge_result_crosses_deadline": merge_crosses,
        "merge_grade": grade,
        "merges": [
            {
                "id": target_id,
                "x": target_x,
                "dist": ratio,
                "contact_r": 1.0,
                "grade": "NO",
                "target_top_y": target_top,
                "target_crosses_deadline": target_crosses,
                "target_is_danger": target_danger,
            }
        ],
    }


class VisibleSameCountryContactTest(unittest.TestCase):
    def setUp(self):
        self.pieces = [
            piece("target", 9, 1.0, radius=1.0),
            piece("support", 12, -0.2, y=-2.0, radius=1.2),
            piece("other", 3, -2.0),
        ]
        self.reactor = {
            "deadline_margin": 2.0,
            "top_edge_y": 0.0,
            "danger_piece_count": 0,
            "deadline_crossed": False,
        }

    def select(
        self,
        *,
        pieces=None,
        results=None,
        next_type=9,
        reactor=None,
        chosen_x=-1.0,
    ):
        return strategy._select_visible_same_country_contact(
            self.pieces if pieces is None else pieces,
            [candidate()] if results is None else results,
            next_type,
            self.reactor if reactor is None else reactor,
            chosen_x,
        )

    def test_selects_analyzer_false_negative_with_real_target_collision(self):
        contact = candidate()
        self.assertIs(self.select(results=[contact]), contact)

    def test_general_contact_ratio_boundary_is_closed(self):
        at_boundary = candidate(ratio=0.930)
        over_boundary = candidate(ratio=0.931)
        self.assertIs(self.select(results=[at_boundary]), at_boundary)
        self.assertIsNone(self.select(results=[over_boundary]))

    def test_wall_contact_ratio_boundary_is_closed(self):
        wall_pieces = [
            piece("target", 9, 2.75, radius=1.0),
            piece("support", 12, -0.2, y=-2.0, radius=1.2),
        ]
        at_boundary = candidate(x=2.75, target_x=2.75, ratio=1.000)
        over_boundary = candidate(x=2.75, target_x=2.75, ratio=1.001)
        self.assertIs(
            self.select(pieces=wall_pieces, results=[at_boundary]), at_boundary
        )
        self.assertIsNone(self.select(pieces=wall_pieces, results=[over_boundary]))

    def test_candidate_deadline_margin_boundary_is_closed(self):
        at_boundary = candidate(margin=0.750)
        below_boundary = candidate(margin=0.749)
        self.assertIs(self.select(results=[at_boundary]), at_boundary)
        self.assertIsNone(self.select(results=[below_boundary]))

    def test_drop_alignment_boundary_is_closed(self):
        at_boundary = candidate(x=1.060)
        outside = candidate(x=1.061)
        self.assertIs(self.select(results=[at_boundary]), at_boundary)
        self.assertIsNone(self.select(results=[outside]))

    def test_existing_choice_within_point_two_keeps_normal_strategy(self):
        contact = candidate()
        self.assertIsNone(self.select(results=[contact], chosen_x=0.800))
        self.assertIs(self.select(results=[contact], chosen_x=0.799), contact)

    def test_requires_a_calm_board_with_generous_headroom(self):
        invalid_reactors = {
            "reactor_margin": dict(self.reactor, deadline_margin=0.999),
            "candidate_danger": dict(self.reactor, danger_piece_count=1),
            "deadline_crossed": dict(self.reactor, deadline_crossed=True),
        }
        for case, reactor in invalid_reactors.items():
            with self.subTest(case=case):
                self.assertIsNone(self.select(reactor=reactor))

    def test_safe_analyzer_merge_always_wins(self):
        contact = candidate()
        for grade in ("DIRECT", "NEAR"):
            with self.subTest(grade=grade):
                safe_merge = candidate(x=-1.0, grade=grade)
                self.assertIsNone(self.select(results=[contact, safe_merge]))

    def test_russia_and_soviet_boards_remain_owned_by_endgame_policy(self):
        for country_type in (15, 16):
            with self.subTest(country_type=country_type):
                pieces = self.pieces + [
                    piece(f"endgame-{country_type}", country_type, -2.5)
                ]
                self.assertIsNone(self.select(pieces=pieces))

    def test_target_must_be_same_country_safe_and_visibly_exposed(self):
        wrong_country = [
            piece("target", 8, 1.0, radius=1.0),
            self.pieces[1],
            self.pieces[2],
        ]
        self.assertIsNone(self.select(pieces=wrong_country))
        self.assertIsNone(self.select(results=[candidate(target_crosses=True)]))
        self.assertIsNone(self.select(results=[candidate(target_danger=True)]))
        self.assertIsNone(self.select(results=[candidate(target_top=-1.251)]))

    def test_support_collision_uses_tighter_manual_envelope(self):
        supported = candidate(hit_id="support", ratio=0.85, target_top=-0.20)
        self.assertIs(self.select(results=[supported]), supported)

        weak_support_pieces = [dict(item) for item in self.pieces]
        weak_support_pieces[1]["type"] = 10
        self.assertIsNone(
            self.select(pieces=weak_support_pieces, results=[supported])
        )
        self.assertIsNone(
            self.select(
                results=[candidate(hit_id="support", ratio=0.851, target_top=-0.20)]
            )
        )
        self.assertIsNone(
            self.select(
                results=[candidate(hit_id="support", ratio=0.80, target_top=-0.201)]
            )
        )

    def test_wall_support_collision_keeps_measured_slide_contact(self):
        wall_pieces = [
            piece("target", 9, 3.044, y=-2.0, radius=1.0),
            piece("support", 12, 1.9, y=-2.0, radius=1.2),
        ]
        supported = candidate(
            x=3.0,
            target_x=3.044,
            hit_id="support",
            ratio=0.976,
            target_top=-1.20,
        )
        self.assertIs(
            self.select(pieces=wall_pieces, results=[supported]), supported
        )

    def test_selection_is_deterministic_when_results_are_reversed(self):
        pieces = [
            piece("higher", 9, -1.0, radius=1.0),
            piece("lower", 9, 1.0, radius=1.0),
            piece("support", 12, 0.0, y=-2.0, radius=1.2),
        ]
        higher = candidate(
            x=-1.0,
            target_id="higher",
            target_x=-1.0,
            target_top=-0.10,
        )
        lower = candidate(
            x=1.0,
            target_id="lower",
            target_x=1.0,
            target_top=-0.30,
        )
        forward = self.select(
            pieces=pieces, results=[lower, higher], chosen_x=0.0
        )
        reverse = self.select(
            pieces=pieces, results=[higher, lower], chosen_x=0.0
        )
        self.assertIs(forward, higher)
        self.assertIs(reverse, higher)

    def test_malformed_geometry_fails_closed(self):
        malformed_cases = []

        duplicate_ids = [dict(item) for item in self.pieces]
        duplicate_ids[1]["id"] = "target"
        malformed_cases.append(("duplicate_piece_ids", duplicate_ids, [candidate()]))

        for field, value in (
            ("id", True),
            ("type", True),
            ("x", math.nan),
            ("y", math.inf),
            ("r", 0.0),
        ):
            pieces = [dict(item) for item in self.pieces]
            pieces[0][field] = value
            malformed_cases.append((f"piece_{field}", pieces, [candidate()]))

        for field, value in (
            ("x", True),
            ("deadline_margin", math.nan),
            ("risk_top_y_after_drop", math.inf),
            ("crosses_deadline", 0),
            ("merge_result_crosses_deadline", None),
            ("merges", None),
        ):
            result = candidate()
            result[field] = value
            malformed_cases.append((f"candidate_{field}", self.pieces, [result]))

        for case, pieces, results in malformed_cases:
            with self.subTest(case=case):
                self.assertIsNone(self.select(pieces=pieces, results=results))

        self.assertIsNone(self.select(next_type=True))
        self.assertIsNone(self.select(chosen_x=True))
        self.assertIsNone(
            self.select(reactor=dict(self.reactor, top_edge_y=math.nan))
        )

    def test_all_real_manual_contact_merges_survive_runner_safety(self):
        cases = (
            (
                "manual_visible_moldova_turn16.json",
                "manual challenge 2026-08-24 turn 16",
                18,
                0.9766,
                {"score": 139, "piece_count": 11, "created_country": "エストニア"},
            ),
            (
                "manual_visible_kyrgyzstan_turn29.json",
                "manual challenge 2026-08-24 turn 29",
                38,
                0.6159,
                {"score": 421, "piece_count": 14, "created_country": "ベラルーシ"},
            ),
            (
                "manual_visible_tajikistan_turn30.json",
                "manual challenge 2026-08-24 turn 30",
                42,
                0.8161,
                {"score": 457, "piece_count": 14, "created_country": "キルギス"},
            ),
        )

        for fixture_name, source, target_id, ratio, observed_after in cases:
            with self.subTest(fixture=fixture_name):
                fixture_path = REPO_ROOT / "tests" / "fixtures" / fixture_name
                game_state = json.loads(fixture_path.read_text(encoding="utf-8"))
                self.assertEqual(game_state["source"], source)
                self.assertEqual(game_state["observed_after"], observed_after)
                self.assertGreater(observed_after["score"], game_state["score"])

                analysis = strategy_runner.build_analysis(copy.deepcopy(game_state))
                strategy_runner.enrich_game_state_deadline_fields(
                    game_state, analysis
                )
                observed = min(
                    analysis["results"],
                    key=lambda result: abs(
                        result["x"] - game_state["observed_drop_x"]
                    ),
                )
                same_country = next(
                    merge for merge in observed["merges"] if merge["id"] == target_id
                )

                self.assertEqual(observed["merge_grade"], "NO")
                self.assertAlmostEqual(
                    same_country["dist"] / same_country["contact_r"],
                    ratio,
                    places=3,
                )
                decision = strategy.decide(
                    copy.deepcopy(game_state), copy.deepcopy(analysis)
                )
                self.assertEqual(
                    decision,
                    {
                        "x": game_state["observed_drop_x"],
                        "reason": "VISIBLE_SAME_COUNTRY_CONTACT_SHOT",
                    },
                )
                self.assertEqual(
                    strategy_runner.enforce_deadline_safety(
                        copy.deepcopy(decision),
                        copy.deepcopy(analysis),
                        copy.deepcopy(game_state),
                    ),
                    decision,
                )


if __name__ == "__main__":
    unittest.main()
