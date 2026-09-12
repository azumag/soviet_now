from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ImproveFailureHistoryTests(unittest.TestCase):
    def _run(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_apply_marker_reclassifies_legacy_validation_failure_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            capture = root / "captured_detail"
            history = root / "improve_last_failure.json"
            apply_marker = root / "strategy_apply_last_failure.json"
            apply_marker.write_text(
                json.dumps({
                    "schema_version": 1,
                    "failure_code": "apply_bundle_failed",
                    "failed_at": 200,
                }),
                encoding="utf-8",
            )

            script = r'''
set -euo pipefail
_write_improve_state() {
  printf '%s\n' "$6" > "$CAPTURE_FILE"
}
source strategy/improve_failure.sh
IMPROVE_LAST_FAILURE_FILE="$HISTORY_FILE"
STRATEGY_APPLY_FAILURE_FILE="$APPLY_MARKER"
_write_improve_state running 123 abc done 100 \
  failed_no_apply:validation_failed 100 50 normal
# A later live-state write must not erase durable terminal history.
_write_improve_state idle 0 '' recovered 0 live_process_detected 300 0 normal
'''
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(root),
                "TMP_STATE_DIR": str(root / "state"),
                "CAPTURE_FILE": str(capture),
                "HISTORY_FILE": str(history),
                "APPLY_MARKER": str(apply_marker),
            }
            result = self._run(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(history.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "failed_no_apply")
            self.assertEqual(data["failure_code"], "apply_bundle_failed")
            self.assertGreater(data["failed_at"], 0)

    def test_old_apply_marker_does_not_relabel_later_validation_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            capture = root / "captured_detail"
            history = root / "improve_last_failure.json"
            apply_marker = root / "strategy_apply_last_failure.json"
            apply_marker.write_text(
                json.dumps({
                    "schema_version": 1,
                    "failure_code": "apply_bundle_failed",
                    "failed_at": 50,
                }),
                encoding="utf-8",
            )

            script = r'''
set -euo pipefail
_write_improve_state() {
  printf '%s\n' "$6" > "$CAPTURE_FILE"
}
source strategy/improve_failure.sh
IMPROVE_LAST_FAILURE_FILE="$HISTORY_FILE"
STRATEGY_APPLY_FAILURE_FILE="$APPLY_MARKER"
_write_improve_state running 123 abc done 100 \
  failed_no_apply:validation_failed 100 50 normal
'''
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(root),
                "TMP_STATE_DIR": str(root / "state"),
                "CAPTURE_FILE": str(capture),
                "HISTORY_FILE": str(history),
                "APPLY_MARKER": str(apply_marker),
            }
            result = self._run(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                capture.read_text(encoding="utf-8").strip(),
                "failed_no_apply:validation_failed",
            )
            data = json.loads(history.read_text(encoding="utf-8"))
            self.assertEqual(data["failure_code"], "validation_failed")

    def test_fixed_model_failure_survives_following_idle_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            history = root / "improve_last_failure.json"
            script = r'''
set -euo pipefail
_write_improve_state() { return 0; }
source strategy/improve_failure.sh
IMPROVE_LAST_FAILURE_FILE="$HISTORY_FILE"
_write_improve_state running 123 abc done 100 \
  failed_no_apply:model_no_response 100 50 normal
_write_improve_state idle 0 '' '' 0 '' 200 0 normal
'''
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(root),
                "TMP_STATE_DIR": str(root / "state"),
                "HISTORY_FILE": str(history),
            }
            result = self._run(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(history.read_text(encoding="utf-8"))
            self.assertEqual(data["failure_code"], "model_no_response")


if __name__ == "__main__":
    unittest.main()
