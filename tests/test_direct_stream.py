import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "lib" / "direct_stream.py"
SPEC = importlib.util.spec_from_file_location("direct_stream", MODULE_PATH)
assert SPEC and SPEC.loader
direct_stream = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = direct_stream
SPEC.loader.exec_module(direct_stream)


def base_env(**overrides: str) -> dict[str, str]:
    env = {
        "SOREN_STREAM_BACKEND": "ffmpeg",
        "SOREN_DIRECT_STREAM_DISPLAY": ":99.0",
        "SOREN_DIRECT_STREAM_SIZE": "1280x720",
        "SOREN_DIRECT_STREAM_FPS": "30",
        "SOREN_DIRECT_STREAM_VIDEO_KBPS": "4500",
        "SOREN_DIRECT_STREAM_AUDIO_KBPS": "160",
        "SOREN_DIRECT_STREAM_PULSE_SOURCE": "soren_null.monitor",
        "SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://127.0.0.1:1935/soren/live",
    }
    env.update(overrides)
    return env


class DirectStreamTests(unittest.TestCase):
    def test_live_command_has_expected_video_audio_and_loopback_output(self) -> None:
        config = direct_stream.load_config(base_env())
        command = direct_stream.build_ffmpeg_command(config, mode="live")
        joined = " ".join(command)
        self.assertIn("-f x11grab", joined)
        self.assertIn("-draw_mouse 0", joined)
        self.assertIn("-framerate 30", joined)
        self.assertIn("-video_size 1280x720", joined)
        self.assertIn("-f pulse", joined)
        self.assertIn("soren_null.monitor", command)
        self.assertIn("aresample=async=1:first_pts=0", command)
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("-g 60", joined)
        self.assertEqual(command[-1], "rtmp://127.0.0.1:1935/soren/live")

    def test_record_command_is_bounded_mkv_without_relay_url(self) -> None:
        config = direct_stream.load_config(base_env())
        output = Path("/tmp/direct-poc.mkv")
        command = direct_stream.build_ffmpeg_command(
            config,
            mode="record",
            output_path=output,
            duration_sec=60,
        )
        joined = " ".join(command)
        self.assertIn("-t 60", joined)
        self.assertIn("-f matroska", joined)
        self.assertEqual(command[-1], str(output))
        self.assertNotIn("rtmp://", joined)

    def test_external_or_credentialed_output_is_rejected(self) -> None:
        for url in (
            "rtmp://live.example.invalid/app/secret",
            "rtmp://user:password@127.0.0.1/app/live",
            "rtmps://127.0.0.1/app/live",
        ):
            with self.subTest(url=url):
                with self.assertRaises(direct_stream.ConfigError):
                    direct_stream.load_config(base_env(SOREN_DIRECT_STREAM_LOCAL_URL=url))

    def test_invalid_config_is_strict(self) -> None:
        invalid = (
            ("SOREN_STREAM_BACKEND", "direct"),
            ("SOREN_DIRECT_STREAM_SIZE", "1281x720"),
            ("SOREN_DIRECT_STREAM_SIZE", "1280*720"),
            ("SOREN_DIRECT_STREAM_FPS", "30fps"),
            ("SOREN_DIRECT_STREAM_VIDEO_KBPS", "6001"),
            ("SOREN_DIRECT_STREAM_AUDIO_KBPS", "0"),
            ("SOREN_DIRECT_STREAM_DISPLAY", "localhost:99"),
            ("SOREN_DIRECT_STREAM_PULSE_SOURCE", "sink;touch /tmp/no"),
        )
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                with self.assertRaises(direct_stream.ConfigError):
                    direct_stream.load_config(base_env(**{key: value}))

    def test_config_and_status_never_include_output_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = direct_stream.load_config(
                base_env(
                    SOREN_DIRECT_STREAM_STATE_DIR=temp_dir,
                    SOREN_DIRECT_STREAM_LOCAL_URL="rtmp://127.0.0.1:1935/private-name/live",
                )
            )
            self.assertNotIn("local_rtmp_url", config.public_dict())
            status_path = Path(temp_dir) / "status.json"
            status_path.write_text(
                json.dumps({"running": False, "output_url": "must-not-survive", "state": "completed"}),
                encoding="utf-8",
            )
            status = direct_stream.read_status(config)
            self.assertNotIn("output_url", status)

    def test_progress_parser_normalizes_numeric_fields(self) -> None:
        parsed = direct_stream.parse_progress_lines(
            [
                "frame=300\n",
                "fps=29.97\n",
                "dup_frames=2\n",
                "drop_frames=1\n",
                "bitrate=4510.2kbits/s\n",
                "speed=0.998x\n",
                "progress=continue\n",
            ]
        )
        self.assertEqual(parsed["frame"], 300)
        self.assertEqual(parsed["fps"], 29.97)
        self.assertEqual(parsed["dup_frames"], 2)
        self.assertEqual(parsed["drop_frames"], 1)
        self.assertEqual(parsed["speed"], 0.998)

    def test_operator_stop_is_successful_while_preserving_raw_ffmpeg_exit(self) -> None:
        runner_exit, state = direct_stream.classify_ffmpeg_exit(255, stopping=True)
        self.assertEqual((runner_exit, state), (0, "stopped"))

        runner_exit, state = direct_stream.classify_ffmpeg_exit(255, stopping=False)
        self.assertEqual((runner_exit, state), (255, "failed"))

    def test_live_relay_check_uses_only_the_validated_loopback_endpoint(self) -> None:
        config = direct_stream.load_config(
            base_env(SOREN_DIRECT_STREAM_LOCAL_URL="rtmp://127.0.0.1:21935/soren/live")
        )
        connection = mock.Mock()
        with mock.patch.object(direct_stream.socket, "create_connection", return_value=connection) as connect:
            direct_stream.validate_local_relay(config)
        connect.assert_called_once_with(("127.0.0.1", 21935), timeout=2)
        connection.close.assert_called_once_with()

    def test_unreachable_live_relay_fails_before_ffmpeg_start(self) -> None:
        config = direct_stream.load_config(base_env())
        with mock.patch.object(
            direct_stream.socket,
            "create_connection",
            side_effect=ConnectionRefusedError,
        ):
            with self.assertRaisesRegex(direct_stream.RuntimeCheckError, "relay is not reachable"):
                direct_stream.validate_local_relay(config)

    def test_cli_invalid_config_makes_no_state_or_output_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_dir = root / "state"
            output = root / "capture.mkv"
            env = os.environ.copy()
            env.update(
                base_env(
                    SOREN_DIRECT_STREAM_FPS="bad",
                    SOREN_DIRECT_STREAM_STATE_DIR=str(state_dir),
                )
            )
            result = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "record",
                    "--output",
                    str(output),
                    "--duration",
                    "1",
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(state_dir.exists())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
