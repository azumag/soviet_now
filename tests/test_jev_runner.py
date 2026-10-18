import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.jev_player import JevPlayer, JevPlayerConfig  # noqa: E402
from lib.jev_player_contract import MODEL, dumps  # noqa: E402
from lib.jev_player_worker import WorkerResult  # noqa: E402
from lib.jev_runner import JevRunner  # noqa: E402


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
    "board_bounds": {"drop_x_min": -3.0, "drop_x_max": 3.0},
}


def game_state():
    state = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state["jev_identity"] = copy.deepcopy(IDENTITY)
    return state


def success_for_request(payload):
    ids = payload["questions"]["drop_position"]["candidate_ids"]
    probabilities = {candidate_id: 0.0 for candidate_id in ids}
    probabilities["c12"] = 1.0
    return WorkerResult(
        status="ok",
        selected_id="c12",
        confidence=0.1,
        probabilities=probabilities,
        model=MODEL,
        usage={"input_tokens": 20, "output_tokens": 2},
    )


class JevRunnerTests(unittest.TestCase):
    def test_jev_choice_is_mapped_to_candidate_without_legacy_finalizer(self):
        calls = []

        def transport(payload, key, timeout_ms):
            calls.append(payload)
            return success_for_request(payload)

        player = JevPlayer(
            JevPlayerConfig(enabled=True),
            transport=transport,
        )
        with tempfile.TemporaryDirectory() as directory:
            from lib.jev_player_evidence import JevEvidence

            evidence = JevEvidence(directory, IDENTITY["run_id"], config={"model": MODEL})
            runner = JevRunner(player=player, evidence=evidence)
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
                selection = runner.choose(game_state(), {"results": []})
            self.assertTrue(selection.applied)
            self.assertEqual(selection.candidate.candidate_id, "c12")
            self.assertEqual(selection.decision["x"], 0.0)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("strategy_score", dumps(calls[0]))
            self.assertNotIn("reason", dumps(calls[0]))
            runner.record_dispatch(IDENTITY, selection.candidate)
            runner.record_accepted(IDENTITY, selection.candidate, "accepted")
            runner.finalize({"pure_jev": True})
            self.assertTrue((Path(directory) / IDENTITY["run_id"] / "report.json").exists())

    def test_missing_bridge_identity_does_not_call_worker_or_guess_turn(self):
        calls = []

        def transport(payload, key, timeout_ms):
            calls.append(1)
            return success_for_request(payload)

        runner = JevRunner(
            player=JevPlayer(JevPlayerConfig(enabled=True), transport=transport),
        )
        state = game_state()
        del state["jev_identity"]
        selection = runner.choose(state, {"results": []})
        self.assertEqual(selection.outcome.status, "identity_missing")
        self.assertFalse(selection.applied)
        self.assertEqual(calls, [])

    def test_runner_builds_evidence_from_the_committed_run_id_env(self):
        # The loop exports SOREN_JEV_RUN_ID; a runner that only read JEV_RUN_ID
        # silently produced no ledger (evidence_incomplete) in production.
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"SOREN_JEV_RUN_ID": IDENTITY["run_id"], "JEV_EVIDENCE_ROOT": directory},
            ):
                runner = JevRunner()
            self.assertIsNotNone(runner.evidence)
            self.assertFalse(runner.evidence_incomplete)
            self.assertEqual(runner.evidence.run_id, IDENTITY["run_id"])


if __name__ == "__main__":
    unittest.main()
