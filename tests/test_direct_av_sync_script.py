from pathlib import Path
import os
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "direct_av_sync_test.sh"


class DirectAVSyncScriptTests(unittest.TestCase):
    def test_live_run_requires_exact_confirmation_before_env_read(self) -> None:
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
        self.assertIn("requires", result.stderr)
        self.assertFalse(marker.exists())

    def test_config_output_contains_no_rtmp_destination(self) -> None:
        env = os.environ.copy()
        env["SOREN_ENV_FILE"] = str(REPO_ROOT / "tests" / "missing.env")
        env["SOREN_STREAM_BACKEND"] = "ffmpeg"
        result = subprocess.run(
            ["bash", str(SCRIPT), "--config"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("rtmp://", result.stdout)


if __name__ == "__main__":
    unittest.main()
