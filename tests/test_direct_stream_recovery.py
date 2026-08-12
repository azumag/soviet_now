from pathlib import Path
import os
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "direct_stream_recovery_test.sh"


class DirectStreamRecoveryTests(unittest.TestCase):
    def test_print_plan_is_non_mutating_and_describes_rollback(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--print-plan"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("one process", result.stdout)
        self.assertIn("one relay input", result.stdout)
        self.assertIn("Automatically restore", result.stdout)
        self.assertNotIn("rtmp://", result.stdout)

    def test_run_requires_exact_confirmation_before_platform_or_sudo_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "missing.env"
            env = os.environ.copy()
            env["SOREN_ENV_FILE"] = str(marker)
            result = subprocess.run(
                ["bash", str(SCRIPT), "--run"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage", result.stderr)
        self.assertFalse(marker.exists())

    def test_source_has_bounded_recovery_and_failure_rollback(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('systemctl --no-block restart "$RELAY_UNIT"', source)
        self.assertIn('TIMEOUT_SEC="${SOREN_DIRECT_RECOVERY_TIMEOUT_SEC:-60}"', source)
        self.assertIn('if [ "$(publisher_shape)" != "1:1" ]', source)
        self.assertIn("--rollback --confirm-live-rollback", source)
        self.assertIn('! systemctl is-active --quiet "$OBS_UNIT"', source)
        self.assertNotIn("PUSH_CONFIG", source)


if __name__ == "__main__":
    unittest.main()
