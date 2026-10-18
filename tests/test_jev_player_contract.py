import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.jev_player_candidates import build_candidates  # noqa: E402
from lib.jev_player_contract import (  # noqa: E402
    DEFAULT_RUBRIC,
    JevContractError,
    MODEL,
    build_request,
    dumps,
    normalize_observation,
    strict_json,
    validate_choice,
)


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


def load_observation():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return normalize_observation(raw, IDENTITY)


def make_candidates(observation):
    return build_candidates(
        observation,
        {
            "command_x_min": 410,
            "command_x_max": 830,
            "analysis_results": [
                {
                    "x": -3.0,
                    "landing_y": -4.0,
                    "top_y_after_drop": -3.6,
                    "deadline_margin": 6.98,
                    "landing_hit_id": None,
                    "merges": [],
                },
                {
                    "x": 0.0,
                    "landing_y": -3.5,
                    "top_y_after_drop": -3.1,
                    "deadline_margin": 6.48,
                    "landing_hit_id": 7,
                    "merges": [{"id": 7, "grade": "DIRECT"}],
                },
                {
                    "x": 3.0,
                    "landing_y": -4.1,
                    "top_y_after_drop": -3.7,
                    "deadline_margin": 7.08,
                    "landing_hit_id": None,
                    "merges": [],
                },
            ],
        },
    )


class ObservationContractTests(unittest.TestCase):
    def test_next_is_current_piece_and_next_next_is_first_preview(self):
        observation = load_observation()
        self.assertEqual(observation.drop_piece["id"], 17)
        self.assertEqual(observation.drop_piece["type"], 3)
        self.assertEqual(observation.preview[0]["type"], 4)
        self.assertIsNone(observation.preview[1])
        self.assertEqual(observation.opportunity_seq, 7)
        self.assertEqual(observation.frame_seq, 120)

    def test_allowlist_drops_hold_persona_strategy_and_unknown_fields(self):
        public = load_observation().to_public_dict()
        serialized = dumps(public)
        for forbidden in ("hold", "garbage", "persona", "strategy_score", "reason"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn('"score":42', serialized)
        self.assertIn('"make_soren_count":0', serialized)

    def test_missing_identity_and_duplicate_or_boolean_numbers_fail_closed(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(JevContractError, "missing_drop_piece_id"):
            normalize_observation(raw, {key: value for key, value in IDENTITY.items() if key != "drop_piece_id"})

        raw["score"] = True
        with self.assertRaisesRegex(JevContractError, "invalid_score"):
            normalize_observation(raw, IDENTITY)

        raw["score"] = 42
        raw["pieces"].append(dict(raw["pieces"][0]))
        with self.assertRaisesRegex(JevContractError, "duplicate_piece_id"):
            normalize_observation(raw, IDENTITY)

    def test_unknown_phase_and_nonfinite_piece_fail(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["state"] = "PAUSED"
        with self.assertRaisesRegex(JevContractError, "invalid_phase"):
            normalize_observation(raw, IDENTITY)

        raw["state"] = "MOVE"
        raw["pieces"][0]["x"] = float("nan")
        with self.assertRaisesRegex(JevContractError, "invalid_piece_0_x"):
            normalize_observation(raw, IDENTITY)


class RequestAndChoiceTests(unittest.TestCase):
    def setUp(self):
        self.observation = load_observation()
        self.candidates = make_candidates(self.observation)
        self.request = build_request(self.observation, self.candidates, DEFAULT_RUBRIC, MODEL)
        self.ids = [candidate.candidate_id for candidate in self.candidates]

    def test_request_is_one_choice_and_contains_no_strategy_recommendation(self):
        self.assertEqual(self.request["model"], MODEL)
        self.assertEqual(set(self.request["questions"]), {"drop_position"})
        question = self.request["questions"]["drop_position"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(question["candidate_ids"], self.ids)
        self.assertNotIn("reason", self.request["state"]["observation"])
        self.assertNotIn("reason", self.request["questions"]["drop_position"])
        self.assertNotIn("best", dumps(self.request).lower())
        self.assertNotIn("strategy_score", dumps(self.request))

    def test_low_confidence_and_equal_maximum_are_valid(self):
        probabilities = {candidate_id: 0.0 for candidate_id in self.ids}
        probabilities[self.ids[0]] = 1.0
        response = {
            "model": MODEL,
            "answers": {
                "drop_position": {
                    "type": "choice",
                    "choice": self.ids[0],
                    "probabilities": probabilities,
                    "confidence": 0.01,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        result = validate_choice(response, self.ids)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.selected_id, self.ids[0])

        tied_ids = self.ids[:2]
        tied = {
            "model": MODEL,
            "answers": {
                "drop_position": {
                    "type": "choice",
                    "choice": tied_ids[1],
                    "probabilities": {tied_ids[0]: 0.5, tied_ids[1]: 0.5},
                    "confidence": 0.5,
                }
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        self.assertEqual(validate_choice(tied, tied_ids).selected_id, tied_ids[1])

    def test_two_decimal_rounded_probabilities_are_accepted_and_normalized(self):
        # The API rounds each probability to two decimals, so a real response
        # can sum to 0.99.  Accept that rounding band and normalize instead of
        # rejecting ~4% of real requests.
        probabilities = {candidate_id: 0.0 for candidate_id in self.ids}
        probabilities[self.ids[0]] = 0.99
        response = {
            "model": MODEL,
            "answers": {
                "drop_position": {
                    "type": "choice",
                    "choice": self.ids[0],
                    "probabilities": probabilities,
                    "confidence": 0.5,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        result = validate_choice(response, self.ids)
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(sum(result.probabilities.values()), 1.0, places=9)
        self.assertEqual(result.probabilities[self.ids[0]], 1.0)

    def test_probabilities_outside_the_rounding_band_are_rejected(self):
        probabilities = {candidate_id: 0.0 for candidate_id in self.ids}
        probabilities[self.ids[0]] = 0.5
        response = {
            "model": MODEL,
            "answers": {
                "drop_position": {
                    "type": "choice",
                    "choice": self.ids[0],
                    "probabilities": probabilities,
                    "confidence": 0.5,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        with self.assertRaisesRegex(JevContractError, "invalid_response"):
            validate_choice(response, self.ids)

    def test_choice_rejects_unknown_or_nonmax_candidate_and_bad_usage(self):
        response = {
            "model": MODEL,
            "answers": {
                "drop_position": {
                    "type": "choice",
                    "choice": "c99",
                    "probabilities": {candidate_id: 1 / len(self.ids) for candidate_id in self.ids},
                    "confidence": 0.5,
                }
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        with self.assertRaisesRegex(JevContractError, "unknown_candidate"):
            validate_choice(response, self.ids)

        response["answers"]["drop_position"]["choice"] = self.ids[0]
        response["answers"]["drop_position"]["probabilities"] = {
            candidate_id: 0.0 for candidate_id in self.ids
        }
        response["answers"]["drop_position"]["probabilities"][self.ids[1]] = 1.0
        with self.assertRaisesRegex(JevContractError, "choice_not_max_probability"):
            validate_choice(response, self.ids)

        response["answers"]["drop_position"]["probabilities"][self.ids[0]] = 1.0
        response["answers"]["drop_position"]["probabilities"][self.ids[1]] = 0.0
        response["usage"] = {"input_tokens": "unknown", "output_tokens": 1}
        with self.assertRaisesRegex(JevContractError, "usage_unknown"):
            validate_choice(response, self.ids)

        del response["usage"]
        with self.assertRaisesRegex(JevContractError, "usage_unknown"):
            validate_choice(response, self.ids)

    def test_response_model_mismatch_and_duplicate_json_keys_are_rejected(self):
        response = {
            "model": "jev-1.13.1",
            "answers": {},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        with self.assertRaisesRegex(JevContractError, "model_mismatch"):
            validate_choice(response, self.ids)
        with self.assertRaisesRegex(JevContractError, "duplicate_json_key"):
            strict_json('{"model":"jev-1.13.0","model":"jev-1.13.0"}')


if __name__ == "__main__":
    unittest.main()
