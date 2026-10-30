import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
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
        "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS": "150",
        "SOREN_DIRECT_STREAM_PULSE_SOURCE": "soren_null.monitor",
        "SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://127.0.0.1:1935/soren/live",
    }
    env.update(overrides)
    return env


class DirectStreamTests(unittest.TestCase):
    @staticmethod
    def write_proc(proc: Path, cpu: str, *, total: int = 1000, available: int = 500) -> None:
        proc.mkdir(parents=True, exist_ok=True)
        (proc / "stat").write_text(f"cpu {cpu}\n", encoding="ascii")
        (proc / "meminfo").write_text(
            f"MemTotal: {total} kB\nMemAvailable: {available} kB\n", encoding="ascii"
        )

    def test_system_metrics_initial_unknown_then_cpu_and_ram_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = Path(temp_dir)
            sampler = direct_stream.SystemMetricsSampler()
            self.write_proc(proc, "100 0 50 850 0 0 0 0 0 0")
            first = sampler.sample(now_epoch=100, now_monotonic=0, proc_root=proc, platform="linux")
            self.assertIsNone(first["cpu_percent"])
            self.assertEqual(first["memory_percent"], 50.0)
            self.assertEqual(first["memory_used_bytes"], 500 * 1024)
            self.assertGreater(first["disk_total_bytes"], 0)
            self.assertGreaterEqual(first["disk_free_bytes"], 0)
            self.assertLessEqual(first["disk_free_bytes"], first["disk_total_bytes"])
            self.assertGreaterEqual(first["disk_percent"], 0)
            self.assertLessEqual(first["disk_percent"], 100)

            skipped = sampler.sample(now_epoch=102, now_monotonic=2, proc_root=proc, platform="linux")
            self.assertIs(skipped, first, "reads are cadence limited")
            self.write_proc(proc, "125 0 75 900 0 0 0 0 0 0")
            second = sampler.sample(now_epoch=105, now_monotonic=5, proc_root=proc, platform="linux")
            self.assertEqual(second["cpu_percent"], 50.0)
            self.assertEqual(second["memory_percent"], 50.0)
            self.assertEqual([point["ts"] for point in second["history"]], [100, 105])

    def test_system_metrics_counter_reset_and_malformed_data_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = Path(temp_dir)
            sampler = direct_stream.SystemMetricsSampler()
            self.write_proc(proc, "100 0 50 850 0 0 0 0 0 0")
            sampler.sample(now_epoch=1, now_monotonic=0, proc_root=proc, platform="linux")
            self.write_proc(proc, "10 0 5 85 0 0 0 0 0 0")
            reset = sampler.sample(now_epoch=6, now_monotonic=5, proc_root=proc, platform="linux")
            self.assertIsNone(reset["cpu_percent"])
            (proc / "stat").write_text("cpu NaN broken\n", encoding="ascii")
            (proc / "meminfo").write_text("MemTotal: NaN kB\n", encoding="ascii")
            malformed = sampler.sample(now_epoch=11, now_monotonic=10, proc_root=proc, platform="linux")
            self.assertIsNone(malformed["cpu_percent"])
            self.assertIsNone(malformed["memory_percent"])
            self.assertIsNone(malformed["memory_used_bytes"])

    def test_system_metrics_are_unknown_off_linux_and_history_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = Path(temp_dir)
            sampler = direct_stream.SystemMetricsSampler()
            for index in range(40):
                self.write_proc(proc, f"{100 + index * 10} 0 50 {850 + index * 10} 0 0 0 0 0 0")
                result = sampler.sample(
                    now_epoch=100 + index * 5,
                    now_monotonic=index * 5,
                    proc_root=proc,
                    platform="darwin",
                )
            self.assertIsNone(result["cpu_percent"])
            self.assertIsNone(result["memory_percent"])
            self.assertIsNone(result["disk_percent"])
            self.assertIsNone(result["disk_free_bytes"])
            self.assertEqual(len(result["history"]), direct_stream.SYSTEM_METRICS_HISTORY_LIMIT)
            self.assertEqual(result["history"][0]["ts"], 120)

    def test_system_metrics_disk_matches_df_and_fails_closed(self) -> None:
        fake = type("Statvfs", (), {
            "f_frsize": 4096,
            "f_bsize": 4096,
            "f_blocks": 1000,
            "f_bfree": 200,
            "f_bavail": 100,
        })()
        sampler = direct_stream.SystemMetricsSampler()
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = Path(temp_dir)
            self.write_proc(proc, "100 0 50 850 0 0 0 0 0 0")
            with mock.patch.object(direct_stream.os, "statvfs", return_value=fake):
                result = sampler.sample(
                    now_epoch=100,
                    now_monotonic=0,
                    proc_root=proc,
                    disk_root=temp_dir,
                    platform="linux",
                )
        # df(1) divides by used + user-available, not the raw total.
        self.assertEqual(result["disk_total_bytes"], 4096 * 1000)
        self.assertEqual(result["disk_used_bytes"], 4096 * 800)
        self.assertEqual(result["disk_free_bytes"], 4096 * 100)
        self.assertEqual(result["disk_percent"], 88.9)
        self.assertEqual(result["history"][-1]["disk_percent"], 88.9)

    def test_system_metrics_disk_failure_keeps_unknown(self) -> None:
        sampler = direct_stream.SystemMetricsSampler()
        with tempfile.TemporaryDirectory() as temp_dir:
            proc = Path(temp_dir)
            self.write_proc(proc, "100 0 50 850 0 0 0 0 0 0")
            with mock.patch.object(direct_stream.os, "statvfs", side_effect=OSError("gone")):
                result = sampler.sample(
                    now_epoch=100,
                    now_monotonic=0,
                    proc_root=proc,
                    disk_root=temp_dir,
                    platform="linux",
                )
        self.assertIsNone(result["disk_percent"])
        self.assertIsNone(result["disk_used_bytes"])
        self.assertIsNone(result["disk_total_bytes"])
        self.assertIsNone(result["disk_free_bytes"])

    def test_live_command_has_expected_video_audio_and_loopback_output(self) -> None:
        config = direct_stream.load_config(base_env())
        command = direct_stream.build_ffmpeg_command(config, mode="live")
        joined = " ".join(command)
        self.assertNotIn("-nostdin", joined)
        self.assertIn("-f x11grab", joined)
        self.assertIn("-draw_mouse 0", joined)
        self.assertIn("-framerate 30", joined)
        self.assertIn("-video_size 1280x720", joined)
        self.assertIn("-f pulse", joined)
        self.assertIn("soren_null.monitor", command)
        self.assertIn("adelay=150:all=1,aresample=async=1:first_pts=0", command)
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("-g 60", joined)
        self.assertEqual(command[-1], "rtmp://127.0.0.1:1935/soren/live")
        self.assertNotIn("-a53cc", command)
        self.assertFalse(any("docichcc" in argument for argument in command))

    def test_audio_input_preserves_video_clock_origin(self) -> None:
        # Device probing opens Pulse after X11; independent zero origins make
        # newer audio play alongside older video. Bind input 1 to input 0.
        config = direct_stream.load_config(base_env())
        command = direct_stream.build_ffmpeg_command(config, mode="live")
        inputs = [i for i, value in enumerate(command) if value == "-i"]
        audio_options = command[inputs[0] + 2:inputs[1]]
        self.assertIn("-isync", audio_options)
        self.assertEqual(audio_options[audio_options.index("-isync") + 1], "0")
        self.assertNotIn("-isync", command[:inputs[0]])

    def test_live_command_keeps_stdin_available_for_operator_stop(self) -> None:
        """The FFmpeg argv must not disable stdin so stop can send q first."""
        config = direct_stream.load_config(base_env())
        command = direct_stream.build_ffmpeg_command(config, mode="live")
        joined = " ".join(command)
        self.assertNotIn("-nostdin", joined)
        self.assertNotIn("-stdin", joined)

    def test_caption_command_is_feature_flagged_and_uses_validated_socket(self) -> None:
        config = direct_stream.load_config(
            base_env(
                DOCICH_CC_ENABLED="1",
                DOCICH_CC_SOCKET="/tmp/docich-private/cc.sock",
            )
        )
        command = direct_stream.build_ffmpeg_command(config, mode="live")
        self.assertIn("docichcc=socket=/tmp/docich-private/cc.sock", command)
        self.assertIn("-a53cc", command)
        self.assertEqual(command[command.index("-a53cc") + 1], "1")

        fallback = direct_stream.build_ffmpeg_command(
            config, mode="live", captions_active=False
        )
        self.assertNotIn("-a53cc", fallback)
        self.assertFalse(any("docichcc" in argument for argument in fallback))

    def test_caption_config_rejects_filter_injection_and_long_socket_paths(self) -> None:
        for socket_path in (
            "/tmp/cc.sock,drawtext=text=bad",
            "/tmp/cc.sock:bad",
            "/tmp/../cc.sock",
            "/" + ("a" * 104),
            "relative/cc.sock",
        ):
            with self.subTest(socket_path=socket_path):
                with self.assertRaises(direct_stream.ConfigError):
                    direct_stream.load_config(
                        base_env(DOCICH_CC_SOCKET=socket_path)
                    )

    def test_caption_capability_check_fails_open(self) -> None:
        config = direct_stream.load_config(base_env(DOCICH_CC_ENABLED="1"))
        with mock.patch.object(
            direct_stream,
            "_checked",
            side_effect=[" ... copy V->V\n", "  -a53cc <boolean>\n"],
        ):
            self.assertFalse(direct_stream.caption_capabilities_available(config))

        with mock.patch.object(
            direct_stream,
            "_checked",
            side_effect=[
                " ... docichcc V->V\n",
                "  -a53cc <boolean> use A53 closed captions\n",
            ],
        ):
            self.assertTrue(direct_stream.caption_capabilities_available(config))

    def test_caption_runtime_directory_is_private(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            socket_path = Path(temp_dir) / "private" / "cc.sock"
            config = direct_stream.load_config(
                base_env(
                    DOCICH_CC_ENABLED="1",
                    DOCICH_CC_SOCKET=str(socket_path),
                )
            )
            self.assertTrue(direct_stream.prepare_caption_runtime(config))
            self.assertEqual(
                socket_path.parent.stat().st_mode & 0o777,
                0o700,
            )

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
            ("SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", "2001"),
            ("SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", "150ms"),
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

    def test_graceful_stop_sends_q_then_sigint_then_sigkill(self) -> None:
        """Operator stop must prefer stdin q and only escalate on timeout."""
        child = mock.Mock()
        child.stdin = mock.Mock()
        poll_results = iter([0])
        child.poll.side_effect = lambda: next(poll_results, None)

        calls: list[tuple[str, object]] = []

        def fake_write(data: object) -> None:
            calls.append(("write", data))

        child.stdin.write.side_effect = fake_write

        def fake_send_signal(sig: object) -> None:
            calls.append(("signal", sig))

        child.send_signal.side_effect = fake_send_signal
        child.kill.side_effect = lambda: calls.append(("kill", None))

        direct_stream._graceful_stop_ffmpeg(
            child,
            q_timeout_sec=0.2,
            sigint_timeout_sec=0.2,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(calls) < 1:
            time.sleep(0.05)
        time.sleep(0.4)

        self.assertEqual(
            calls,
            [("write", "q\n")],
        )
        child.send_signal.assert_not_called()
        child.kill.assert_not_called()

    def test_graceful_stop_escalates_to_sigint_and_sigkill_on_timeout(self) -> None:
        child = mock.Mock()
        child.stdin = mock.Mock()
        child.poll.return_value = None

        calls: list[tuple[str, object]] = []
        child.stdin.write.side_effect = lambda data: calls.append(("write", data))

        def fake_send_signal(sig: object) -> None:
            calls.append(("signal", sig))

        child.send_signal.side_effect = fake_send_signal
        child.kill.side_effect = lambda: calls.append(("kill", None))

        direct_stream._graceful_stop_ffmpeg(
            child,
            q_timeout_sec=0.1,
            sigint_timeout_sec=0.1,
        )
        time.sleep(1.0)

        self.assertIn(("write", "q\n"), calls)
        self.assertIn(("signal", signal.SIGINT), calls)
        self.assertIn(("kill", None), calls)
        self.assertEqual(
            calls.index(("write", "q\n")),
            0,
        )
        self.assertLess(
            calls.index(("signal", signal.SIGINT)),
            calls.index(("kill", None)),
        )

    def test_graceful_stop_skips_signals_when_child_exits_after_q(self) -> None:
        child = mock.Mock()
        child.stdin = mock.Mock()
        child.poll.return_value = 0
        child.send_signal = mock.Mock()
        child.kill = mock.Mock()

        direct_stream._graceful_stop_ffmpeg(
            child,
            q_timeout_sec=0.2,
            sigint_timeout_sec=0.2,
        )
        time.sleep(0.5)

        child.stdin.write.assert_called_once_with("q\n")
        child.send_signal.assert_not_called()
        child.kill.assert_not_called()

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

    def test_reconnect_config_defaults_and_strict_validation(self) -> None:
        config = direct_stream.load_reconnect_config(
            {"TWITCH_CHANNEL": "dociai"}
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.twitch_channel, "dociai")
        self.assertEqual(config.probe_interval_sec, 60)
        self.assertEqual(config.offline_threshold, 2)
        self.assertEqual(config.backoff_sec, 10)
        self.assertEqual(config.max_consecutive_restarts, 10)
        self.assertTrue(config.reload_relay)

        with self.assertRaises(direct_stream.ConfigError):
            direct_stream.load_reconnect_config(
                {"SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD": "0"}
            )
        with self.assertRaises(direct_stream.ConfigError):
            direct_stream.load_reconnect_config(
                {"SOREN_DIRECT_STREAM_RECONNECT_ENABLED": "maybe"}
            )
        with self.assertRaises(direct_stream.ConfigError):
            direct_stream.load_reconnect_config(
                {"SOREN_DIRECT_STREAM_TWITCH_CHANNEL": "bad name!"}
            )

    def test_twitch_channel_is_live_parses_public_gql(self) -> None:
        channel = "dociai"
        request_capture: list[object] = []

        def fake_urlopen(request, timeout):
            request_capture.append(request)
            response = mock.MagicMock()
            response.read.return_value = json.dumps(
                {"data": {"user": {"stream": {"id": "1"}}}}
            ).encode("utf-8")
            response.__enter__.return_value = response
            return response

        with mock.patch.object(
            direct_stream.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            self.assertTrue(direct_stream.twitch_channel_is_live(channel))
        request = request_capture[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertIn("dociai", body["query"])
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.assertEqual(
            headers.get("client-id"), direct_stream.TWITCH_WEB_CLIENT_ID
        )

    def test_twitch_channel_is_live_handles_offline_and_probe_failures(self) -> None:
        offline_response = mock.MagicMock()
        offline_response.read.return_value = json.dumps(
            {"data": {"user": {"stream": None}}}
        ).encode("utf-8")
        offline_response.__enter__.return_value = offline_response
        with mock.patch.object(
            direct_stream.urllib.request,
            "urlopen",
            return_value=offline_response,
        ):
            self.assertFalse(direct_stream.twitch_channel_is_live("dociai"))

        with mock.patch.object(
            direct_stream.urllib.request,
            "urlopen",
            side_effect=direct_stream.urllib.error.URLError("boom"),
        ):
            self.assertIsNone(direct_stream.twitch_channel_is_live("dociai"))

        self.assertIsNone(direct_stream.twitch_channel_is_live(""))

    def test_reconnect_monitor_requests_reconnect_after_offline_streak(self) -> None:
        reconnect = direct_stream.load_reconnect_config(
            {
                "TWITCH_CHANNEL": "dociai",
                "SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC": "10",
                "SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD": "2",
            }
        )
        child = mock.Mock()
        child.pid = 12345
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
            "offline_streak": 0,
            "last_live": None,
        }
        config = direct_stream.load_config(base_env())

        with mock.patch.object(
            direct_stream, "twitch_channel_is_live", return_value=False
        ), mock.patch.object(direct_stream.time, "sleep"), mock.patch.object(
            direct_stream, "_pid_alive", return_value=True
        ):
            thread = direct_stream._start_reconnect_monitor(
                config, reconnect, child, monitor_state
            )
            thread.join(timeout=5)

        self.assertTrue(monitor_state["reconnect"])
        self.assertEqual(monitor_state["reconnect_reason"], "twitch_offline")
        child.send_signal.assert_called_with(signal.SIGINT)

    def test_reconnect_monitor_resets_streak_on_live(self) -> None:
        reconnect = direct_stream.load_reconnect_config(
            {
                "TWITCH_CHANNEL": "dociai",
                "SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC": "10",
                "SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD": "3",
            }
        )
        child = mock.Mock()
        child.pid = 12345
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
            "offline_streak": 0,
            "last_live": None,
        }
        config = direct_stream.load_config(base_env())
        probe_results = [False, True, False, False, False]

        with mock.patch.object(
            direct_stream,
            "twitch_channel_is_live",
            side_effect=lambda channel: probe_results.pop(0),
        ), mock.patch.object(direct_stream.time, "sleep"), mock.patch.object(
            direct_stream, "_pid_alive", return_value=True
        ):
            thread = direct_stream._start_reconnect_monitor(
                config, reconnect, child, monitor_state
            )
            thread.join(timeout=5)

        self.assertTrue(monitor_state["reconnect"])
        self.assertEqual(monitor_state["offline_streak"], 3)

    def test_run_ffmpeg_supervise_restarts_on_reconnect(self) -> None:
        config = direct_stream.load_config(
            base_env(SOREN_DIRECT_STREAM_STATE_DIR=tempfile.mkdtemp())
        )
        reconnect = direct_stream.load_reconnect_config(
            {
                "TWITCH_CHANNEL": "dociai",
                "SOREN_DIRECT_STREAM_RECONNECT_BACKOFF_SEC": "1",
            }
        )
        outcomes = [
            direct_stream.RunOutcome(
                exit_code=0,
                state="reconnect",
                started_at=1,
                reconnect_reason="twitch_offline",
                ffmpeg_exit_code=255,
            ),
            direct_stream.RunOutcome(
                exit_code=0,
                state="stopped",
                started_at=2,
                ffmpeg_exit_code=255,
            ),
        ]
        calls: list[direct_stream.RunOutcome] = []

        def fake_once(config_, command, *, mode, reconnect, captions_active=False, audio_watchdog=None):
            calls.append(outcomes.pop(0))
            return calls[-1]

        with mock.patch.object(
            direct_stream, "_run_ffmpeg_once", side_effect=fake_once
        ) as once, mock.patch.object(
            direct_stream, "_reload_relay", return_value=True
        ) as reload_relay, mock.patch.object(
            direct_stream, "validate_runtime"
        ):
            result = direct_stream._run_ffmpeg(
                config,
                mode="live",
                output_path=None,
                duration_sec=None,
                reconnect=reconnect,
            )

        self.assertEqual(result, 0)
        self.assertEqual(once.call_count, 2)
        self.assertEqual(calls[0].state, "reconnect")
        self.assertEqual(calls[1].state, "stopped")
        reload_relay.assert_called_once_with(config)

    def test_run_ffmpeg_supervise_stops_after_restart_limit(self) -> None:
        config = direct_stream.load_config(
            base_env(SOREN_DIRECT_STREAM_STATE_DIR=tempfile.mkdtemp())
        )
        reconnect = direct_stream.load_reconnect_config(
            {
                "TWITCH_CHANNEL": "dociai",
                "SOREN_DIRECT_STREAM_RECONNECT_BACKOFF_SEC": "1",
                "SOREN_DIRECT_STREAM_RECONNECT_MAX_CONSECUTIVE": "2",
            }
        )

        def fake_once(config_, command, *, mode, reconnect, captions_active=False, audio_watchdog=None):
            return direct_stream.RunOutcome(
                exit_code=0,
                state="reconnect",
                started_at=1,
                reconnect_reason="twitch_offline",
                ffmpeg_exit_code=255,
            )

        with mock.patch.object(
            direct_stream, "_run_ffmpeg_once", side_effect=fake_once
        ), mock.patch.object(direct_stream, "validate_runtime"):
            with self.assertRaisesRegex(
                direct_stream.RuntimeCheckError, "reconnect limit reached"
            ):
                direct_stream._run_ffmpeg(
                    config,
                    mode="live",
                    output_path=None,
                    duration_sec=None,
                    reconnect=reconnect,
                )

    def test_audio_capture_watchdog_config_defaults_and_validation(self) -> None:
        watchdog = direct_stream.load_audio_capture_watchdog_config({})
        self.assertTrue(watchdog.enabled)
        self.assertEqual(watchdog.probe_interval_sec, 10)
        self.assertEqual(watchdog.loss_threshold, 2)
        self.assertTrue(watchdog.pulse_server.startswith("unix:/run/user/"))

        custom = direct_stream.load_audio_capture_watchdog_config(
            {
                "PULSE_SERVER": "unix:/run/user/1001/pulse/native",
                "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED": "0",
                "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_PROBE_SEC": "30",
                "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_LOSS_THRESHOLD": "3",
            }
        )
        self.assertFalse(custom.enabled)
        self.assertEqual(custom.probe_interval_sec, 30)
        self.assertEqual(custom.loss_threshold, 3)
        self.assertEqual(custom.pulse_server, "unix:/run/user/1001/pulse/native")

        for bad_env in (
            {"PULSE_SERVER": "tcp:127.0.0.1:4713"},
            {"PULSE_SERVER": "unix:/run/user/../../etc/passwd"},
            {"SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_LOSS_THRESHOLD": "0"},
            {"SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_PROBE_SEC": "999"},
            {"SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED": "maybe"},
        ):
            with self.subTest(env=bad_env):
                with self.assertRaises(direct_stream.ConfigError):
                    direct_stream.load_audio_capture_watchdog_config(bad_env)

    def test_probe_audio_capture_state_classifies_capture_streams(self) -> None:
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )
        listing = (
            'Source Output #7\n'
            '\tSource: 3\n'
            '\tapplication.name = "ffmpeg"\n'
            '\tapplication.process.id = "4321"\n'
        )

        def run_ok(command, *, env=None):
            self.assertEqual(
                env.get("PULSE_SERVER"), "unix:/run/user/1001/pulse/native"
            )
            self.assertTrue(env.get("PATH"))
            return "" if command[1:] == ["info"] else listing

        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=run_ok
            ),
            "ok",
        )
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 9999, run=run_ok
            ),
            "missing",
        )

        def run_down(command, *, env=None):
            raise direct_stream.RuntimeCheckError("pulse down")

        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=run_down
            ),
            "unreachable",
        )

        def run_garbled(command, *, env=None):
            return "" if command[1:] == ["info"] else "unexpected listing"

        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=run_garbled
            ),
            "unknown",
        )

    def test_probe_never_reports_missing_when_a_pid_is_unavailable(self) -> None:
        # Review 2026-10-20: a listing that stops exposing
        # application.process.id (pactl version, restricted properties, a
        # stream reclaimed by module-stream-restore) must not be classified as
        # "missing". Doing so stops a healthy encoder and causes the very
        # outage the watchdog exists to prevent.
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )

        def listing_run(listing: str):
            def run(command, *, env=None):
                return "" if command[1:] == ["info"] else listing

            return run

        # A single unattributable stream: we cannot prove it is not ours.
        no_pid_attribute = 'Source Output #7\n\tSource: 3\n\tapplication.name = "ffmpeg"\n'
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=listing_run(no_pid_attribute)
            ),
            "unknown",
        )

        # One attributable (foreign) stream plus one unattributable stream:
        # still not provably ours, so never "missing".
        mixed = (
            'Source Output #7\n'
            '\tSource: 3\n'
            '\tapplication.process.id = "9999"\n'
            'Source Output #9\n'
            '\tSource: 3\n'
            '\tapplication.name = "ffmpeg"\n'
        )
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=listing_run(mixed)
            ),
            "unknown",
        )

        # Every stream is attributable and none is ours: a genuine loss.
        all_attributable = (
            'Source Output #7\n'
            '\tSource: 3\n'
            '\tapplication.process.id = "9999"\n'
            'Source Output #9\n'
            '\tSource: 3\n'
            '\tapplication.process.id = "8888"\n'
        )
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=listing_run(all_attributable)
            ),
            "missing",
        )

        # Our own stream among others is healthy even if another lacks a pid.
        ours_plus_unattributable = (
            'Source Output #7\n'
            '\tSource: 3\n'
            '\tapplication.process.id = "4321"\n'
            'Source Output #9\n'
            '\tSource: 3\n'
            '\tapplication.name = "ffmpeg"\n'
        )
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=listing_run(ours_plus_unattributable)
            ),
            "ok",
        )

        # The daemon answered and no capture stream exists at all.
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=listing_run("")
            ),
            "missing",
        )

    def test_empty_block_bodies_are_unknown_not_missing(self) -> None:
        # Review 2026-10-20: a "Source Output #N" header whose body is empty or
        # incomplete means the listing is unusable, not that no capture exists.
        # Discarding such blocks made an incomplete listing look like "no
        # capture", which signalled a healthy FFmpeg after two strikes.
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )

        def probe(listing: str) -> str:
            def run(command, *, env=None):
                return "" if command[1:] == ["info"] else listing

            return direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=run
            )

        # Headers without usable bodies are unattributable, never "missing".
        self.assertEqual(probe("Source Output #7\n"), "unknown")
        self.assertEqual(probe("Source Output #7\n\n"), "unknown")
        self.assertEqual(probe("Source Output #7\nSource Output #9\n"), "unknown")
        self.assertEqual(
            probe(
                'Source Output #7\nSource Output #9\n'
                '\tapplication.process.id = "9999"\n'
            ),
            "unknown",
        )

        # The reviewer's second shape (5581203436): a normal foreign block
        # FOLLOWED BY a trailing header-only block. Dropping the trailing header
        # would reduce this listing to a foreign "missing" and stop FFmpeg.
        self.assertEqual(
            probe(
                'Source Output #9\n'
                '\tapplication.process.id = "9999"\n'
                'Source Output #7\n'
            ),
            "unknown",
        )

        # A listing with no header at all still means "no capture stream".
        self.assertEqual(probe(""), "missing")
        self.assertEqual(probe("   \n"), "missing")

        # Transition: after a healthy observation, repeated incomplete listings
        # must never reach the strike threshold or signal FFmpeg. The monitor is
        # driven by the real classifier over those listings, so the parse and
        # the transition are covered together rather than by a "unknown" stub.
        child = mock.Mock()
        child.pid = 4321
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
        }
        healthy = 'Source Output #7\n\tapplication.process.id = "4321"\n'
        incomplete_listings = [
            "Source Output #7\n",
            'Source Output #9\n\tapplication.process.id = "9999"\nSource Output #7\n',
            "Source Output #7\nSource Output #9\n",
        ]
        sequence = [healthy] + incomplete_listings * 3
        checks = {"count": 0}

        def fake_sleep(_seconds: float) -> None:
            checks["count"] += 1
            if checks["count"] >= len(sequence):
                monitor_state["stopping"] = True

        def real_probe(_config, _watchdog, _pid):
            return probe(sequence.pop(0))

        thread = direct_stream._start_audio_capture_monitor(
            config,
            watchdog,
            child,
            monitor_state,
            probe=real_probe,
            sleep=fake_sleep,
            alive=lambda _pid: True,
        )
        thread.join(timeout=5)

        self.assertFalse(monitor_state["reconnect"])
        self.assertIsNone(monitor_state["reconnect_reason"])
        child.send_signal.assert_not_called()
        self.assertEqual(monitor_state["audio_capture"]["consecutive_unhealthy"], 0)

    def test_mixed_listing_with_our_pid_hidden_is_unknown(self) -> None:
        # Review 5580497362: a listing where our own block hides
        # application.process.id while a foreign recorder publishes PID 9999.
        # Attributing by "our pid is absent" here would stop a healthy encoder.
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )
        mixed = (
            'Source Output #7\n'
            '\tSource: 3\n'
            '\tapplication.name = "ffmpeg"\n'
            'Source Output #9\n'
            '\tSource: 3\n'
            '\tapplication.process.id = "9999"\n'
        )

        def run(command, *, env=None):
            return "" if command[1:] == ["info"] else mixed

        # Observed healthy first, then the same mixed listing repeatedly: the
        # monitor must never reach the strike threshold.
        self.assertEqual(
            direct_stream.probe_audio_capture_state(
                config, watchdog, 4321, run=run
            ),
            "unknown",
        )

        child = mock.Mock()
        child.pid = 4321
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
        }
        states = ["ok"] + ["unknown"] * 6
        checks = {"count": 0}

        def fake_sleep(_seconds: float) -> None:
            checks["count"] += 1
            if checks["count"] >= len(states):
                monitor_state["stopping"] = True

        thread = direct_stream._start_audio_capture_monitor(
            config,
            watchdog,
            child,
            monitor_state,
            probe=lambda *args, **kwargs: states.pop(0),
            sleep=fake_sleep,
            alive=lambda _pid: True,
        )
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(monitor_state["reconnect"])
        self.assertIsNone(monitor_state["reconnect_reason"])
        child.send_signal.assert_not_called()

    def test_unattributable_probe_never_stops_ffmpeg(self) -> None:
        # End-to-end: with the daemon answering but the pid attribute missing,
        # the monitor must not request a reconnect even after many probes.
        config = direct_stream.load_config(
            base_env(SOREN_DIRECT_STREAM_STATE_DIR=tempfile.mkdtemp())
        )
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )
        child = mock.Mock()
        child.pid = 4321
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
        }
        checks = {"count": 0}

        def fake_sleep(_seconds: float) -> None:
            checks["count"] += 1
            if checks["count"] >= 5:
                monitor_state["stopping"] = True

        thread = direct_stream._start_audio_capture_monitor(
            config,
            watchdog,
            child,
            monitor_state,
            probe=lambda *args, **kwargs: "unknown",
            sleep=fake_sleep,
            alive=lambda _pid: True,
        )
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(monitor_state["reconnect"])
        self.assertIsNone(monitor_state["reconnect_reason"])
        child.send_signal.assert_not_called()
        self.assertEqual(monitor_state["audio_capture"]["status"], "unknown")

    def test_audio_watchdog_restarts_ffmpeg_after_capture_loss(self) -> None:
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )
        child = mock.Mock()
        child.pid = 4321
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
        }
        states = ["ok", "missing", "missing"]

        with mock.patch.object(direct_stream.time, "sleep"):
            thread = direct_stream._start_audio_capture_monitor(
                config,
                watchdog,
                child,
                monitor_state,
                probe=lambda *args, **kwargs: states.pop(0),
                alive=lambda _pid: True,
            )
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertTrue(monitor_state["reconnect"])
        self.assertEqual(monitor_state["reconnect_reason"], "pulse_capture_lost")
        child.send_signal.assert_called_once_with(signal.SIGINT)
        capture = monitor_state["audio_capture"]
        self.assertEqual(capture["status"], "missing")
        self.assertEqual(capture["source"], "soren_null.monitor")
        self.assertGreaterEqual(capture["consecutive_unhealthy"], 2)
        self.assertEqual(states, [])

    def test_audio_watchdog_keeps_video_while_pulse_is_unreachable(self) -> None:
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=2,
            pulse_server="unix:/run/user/1001/pulse/native",
        )
        child = mock.Mock()
        child.pid = 4321
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
        }
        checks = {"count": 0}

        def fake_sleep(_seconds: float) -> None:
            checks["count"] += 1
            if checks["count"] >= 4:
                monitor_state["stopping"] = True

        thread = direct_stream._start_audio_capture_monitor(
            config,
            watchdog,
            child,
            monitor_state,
            probe=lambda *args, **kwargs: "unreachable",
            sleep=fake_sleep,
            alive=lambda _pid: True,
        )
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(monitor_state["reconnect"])
        self.assertIsNone(monitor_state["reconnect_reason"])
        child.send_signal.assert_not_called()
        self.assertEqual(monitor_state["audio_capture"]["status"], "unreachable")

    def test_audio_watchdog_never_restarts_without_an_observed_capture(self) -> None:
        config = direct_stream.load_config(base_env())
        watchdog = direct_stream.AudioCaptureWatchdogConfig(
            enabled=True,
            probe_interval_sec=10,
            loss_threshold=1,
            pulse_server="unix:/run/user/1001/pulse/native",
        )
        child = mock.Mock()
        child.pid = 4321
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
        }
        checks = {"count": 0}

        def fake_sleep(_seconds: float) -> None:
            checks["count"] += 1
            if checks["count"] >= 3:
                monitor_state["stopping"] = True

        thread = direct_stream._start_audio_capture_monitor(
            config,
            watchdog,
            child,
            monitor_state,
            probe=lambda *args, **kwargs: "missing",
            sleep=fake_sleep,
            alive=lambda _pid: True,
        )
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(monitor_state["reconnect"])
        child.send_signal.assert_not_called()

    def test_run_ffmpeg_passes_the_audio_watchdog_to_each_run(self) -> None:
        config = direct_stream.load_config(
            base_env(SOREN_DIRECT_STREAM_STATE_DIR=tempfile.mkdtemp())
        )
        reconnect = direct_stream.load_reconnect_config(
            {
                "TWITCH_CHANNEL": "dociai",
                "SOREN_DIRECT_STREAM_RECONNECT_BACKOFF_SEC": "1",
            }
        )
        watchdog = direct_stream.load_audio_capture_watchdog_config({})
        seen: list[object] = []

        def fake_once(
            config_, command, *, mode, reconnect, captions_active=False,
            audio_watchdog=None,
        ):
            seen.append(audio_watchdog)
            return direct_stream.RunOutcome(
                exit_code=0, state="stopped", started_at=1, ffmpeg_exit_code=0
            )

        with mock.patch.object(
            direct_stream, "_run_ffmpeg_once", side_effect=fake_once
        ), mock.patch.object(direct_stream, "validate_runtime"):
            result = direct_stream._run_ffmpeg(
                config,
                mode="live",
                output_path=None,
                duration_sec=None,
                reconnect=reconnect,
                audio_watchdog=watchdog,
            )

        self.assertEqual(result, 0)
        self.assertEqual(seen, [watchdog])


class EnsurePrivateDirectoryTests(unittest.TestCase):
    """Regression: diagnostics refuses 0o022 directories under the fixed sources."""

    def test_created_levels_are_private_even_with_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "state" / "direct_stream"
            previous = os.umask(0o002)
            try:
                direct_stream._ensure_private_directory(target)
            finally:
                os.umask(previous)
            for level in (target.parent, target):
                self.assertTrue(level.is_dir())
                self.assertEqual(stat.S_IMODE(level.stat().st_mode), 0o700)

    def test_existing_group_writable_directory_self_heals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "logs"
            target.mkdir(mode=0o775)
            direct_stream._ensure_private_directory(target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_foreign_owned_directory_is_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "logs"
            previous = os.umask(0o000)
            try:
                target.mkdir(mode=0o775)
            finally:
                os.umask(previous)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o775)
            with mock.patch.object(direct_stream.os, "geteuid", return_value=-1):
                direct_stream._ensure_private_directory(target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o775)

    def test_non_directory_path_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "state"
            target.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                direct_stream._ensure_private_directory(target)


if __name__ == "__main__":
    unittest.main()
