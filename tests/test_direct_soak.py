import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "lib" / "direct_soak.py"
SPEC = importlib.util.spec_from_file_location("direct_soak", MODULE_PATH)
assert SPEC and SPEC.loader
direct_soak = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = direct_soak
SPEC.loader.exec_module(direct_soak)


def sample(
    at: float,
    frame: int,
    *,
    started_at: int = 100,
    fps: float = 30.0,
    speed: float = 1.0,
    drops: int = 0,
    dups: int = 0,
    running: bool = True,
    publishers: int = 1,
    relay: bool = True,
    obs: bool = False,
    relay_publishers: int = 1,
    audio_ok: bool = True,
    audio_present: bool = True,
) -> dict[str, object]:
    return {
        "sampled_at": at,
        "direct": {
            "running": running,
            "started_at": started_at,
            "frame": frame,
            "fps": fps,
            "speed": speed,
            "drop_frames": drops,
            "dup_frames": dups,
        },
        "game": {"measuredFps": 27.5},
        "relay": {"active": relay, "restarts": 0},
        "obs": {"active": obs, "restarts": 0},
        "publisher_process_count": publishers,
        "relay_publisher_connection_count": relay_publishers,
        "audio": {
            "ok": audio_ok,
            "non_silent": audio_present,
            "mean_db": -27.0 if audio_present else -80.0,
            "max_db": -10.6 if audio_present else -70.0,
        },
        "system": {},
    }


class DirectSoakTests(unittest.TestCase):
    def test_good_24h_shape_passes_declared_machine_measurable_requirements(self) -> None:
        rows = [sample(at, int(at * 30)) for at in (0, 21600, 43200, 64800, 86400)]
        summary = direct_soak.summarize_samples(rows, 86400)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["mean_output_fps"], 30.0)
        self.assertEqual(summary["publisher_count_max"], 1)
        self.assertEqual(summary["relay_publisher_connection_count_max"], 1)
        self.assertEqual(summary["combined_audio_present_ratio"], 1.0)
        self.assertIn("audio_video_sync_ms", summary["not_measured_by_soak_monitor"])

    def test_sustained_slow_speed_drop_growth_and_duplicate_publishers_fail(self) -> None:
        rows = [
            sample(0, 0, speed=1.0, drops=0, publishers=1),
            sample(60, 1500, speed=0.95, drops=3, publishers=2, relay_publishers=2),
            sample(120, 3000, speed=0.94, drops=8, publishers=2, relay_publishers=2),
        ]
        summary = direct_soak.summarize_samples(rows, 120)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["requirements"]["mean_output_fps_29_5"])
        self.assertFalse(summary["requirements"]["speed_p05_0_98"])
        self.assertFalse(summary["requirements"]["drop_not_continuous"])
        self.assertFalse(summary["requirements"]["single_publisher"])
        self.assertFalse(summary["requirements"]["single_relay_publisher_connection"])

    def test_tiny_cfr_corrections_do_not_count_as_sustained_drop_growth(self) -> None:
        rows = [
            sample(0, 0, drops=0, dups=0),
            sample(60, 1800, drops=1, dups=1),
            sample(120, 3600, drops=7, dups=7),
            sample(180, 5400, drops=8, dups=8),
        ]
        summary = direct_soak.summarize_samples(rows, 180)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["max_consecutive_drop_growth"], 3)
        self.assertEqual(summary["max_consecutive_significant_drop_growth"], 1)
        self.assertTrue(summary["requirements"]["drop_not_continuous"])
        self.assertTrue(summary["requirements"]["drop_ratio_under_1pct"])
        self.assertTrue(summary["requirements"]["dup_ratio_under_1pct"])

    def test_one_large_frame_adjustment_burst_fails_aggregate_ratio(self) -> None:
        rows = [
            sample(0, 0, drops=0, dups=0),
            sample(60, 1800, drops=20, dups=20),
        ]
        summary = direct_soak.summarize_samples(rows, 60)
        self.assertFalse(summary["ok"])
        self.assertTrue(summary["requirements"]["drop_not_continuous"])
        self.assertFalse(summary["requirements"]["drop_ratio_under_1pct"])
        self.assertFalse(summary["requirements"]["dup_ratio_under_1pct"])

    def test_missing_or_silent_combined_audio_fails(self) -> None:
        rows = [
            sample(0, 0),
            sample(60, 1800, audio_present=False),
            sample(120, 3600, audio_ok=False, audio_present=False),
        ]
        summary = direct_soak.summarize_samples(rows, 120)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["requirements"]["audio_probe_success_ratio_0_99"])
        self.assertFalse(summary["requirements"]["combined_audio_present_ratio_0_99"])

    def test_config_is_strict_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = direct_soak.load_config(
                {
                    "SOREN_STREAM_BACKEND": "ffmpeg",
                    "SOREN_DIRECT_SOAK_DURATION_SEC": "3600",
                    "SOREN_DIRECT_SOAK_INTERVAL_SEC": "60",
                    "SOREN_DIRECT_SOAK_STATE_DIR": temp_dir,
                }
            )
            public = config.public_dict()
            self.assertEqual(public["duration_sec"], 3600)
            self.assertEqual(public["audio_silence_threshold_db"], -60.0)
            self.assertNotIn("url", " ".join(public))
            with self.assertRaises(direct_soak.SoakConfigError):
                direct_soak.load_config(
                    {
                        "SOREN_STREAM_BACKEND": "ffmpeg",
                        "SOREN_DIRECT_SOAK_DURATION_SEC": "24h",
                    }
                )
            with self.assertRaises(direct_soak.SoakConfigError):
                direct_soak.load_config(
                    {
                        "SOREN_STREAM_BACKEND": "ffmpeg",
                        "SOREN_DIRECT_STREAM_PULSE_SOURCE": "sink;unsafe",
                    }
                )

    def test_audio_probe_parses_volume_without_persisting_ffmpeg_output(self) -> None:
        config = direct_soak.load_config(
            {
                "SOREN_STREAM_BACKEND": "ffmpeg",
                "SOREN_DIRECT_SOAK_DURATION_SEC": "60",
                "SOREN_DIRECT_SOAK_INTERVAL_SEC": "10",
            }
        )
        completed = direct_soak.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="mean_volume: -27.0 dB\nmax_volume: -10.6 dB\n",
        )
        with mock.patch.object(direct_soak.subprocess, "run", return_value=completed) as run:
            result = direct_soak._probe_audio(config)
        self.assertEqual(result["mean_db"], -27.0)
        self.assertEqual(result["max_db"], -10.6)
        self.assertTrue(result["non_silent"])
        command = run.call_args.args[0]
        self.assertIn("soren_null.monitor", command)
        self.assertNotIn("rtmp", " ".join(command))

    def test_audio_probe_failure_is_generic_and_redacted(self) -> None:
        config = direct_soak.load_config(
            {
                "SOREN_STREAM_BACKEND": "ffmpeg",
                "SOREN_DIRECT_SOAK_DURATION_SEC": "60",
                "SOREN_DIRECT_SOAK_INTERVAL_SEC": "10",
            }
        )
        completed = direct_soak.subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="internal failure with sensitive diagnostics",
        )
        with mock.patch.object(direct_soak.subprocess, "run", return_value=completed):
            result = direct_soak._probe_audio(config)
        self.assertEqual(result, {"ok": False, "non_silent": False, "error": "probe_failed"})

    def test_relay_publisher_connection_count_uses_local_rtmp_server_port(self) -> None:
        completed = direct_soak.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "0 0 127.0.0.1:1935 127.0.0.1:42000\n"
                "0 0 127.0.0.1:1935 127.0.0.1:42002\n"
            ),
            stderr="",
        )
        with mock.patch.object(direct_soak.subprocess, "run", return_value=completed) as run:
            count = direct_soak._relay_publisher_connection_count()
        self.assertEqual(count, 2)
        self.assertEqual(run.call_args.args[0][-5:], ["(", "sport", "=", ":1935", ")"])

    def test_direct_snapshot_filters_config_and_output_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "status.json"
            path.write_text(
                '{"running":false,"state":"failed","config":{"secret":"x"},'
                '"output_url":"rtmp://must-not-leak"}',
                encoding="utf-8",
            )
            snapshot = direct_soak._direct_snapshot(path)
            self.assertEqual(snapshot["state"], "failed")
            self.assertNotIn("config", snapshot)
            self.assertNotIn("output_url", snapshot)


if __name__ == "__main__":
    unittest.main()
