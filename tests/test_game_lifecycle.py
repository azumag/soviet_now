import json
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BROKER = REPO_ROOT / "lib" / "game_lifecycle.py"


class GameLifecycleBrokerTests(unittest.TestCase):
    def run_broker(self, root: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            ["python3", str(BROKER), "--root", str(root), *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        return result, payload

    @staticmethod
    def request(root: Path, request_id: str, deadline: float = 60.0) -> tuple[subprocess.CompletedProcess[str], dict]:
        return GameLifecycleBrokerTests().run_broker(
            root,
            "request",
            "--request-id",
            request_id,
            "--game",
            "sorengame",
            "--generation",
            "1",
            "--deadline-sec",
            str(deadline),
        )

    def write_state(self, root: Path, state: str) -> None:
        (root / "game_state.json").write_text(
            json.dumps({"state": state, "score": 12, "pieces": []}),
            encoding="utf-8",
        )

    def test_request_is_idempotent_but_identity_and_parallel_requests_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            first, first_payload = self.request(root, request_id)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(first_payload["status"], "accepted")

            repeat, repeat_payload = self.request(root, request_id)
            self.assertEqual(repeat.returncode, 0)
            self.assertEqual(repeat_payload["status"], "existing")

            conflict, conflict_payload = self.run_broker(
                root,
                "request",
                "--request-id",
                request_id,
                "--game",
                "robots",
                "--generation",
                "1",
            )
            self.assertEqual(conflict.returncode, 3)
            self.assertEqual(conflict_payload["status"], "conflict")

            busy, busy_payload = self.request(root, str(uuid.uuid4()))
            self.assertEqual(busy.returncode, 3)
            self.assertEqual(busy_payload["status"], "busy")

    def test_stop_requires_boundary_ack_and_finish_requires_matching_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)
            self.write_state(root, "MOVE")

            premature, premature_payload = self.run_broker(root, "stop", "--request-id", request_id)
            self.assertEqual(premature.returncode, 1)
            self.assertEqual(premature_payload["status"], "waiting")
            self.assertFalse((root / "tmp/state/game_lifecycle/control.json").exists())

            self.write_state(root, "GAMEOVER")
            boundary, boundary_payload = self.run_broker(root, "boundary", "--request-id", request_id)
            self.assertEqual(boundary.returncode, 0)
            self.assertEqual(boundary_payload["ack"]["status"], "boundary")

            stopped, stopped_payload = self.run_broker(root, "stop", "--request-id", request_id)
            self.assertEqual(stopped.returncode, 0)
            self.assertEqual(stopped_payload["ack"]["status"], "stop_requested")
            control = json.loads((root / "tmp/state/game_lifecycle/control.json").read_text(encoding="utf-8"))
            self.assertEqual(control["action"], "stop")

            claimed, claimed_payload = self.run_broker(root, "claim-stop", "--request-id", request_id)
            self.assertEqual(claimed.returncode, 0)
            self.assertEqual(claimed_payload["ack"]["status"], "stopping")

            cancelled, cancelled_payload = self.run_broker(root, "cancel", "--request-id", request_id)
            self.assertEqual(cancelled.returncode, 3)
            self.assertEqual(cancelled_payload["ack"]["status"], "stopping")

            missing, missing_payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(missing.returncode, 1)
            self.assertEqual(missing_payload["status"], "waiting")

            resource = root / "tmp/state/game_lifecycle/game_resource.json"
            request_record = json.loads((root / "tmp/state/game_lifecycle/request.json").read_text(encoding="utf-8"))
            resource.write_text(
                json.dumps({
                    "schema": 1,
                    "request_id": request_id,
                    "game": request_record["game"],
                    "generation": request_record["generation"],
                    "deadline_epoch": request_record["deadline_epoch"],
                    "deadline_at": request_record["deadline_at"],
                    "status": "stopped",
                }),
                encoding="utf-8",
            )
            finished, finished_payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(finished.returncode, 0)
            self.assertEqual(finished_payload["ack"]["status"], "stopped")
            self.assertFalse((root / "tmp/state/game_lifecycle/control.json").exists())

    def test_stopping_claim_survives_deadline_and_cannot_be_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id, deadline=0.4)
            self.write_state(root, "GAMEOVER")
            self.run_broker(root, "boundary", "--request-id", request_id)
            self.run_broker(root, "stop", "--request-id", request_id)
            claimed, payload = self.run_broker(root, "claim-stop", "--request-id", request_id)
            self.assertEqual(claimed.returncode, 0)
            self.assertEqual(payload["ack"]["status"], "stopping")
            time.sleep(0.5)

            still_stopping, still_payload = self.run_broker(root, "stop", "--request-id", request_id)
            self.assertEqual(still_stopping.returncode, 0)
            self.assertEqual(still_payload["ack"]["status"], "stopping")

            request_record = json.loads((root / "tmp/state/game_lifecycle/request.json").read_text(encoding="utf-8"))
            (root / "tmp/state/game_lifecycle/game_resource.json").write_text(
                json.dumps({
                    **request_record,
                    "status": "stopped",
                    "irreversible": True,
                    "quit_called": True,
                }),
                encoding="utf-8",
            )
            finished, finished_payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(finished.returncode, 0)
            self.assertEqual(finished_payload["ack"]["status"], "stopped")

            restore, restore_payload = self.run_broker(root, "restore", "--request-id", request_id)
            self.assertEqual(restore.returncode, 3)
            self.assertEqual(restore_payload["status"], "conflict")

    def test_expired_stop_removes_control_and_late_resource_cannot_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id, deadline=0.5)
            self.write_state(root, "GAMEOVER")
            boundary, _ = self.run_broker(root, "boundary", "--request-id", request_id)
            self.assertEqual(boundary.returncode, 0)
            stop, _ = self.run_broker(root, "stop", "--request-id", request_id)
            self.assertEqual(stop.returncode, 0)
            self.assertTrue((root / "tmp/state/game_lifecycle/control.json").exists())
            time.sleep(0.6)

            expired, expired_payload = self.run_broker(root, "stop", "--request-id", request_id)
            self.assertEqual(expired.returncode, 2)
            self.assertEqual(expired_payload["ack"]["status"], "timeout")
            self.assertFalse((root / "tmp/state/game_lifecycle/control.json").exists())

            (root / "tmp/state/game_lifecycle/game_resource.json").write_text(
                json.dumps({
                    "schema": 1,
                    "request_id": request_id,
                    "game": "sorengame",
                    "generation": 1,
                    "deadline_epoch": 0,
                    "deadline_at": "expired",
                    "status": "stopped",
                }),
                encoding="utf-8",
            )
            late, late_payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(late.returncode, 3)
            self.assertEqual(late_payload["ack"]["status"], "timeout")

    def test_cancel_and_restore_are_request_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)
            cancel, cancel_payload = self.run_broker(root, "cancel", "--request-id", request_id)
            self.assertEqual(cancel.returncode, 0)
            self.assertEqual(cancel_payload["ack"]["status"], "cancelled")
            control = json.loads((root / "tmp/state/game_lifecycle/control.json").read_text(encoding="utf-8"))
            self.assertEqual(control["action"], "cancel")

            restore, restore_payload = self.run_broker(root, "restore", "--request-id", request_id)
            self.assertEqual(restore.returncode, 0)
            self.assertEqual(restore_payload["ack"]["status"], "resume_requested")
            control = json.loads((root / "tmp/state/game_lifecycle/control.json").read_text(encoding="utf-8"))
            self.assertEqual(control["action"], "resume")

    def test_finish_rejects_same_uuid_with_different_generation_or_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)
            self.write_state(root, "GAMEOVER")
            self.run_broker(root, "boundary", "--request-id", request_id)
            self.run_broker(root, "stop", "--request-id", request_id)
            request_record = json.loads((root / "tmp/state/game_lifecycle/request.json").read_text(encoding="utf-8"))
            resource = {
                "schema": 1,
                "request_id": request_id,
                "game": request_record["game"],
                "generation": request_record["generation"] + 1,
                "deadline_epoch": request_record["deadline_epoch"],
                "deadline_at": request_record["deadline_at"],
                "status": "stopped",
            }
            (root / "tmp/state/game_lifecycle/game_resource.json").write_text(json.dumps(resource), encoding="utf-8")
            finished, payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(finished.returncode, 1)
            self.assertEqual(payload["status"], "waiting")

            resource["generation"] = request_record["generation"]
            resource["deadline_epoch"] = request_record["deadline_epoch"] + 1
            (root / "tmp/state/game_lifecycle/game_resource.json").write_text(json.dumps(resource), encoding="utf-8")
            finished, payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(finished.returncode, 1)
            self.assertEqual(payload["status"], "waiting")

    def test_finish_does_not_trust_stopped_ack_without_matching_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)
            self.write_state(root, "GAMEOVER")
            self.run_broker(root, "boundary", "--request-id", request_id)
            self.run_broker(root, "stop", "--request-id", request_id)
            lifecycle_dir = root / "tmp/state/game_lifecycle"
            request_record = json.loads((lifecycle_dir / "request.json").read_text(encoding="utf-8"))
            (lifecycle_dir / "ack.json").write_text(json.dumps({
                "schema": 1,
                "request_id": request_id,
                "game": request_record["game"],
                "generation": request_record["generation"],
                "deadline_epoch": request_record["deadline_epoch"],
                "deadline_at": request_record["deadline_at"],
                "status": "stopped",
            }), encoding="utf-8")
            (lifecycle_dir / "game_resource.json").write_text(json.dumps({
                **request_record,
                "status": "stopped",
            }), encoding="utf-8")
            (lifecycle_dir / "game_resource.json").write_text(json.dumps({
                "schema": 1,
                "request_id": request_id,
                "game": "robots",
                "generation": request_record["generation"],
                "deadline_epoch": request_record["deadline_epoch"],
                "deadline_at": request_record["deadline_at"],
                "status": "stopped",
            }), encoding="utf-8")
            finished, payload = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(finished.returncode, 3)
            self.assertEqual(payload["status"], "conflict")

    def test_cancel_conflicts_on_resumed_and_is_idempotent_on_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)

            first, _ = self.run_broker(root, "cancel", "--request-id", request_id)
            self.assertEqual(first.returncode, 0)
            control_before = (root / "tmp/state/game_lifecycle/control.json").read_bytes()

            # Idempotent retry reports the existing ack without rewriting control.
            repeat, repeat_payload = self.run_broker(root, "cancel", "--request-id", request_id)
            self.assertEqual(repeat.returncode, 0)
            self.assertEqual(repeat_payload["ack"]["status"], "cancelled")
            self.assertEqual(
                (root / "tmp/state/game_lifecycle/control.json").read_bytes(),
                control_before,
            )

            # Drive the request to resumed, then cancel/stop must conflict.
            self.run_broker(root, "restore", "--request-id", request_id)
            lifecycle_dir = root / "tmp/state/game_lifecycle"
            request_record = json.loads((lifecycle_dir / "request.json").read_text(encoding="utf-8"))
            (lifecycle_dir / "game_resource.json").write_text(
                json.dumps({**request_record, "status": "resumed"}),
                encoding="utf-8",
            )
            resumed, resumed_payload = self.run_broker(root, "resume-complete", "--request-id", request_id)
            self.assertEqual(resumed.returncode, 0)
            self.assertEqual(resumed_payload["ack"]["status"], "resumed")

            cancel, cancel_payload = self.run_broker(root, "cancel", "--request-id", request_id)
            self.assertEqual(cancel.returncode, 3)
            self.assertEqual(cancel_payload["ack"]["status"], "resumed")

            stop, stop_payload = self.run_broker(root, "stop", "--request-id", request_id)
            self.assertEqual(stop.returncode, 3)
            self.assertEqual(stop_payload["ack"]["status"], "resumed")

    def test_restore_from_stopped_requires_matching_reversible_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)
            self.write_state(root, "GAMEOVER")
            self.run_broker(root, "boundary", "--request-id", request_id)
            self.run_broker(root, "stop", "--request-id", request_id)
            self.run_broker(root, "claim-stop", "--request-id", request_id)
            lifecycle_dir = root / "tmp/state/game_lifecycle"
            request_record = json.loads((lifecycle_dir / "request.json").read_text(encoding="utf-8"))
            (lifecycle_dir / "game_resource.json").write_text(
                json.dumps({**request_record, "status": "stopped"}),
                encoding="utf-8",
            )
            finished, _ = self.run_broker(root, "finish", "--request-id", request_id)
            self.assertEqual(finished.returncode, 0)

            # Missing resource: a stopped ack alone must not be restorable.
            (lifecycle_dir / "game_resource.json").unlink()
            missing, missing_payload = self.run_broker(root, "restore", "--request-id", request_id)
            self.assertEqual(missing.returncode, 3)
            self.assertEqual(missing_payload["status"], "conflict")

            # Mismatched resource identity must not be restorable either.
            (lifecycle_dir / "game_resource.json").write_text(
                json.dumps({
                    "schema": 1,
                    "request_id": request_id,
                    "game": "robots",
                    "generation": request_record["generation"],
                    "deadline_epoch": request_record["deadline_epoch"],
                    "deadline_at": request_record["deadline_at"],
                    "status": "stopped",
                }),
                encoding="utf-8",
            )
            mismatched, _ = self.run_broker(root, "restore", "--request-id", request_id)
            self.assertEqual(mismatched.returncode, 3)

            # Restore from cancelled without a resource keeps working.
            cancelled_id = str(uuid.uuid4())
            self.request(root, cancelled_id)
            # Current request is stopped/terminal, so the fresh request archives it.
            cancel, _ = self.run_broker(root, "cancel", "--request-id", cancelled_id)
            self.assertEqual(cancel.returncode, 0)
            restore, restore_payload = self.run_broker(root, "restore", "--request-id", cancelled_id)
            self.assertEqual(restore.returncode, 0)
            self.assertEqual(restore_payload["ack"]["status"], "resume_requested")

    def test_irreversible_fence_uses_truthiness_not_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request_id = str(uuid.uuid4())
            self.request(root, request_id)
            lifecycle_dir = root / "tmp/state/game_lifecycle"
            request_record = json.loads((lifecycle_dir / "request.json").read_text(encoding="utf-8"))
            # quit_called as int 1 (not boolean True) must still fence cancel.
            (lifecycle_dir / "game_resource.json").write_text(
                json.dumps({**request_record, "status": "stop_requested", "quit_called": 1}),
                encoding="utf-8",
            )
            cancelled, cancelled_payload = self.run_broker(root, "cancel", "--request-id", request_id)
            self.assertEqual(cancelled.returncode, 3)
            self.assertEqual(cancelled_payload["status"], "conflict")

    def test_request_ignores_identity_mismatched_ack(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_id = str(uuid.uuid4())
            self.request(root, first_id)
            lifecycle_dir = root / "tmp/state/game_lifecycle"
            # A stale ack from another request/generation must not park the broker.
            (lifecycle_dir / "ack.json").write_text(
                json.dumps({
                    "schema": 1,
                    "request_id": str(uuid.uuid4()),
                    "game": "sorengame",
                    "generation": 1,
                    "deadline_epoch": 0,
                    "deadline_at": "1970-01-01T00:00:00.000Z",
                    "status": "accepted",
                }),
                encoding="utf-8",
            )
            second_id = str(uuid.uuid4())
            second, second_payload = self.request(root, second_id)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(second_payload["status"], "accepted")
            history = list((lifecycle_dir / "history").glob("*.json"))
            self.assertTrue(history)

    def test_deadline_must_be_finite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for value in ("nan", "inf", "-inf"):
                deadline_arg = f"--deadline-sec={value}"
                result, payload = self.run_broker(root, "request", "--request-id", str(uuid.uuid4()), "--game", "sorengame", deadline_arg)
                self.assertEqual(result.returncode, 4)
                self.assertEqual(payload["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
