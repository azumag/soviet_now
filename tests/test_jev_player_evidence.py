import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.jev_player_evidence import EvidenceError, JevEvidence  # noqa: E402


RUN_ID = "11111111-1111-4111-8111-111111111111"
GAME_ID = "22222222-2222-4222-8222-222222222222"


class EvidenceTests(unittest.TestCase):
    def test_run_is_separate_private_and_finalizable(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = JevEvidence(
                directory,
                RUN_ID,
                config={
                    "model": "jev-1.13.0",
                    "decision_budget_ms": 1500,
                    "candidate_version": "uniform25-v1",
                },
            )
            evidence.record_decision(
                game_instance_id=GAME_ID,
                opportunity_seq=1,
                player_generation=3,
                candidate_count=25,
                status="ok",
                source="jev",
                selected_id="c12",
                confidence=0.61,
            )
            evidence.record_game_event(
                "action_accepted",
                game_instance_id=GAME_ID,
                payload={"candidate_id": "c12", "accepted": True},
            )
            report_path = evidence.finalize({"pure_jev": True, "fallback_count": 0})

            manifest = json.loads((Path(directory) / RUN_ID / "manifest.json").read_text())
            report = json.loads(report_path.read_text())
            events = [json.loads(line) for line in (Path(directory) / RUN_ID / "events.jsonl").read_text().splitlines()]
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(report["summary"]["pure_jev"], True)
            self.assertEqual([event["event_type"] for event in events], [
                "run_started",
                "decision",
                "action_accepted",
                "run_finished",
            ])
            self.assertEqual(events[1]["payload"]["selected_id"], "c12")
            for path in (Path(directory) / RUN_ID).iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((Path(directory) / RUN_ID).stat().st_mode), 0o700)

    def test_raw_request_and_secret_like_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EvidenceError, "forbidden"):
                JevEvidence(directory, RUN_ID, config={"api_key": "never-write"})

            evidence = JevEvidence(directory, RUN_ID)
            with self.assertRaisesRegex(EvidenceError, "forbidden"):
                evidence.append_event("decision", {"raw_response": {"model": "x"}})
            with self.assertRaisesRegex(EvidenceError, "forbidden"):
                evidence.finalize({"authorization": "never-write"})

    def test_path_and_duplicate_run_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EvidenceError):
                JevEvidence(directory, "../escape")
            evidence = JevEvidence(directory, RUN_ID)
            with self.assertRaisesRegex(EvidenceError, "already exists"):
                JevEvidence(directory, RUN_ID)
            with self.assertRaisesRegex(EvidenceError, "invalid game_instance_id"):
                evidence.record_decision(
                    game_instance_id="not-a-uuid",
                    opportunity_seq=1,
                    player_generation=1,
                    candidate_count=25,
                    status="fallback",
                    source="fallback",
                )

    def test_nonfinite_and_oversized_records_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = JevEvidence(directory, str(uuid.UUID(RUN_ID)))
            with self.assertRaises(EvidenceError):
                evidence.finalize({"score": float("nan")})
            with self.assertRaises(EvidenceError):
                evidence.append_event("observation", {"value": "x" * (32 * 1024)})
            events = (Path(directory) / RUN_ID / "events.jsonl").read_text().splitlines()
            self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
