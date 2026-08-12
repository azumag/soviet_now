import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class StreamBackendStatusTests(unittest.TestCase):
    def test_show_status_reports_running_ffmpeg_instead_of_expected_obs_down(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            (state_dir / "status.json").write_text(
                json.dumps(
                    {
                        "backend": "ffmpeg",
                        "running": True,
                        "state": "running",
                        "pid": os.getpid(),
                        "ffmpeg_pid": os.getpid(),
                        "fps": 29.97,
                        "speed": 0.998,
                        "drop_frames": 0,
                        "dup_frames": 1,
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "SOREN_STREAM_BACKEND": "ffmpeg",
                    "SOREN_DIRECT_STREAM_DISPLAY": ":99.0",
                    "SOREN_DIRECT_STREAM_STATE_DIR": str(state_dir),
                    "SHOW_STATUS_NO_FLICKER": "1",
                }
            )
            result = subprocess.run(
                ["./show_status.sh", "--once"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Backend", result.stdout)
        self.assertIn("FFMPEG LIVE", result.stdout)
        self.assertIn("fps=29.97", result.stdout)
        self.assertNotIn("OBSWS", result.stdout)

    def test_status_dashboard_header_includes_selected_backend(self) -> None:
        env = os.environ.copy()
        env["SOREN_STREAM_BACKEND"] = "ffmpeg"
        result = subprocess.run(
            ["python3", "status_dashboard.py"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SOREN/FFMPEG", result.stdout)

    def test_show_status_reports_soak_audio_and_av_sync_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            direct = root / "direct"
            soak = root / "soak"
            direct.mkdir()
            soak.mkdir()
            (direct / "status.json").write_text(
                json.dumps(
                    {
                        "running": True,
                        "mode": "live",
                        "pid": os.getpid(),
                        "ffmpeg_pid": os.getpid(),
                        "fps": 30.0,
                        "speed": 1.0,
                        "drop_frames": 0,
                        "dup_frames": 0,
                    }
                ),
                encoding="utf-8",
            )
            (soak / "status.json").write_text(
                json.dumps(
                    {
                        "state": "running",
                        "running": True,
                        "elapsed_sec": 120,
                        "latest": {
                            "direct": {"fps": 30.0, "speed": 1.0},
                            "audio": {"ok": True, "non_silent": True, "max_db": -12.5},
                        },
                    }
                ),
                encoding="utf-8",
            )
            av_state = root / "av.json"
            av_state.write_text(
                json.dumps(
                    {
                        "state": "passed",
                        "result": {
                            "pair_count": 6,
                            "max_abs_offset_ms": 62.0,
                            "drift_ms": 20.0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "SOREN_STREAM_BACKEND": "ffmpeg",
                    "SOREN_DIRECT_STREAM_STATE_DIR": str(direct),
                    "SOREN_DIRECT_SOAK_STATE_DIR": str(soak),
                    "SOREN_DIRECT_AV_SYNC_STATE_FILE": str(av_state),
                    "SHOW_STATUS_NO_FLICKER": "1",
                }
            )
            result = subprocess.run(
                ["./show_status.sh", "--once"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Soak", result.stdout)
        self.assertIn("audio=on max=-12.5dB", result.stdout)
        self.assertIn("AVSync", result.stdout)
        self.assertIn("pairs=6 max=62.0ms drift=20.0ms", result.stdout)


if __name__ == "__main__":
    unittest.main()
