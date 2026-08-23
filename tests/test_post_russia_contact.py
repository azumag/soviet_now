import copy
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import strategy
import strategy_runner


REPO_ROOT = Path(__file__).resolve().parents[1]


def piece(piece_id, piece_type, x, y, radius):
    return {"id": piece_id, "type": piece_type, "x": x, "y": y, "r": radius}


def candidate(
    x,
    *,
    grade="NO",
    margin=1.0,
    crosses=False,
    landing_y=-1.0,
    landing_hit_id=None,
):
    result = {
        "x": x,
        "landing_y": landing_y,
        "drift_x": 0.0,
        "drift_unc": 0.0,
        "merge_grade": grade,
        "crosses_deadline": crosses,
        "merge_result_crosses_deadline": False,
        "deadline_margin": margin,
        "merges": [],
    }
    if landing_hit_id is not None:
        result["landing_hit_id"] = landing_hit_id
    return result


class PostRussiaContactCandidateTest(unittest.TestCase):
    def setUp(self):
        self.one_russia_with_t12_pair = [
            piece("russia", 15, -2.0, -2.7, 1.35),
            piece("t12-left", 12, 0.0, -2.0, 0.9),
            piece("t12-right", 12, 2.2, -1.5, 0.9),
        ]
        self.results = [
            candidate(-2.0),
            candidate(0.0),
            candidate(3.0, landing_hit_id="t12-right"),
        ]

    def test_current_strategy_passes_production_sandbox_validation(self):
        result = subprocess.run(
            [
                "bash",
                "-lc",
                "source ./eloop_lib.sh; validate_strategy_with_helpers strategy.py strategy_helpers",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )

    def test_selects_outer_side_of_highest_pair_after_first_russia(self):
        selected = strategy._select_post_russia_contact_candidate(
            self.one_russia_with_t12_pair, self.results, 10, 0.5
        )

        self.assertIsNotNone(selected)
        result, reason = selected
        self.assertEqual(result["x"], 3.0)
        self.assertEqual(reason, "POST_RUSSIA_T12_CONTACT_SHOT")

    def test_does_not_change_pre_russia_policy(self):
        pieces = self.one_russia_with_t12_pair[1:]

        self.assertIsNone(
            strategy._select_post_russia_contact_candidate(pieces, self.results, 10, 0.5)
        )

    def test_keeps_safe_immediate_merge_priority(self):
        results = self.results + [candidate(1.5, grade="DIRECT")]

        self.assertIsNone(
            strategy._select_post_russia_contact_candidate(
                self.one_russia_with_t12_pair, results, 10, 0.5
            )
        )

    def test_rejects_unsafe_or_distant_contact_lane(self):
        results = [
            candidate(3.0, margin=0.2, landing_hit_id="t12-right"),
            candidate(0.0, landing_hit_id="russia"),
        ]

        self.assertIsNone(
            strategy._select_post_russia_contact_candidate(
                self.one_russia_with_t12_pair, results, 10, 0.5
            )
        )

    def test_small_piece_does_not_trigger_contact_shot(self):
        self.assertIsNone(
            strategy._select_post_russia_contact_candidate(
                self.one_russia_with_t12_pair, self.results, 7, 0.35
            )
        )

    def test_two_russias_activate_final_soviet_contact_shot(self):
        pieces = [
            piece("russia-left", 15, -1.5, -2.0, 1.4),
            piece("russia-right", 15, 1.5, -2.0, 1.4),
        ]
        results = [
            candidate(-3.0, landing_hit_id="russia-left"),
            candidate(0.0),
            candidate(3.0, landing_hit_id="russia-right"),
        ]

        selected = strategy._select_post_russia_contact_candidate(
            pieces, results, 11, 0.55
        )

        self.assertIsNotNone(selected)
        result, reason = selected
        self.assertEqual(result["x"], -3.0)
        self.assertEqual(reason, "SOVIET_T15_CONTACT_SHOT")

    def test_decide_integration_returns_contact_reason(self):
        game_state = {
            "pieces": self.one_russia_with_t12_pair,
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": self.results,
            "reactor": {
                "deadline_margin": 1.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(decision, {"x": 3.0, "reason": "POST_RUSSIA_T12_CONTACT_SHOT"})
        final_decision = strategy_runner.enforce_deadline_safety(
            decision, analysis, game_state
        )
        self.assertEqual(final_decision, decision)

    def test_decide_uses_analyzer_second_russia_lane_without_a_pair(self):
        pieces = [
            piece("russia", 15, -2.0, -2.7, 1.35),
            piece("t12-seed", 12, 2.0, -1.8, 0.9),
        ]
        results = [candidate(-2.0), candidate(2.0)]
        game_state = {
            "pieces": pieces,
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": results,
            "reactor": {
                "deadline_margin": 1.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
                "soviet": {"second_russia_lane_x": 2.0},
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(decision["x"], 2.0)
        self.assertIn("SECOND_RUSSIA_CHAIN_LANE", decision["reason"])

    def test_decide_preserves_mirrored_left_second_russia_lane(self):
        pieces = [
            piece("russia", 15, 2.0, -2.7, 1.35),
            piece("t12-seed", 12, -2.0, -1.8, 0.9),
        ]
        results = [candidate(-2.0), candidate(2.0)]
        game_state = {
            "pieces": pieces,
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": results,
            "reactor": {
                "deadline_margin": 1.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
                "soviet": {"second_russia_lane_x": -2.0},
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(decision["x"], -2.0)
        self.assertIn("SECOND_RUSSIA_CHAIN_LANE", decision["reason"])
        final_decision = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(decision), copy.deepcopy(analysis), copy.deepcopy(game_state)
        )
        self.assertEqual(final_decision, decision)

    def test_deadline_guard_selects_safe_no_merge_instead_of_default_x(self):
        pieces = [piece("russia", 15, 2.0, -2.7, 1.35)]
        safe = candidate(-2.0, landing_y=-2.0)
        crossing = candidate(0.0, crosses=True, landing_y=2.8)
        game_state = {
            "pieces": pieces,
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": [safe, crossing],
            "reactor": {
                "deadline_margin": 0.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(decision, {"x": -2.0, "reason": "DEADLINE_GUARD_SAFE_LANDING"})

    def test_deadline_guard_keeps_pre_russia_default_boundary(self):
        game_state = {
            "pieces": [piece("t12", 12, 2.0, -2.7, 0.9)],
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": [
                candidate(-2.0, landing_y=-2.0),
                candidate(0.0, crosses=True, landing_y=2.8),
            ],
            "reactor": {
                "deadline_margin": 0.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(
            decision,
            {"x": 0.0, "reason": "NO_MERGE_DEADLINE_GUARD_NO_VALID"},
        )

    def test_deadline_guard_preserves_e5_far_choice_before_russia(self):
        far = candidate(-2.0, grade="FAR", landing_y=-1.0)
        no_merge = candidate(2.0, grade="NO", landing_y=-2.0)
        game_state = {
            "pieces": [piece("uzbekistan", 11, 0.0, -2.7, 0.8)],
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": [far, no_merge],
            "reactor": {
                "deadline_margin": 0.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(
            decision,
            {"x": -2.0, "reason": "DEADLINE_GUARD_SAFE_LANDING"},
        )

    def test_deadline_guard_uses_safe_no_merge_after_russia(self):
        far = candidate(-2.0, grade="FAR", landing_y=-1.0)
        no_merge = candidate(2.0, grade="NO", landing_y=-2.0)
        game_state = {
            "pieces": [piece("russia", 15, 0.0, -2.7, 1.35)],
            "next": {"type": 7, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": [far, no_merge],
            "reactor": {
                "deadline_margin": 0.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(
            decision,
            {"x": 2.0, "reason": "DEADLINE_GUARD_SAFE_LANDING"},
        )

    def test_deadline_guard_uses_lowest_risk_real_candidate_when_all_cross(self):
        pieces = [piece("russia", 15, 2.0, -2.7, 1.35)]
        lower_risk = candidate(-2.0, crosses=True, landing_y=2.7)
        lower_risk["risk_top_y_after_drop"] = 3.6
        higher_risk = candidate(0.0, crosses=True, landing_y=2.8)
        higher_risk["risk_top_y_after_drop"] = 4.2
        game_state = {
            "pieces": pieces,
            "next": {"type": 10, "r": 0.5},
            "nextNext": {"type": 4, "r": 0.3},
            "deadline_crossed": False,
        }
        analysis = {
            "results": [higher_risk, lower_risk],
            "reactor": {
                "deadline_margin": 0.5,
                "danger_piece_count": 0,
                "reactive_pairs": [],
                "near_pairs": [],
                "pipeline": [],
            },
        }

        decision = strategy.decide(game_state, analysis)

        self.assertEqual(
            decision,
            {"x": -2.0, "reason": "NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS"},
        )

    def test_final_clip_preserves_old_bounds_until_russia_exists(self):
        self.assertEqual(strategy._clip_final_drop_x(-3.0, False), -0.991)
        self.assertEqual(strategy._clip_final_drop_x(5.0, False), 4.362)
        self.assertEqual(strategy._clip_final_drop_x(-3.0, False, fallback=True), -1.6)
        self.assertEqual(strategy._clip_final_drop_x(3.0, False, fallback=True), 0.9)

    def test_final_clip_unlocks_both_outer_lanes_after_russia(self):
        self.assertEqual(strategy._clip_final_drop_x(-3.0, True), -3.0)
        self.assertEqual(strategy._clip_final_drop_x(3.0, True), 3.0)
        self.assertEqual(strategy._clip_final_drop_x(-3.0, True, fallback=True), -3.0)
        self.assertEqual(strategy._clip_final_drop_x(3.0, True, fallback=True), 3.0)

    def test_shape_aware_hit_must_match_contact_target(self):
        results = [
            candidate(-2.0, landing_hit_id="russia"),
            candidate(0.0, landing_hit_id="t12-left"),
            candidate(3.0, landing_hit_id="low-tier-obstacle"),
        ]

        self.assertIsNone(
            strategy._select_post_russia_contact_candidate(
                self.one_russia_with_t12_pair, results, 10, 0.5
            )
        )

    def test_bbox_overlap_does_not_hide_shape_aware_contact_lane(self):
        pieces = [
            piece("russia", 15, -2.5, -2.7, 1.35),
            # Sprite bounding circles overlap (distance 2.8 < radius sum 3.0),
            # while landing_hit_id represents the actual collider contact path.
            piece("t12-left", 12, -1.4, -2.0, 1.5),
            piece("t12-right", 12, 1.4, -1.5, 1.5),
        ]
        results = [
            candidate(-2.0),
            candidate(0.0),
            candidate(3.0, landing_hit_id="t12-right"),
        ]

        selected = strategy._select_post_russia_contact_candidate(
            pieces, results, 10, 0.5
        )

        self.assertIsNotNone(selected)
        result, reason = selected
        self.assertEqual(result["x"], 3.0)
        self.assertEqual(reason, "POST_RUSSIA_T12_CONTACT_SHOT")

    def test_analyzer_exposes_shape_aware_landing_hit(self):
        from analyze_board import TYPE_RADII, analyze_drops

        results, _ = analyze_drops(
            self.one_russia_with_t12_pair,
            next_type=9,
            next_r=TYPE_RADII[9],
        )
        right_edge = min(results, key=lambda result: abs(result["x"] - 3.0))

        self.assertIn("landing_hit_id", right_edge)
        self.assertEqual(right_edge["landing_hit_id"], "t12-right")

    def test_full_analysis_and_runner_pipeline_preserves_safe_contact(self):
        from analyze_board import TYPE_RADII

        game_state = {
            "pieces": self.one_russia_with_t12_pair,
            "next": {"type": 9, "r": TYPE_RADII[9]},
            "nextNext": {"type": 4, "r": TYPE_RADII[4]},
            "state": "MOVE",
            "deadline_y": 3.38,
        }
        analysis = strategy_runner.build_analysis(copy.deepcopy(game_state))
        strategy_runner.enrich_game_state_deadline_fields(game_state, analysis)

        decision = strategy.decide(copy.deepcopy(game_state), copy.deepcopy(analysis))
        final_decision = strategy_runner.enforce_deadline_safety(
            copy.deepcopy(decision), copy.deepcopy(analysis), copy.deepcopy(game_state)
        )

        self.assertEqual(decision["reason"], "POST_RUSSIA_T12_CONTACT_SHOT")
        self.assertEqual(final_decision, decision)

    def test_runner_overrides_contact_when_deadline_headroom_is_worse(self):
        low_risk = candidate(0.0)
        low_risk.update({"risk_top_y_after_drop": 1.0, "top_y_after_drop": 1.0})
        contact = candidate(3.0, landing_hit_id="t12-right")
        contact.update(
            {
                "landing_y": 2.7,
                "risk_top_y_after_drop": 3.2,
                "top_y_after_drop": 3.2,
                "deadline_margin": 0.18,
            }
        )
        analysis = {
            "results": [low_risk, contact],
            "deadline": {
                "deadline_y": 3.38,
                "top_edge_y": 3.0,
                "deadline_crossed": False,
                "danger_piece_count": 0,
            },
            "reactor": {"reactive_pairs": []},
        }
        game_state = {
            "pieces": self.one_russia_with_t12_pair,
            "next": {"type": 10, "r": 0.846},
        }

        with mock.patch.object(strategy_runner, "log"):
            final_decision = strategy_runner.enforce_deadline_safety(
                {"x": 3.0, "reason": "POST_RUSSIA_T12_CONTACT_SHOT"},
                analysis,
                game_state,
            )

        self.assertEqual(final_decision["x"], 0.0)
        self.assertIn("RUNTIME_DEADLINE_SAFETY_OVERRIDE", final_decision["reason"])


if __name__ == "__main__":
    unittest.main()
