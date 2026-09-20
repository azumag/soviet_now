from pathlib import Path
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analyze_board  # noqa: E402
from lib.jev_player_candidates import build_candidates  # noqa: E402
from lib.jev_player_contract import normalize_observation  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "jev_observation_v1.json"
IDENTITY = {
    "run_id": "11111111-1111-4111-8111-111111111111",
    "game_instance_id": "22222222-2222-4222-8222-222222222222",
    "game_generation": 18,
    "player_generation": 3,
    "opportunity_seq": 7,
    "frame_seq": 120,
    "observed_at": "2026-09-20T04:00:00Z",
    "drop_piece_id": 17,
}


def observation():
    return normalize_observation(json.loads(FIXTURE.read_text(encoding="utf-8")), IDENTITY)


def config(**extra):
    value = {"command_x_min": 410, "command_x_max": 830}
    value.update(extra)
    return value


class CandidateTests(unittest.TestCase):
    def test_uniform25_has_edges_center_and_stable_ids(self):
        candidates = build_candidates(observation(), config())
        self.assertEqual(len(candidates), 25)
        self.assertEqual(candidates[0].candidate_id, "c00")
        self.assertEqual(candidates[0].x, -3.0)
        self.assertEqual(candidates[12].candidate_id, "c12")
        self.assertEqual(candidates[12].x, 0.0)
        self.assertEqual(candidates[-1].candidate_id, "c24")
        self.assertEqual(candidates[-1].x, 3.0)
        self.assertEqual([item.candidate_id for item in candidates], [f"c{i:02d}" for i in range(25)])
        self.assertEqual(len({item.command_x for item in candidates}), 25)

    def test_geometry_prediction_is_allowlisted_and_danger_is_not_filtered(self):
        result = {
            "x": 0.0,
            "landing_y": -1.0,
            "landing_hit_id": 7,
            "top_y_after_drop": 4.0,
            "risk_top_y_after_drop": 4.2,
            "deadline_y": 3.38,
            "crosses_deadline": True,
            "merge_grade": "NO",
            "merges": [{"id": 7, "grade": "DIRECT"}],
            "strategy_score": 999999,
            "reason": "should never cross boundary",
        }
        candidates = build_candidates(observation(), config(analysis_results=[result]))
        center = candidates[12]
        public = center.to_public_dict()
        self.assertEqual(public["prediction"]["predicted_first_contact_id"], 7)
        self.assertEqual(public["prediction"]["predicted_first_contact_type"], 3)
        self.assertEqual(public["prediction"]["predicted_merge_targets"], [7])
        self.assertGreater(public["prediction"]["predicted_top_y"], 3.38)
        self.assertNotIn("strategy_score", json.dumps(public))
        self.assertNotIn("reason", json.dumps(public))
        self.assertEqual(len(candidates), 25)

    def test_duplicate_command_coordinates_are_removed_after_quantization(self):
        candidates = build_candidates(
            observation(),
            config(command_x_min=0, command_x_max=3),
        )
        self.assertEqual(len(candidates), 4)
        self.assertEqual([item.command_x for item in candidates], [0, 1, 2, 3])
        self.assertEqual([item.candidate_id for item in candidates], ["c00", "c01", "c02", "c03"])

    def test_analyzer_override_is_opt_in_and_default_sample_is_unchanged(self):
        pieces = []
        default_results, _ = analyze_board.analyze_drops(
            pieces, 3, analyze_board.TYPE_RADII[3], {}
        )
        explicit_default, _ = analyze_board.analyze_drops(
            pieces, 3, analyze_board.TYPE_RADII[3], {}, sample_xs_override=None
        )
        override_results, _ = analyze_board.analyze_drops(
            pieces,
            3,
            analyze_board.TYPE_RADII[3],
            {},
            sample_xs_override=[-3.0, -1.5, 0.0, 1.5, 3.0],
        )
        self.assertEqual(default_results, explicit_default)
        self.assertEqual([item["x"] for item in override_results], [-3.0, -1.5, 0.0, 1.5, 3.0])
        self.assertNotEqual(len(default_results), len(override_results))


if __name__ == "__main__":
    unittest.main()
