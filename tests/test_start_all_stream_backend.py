import json
import os
from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
START_ALL = REPO_ROOT / "start_all.sh"


def print_worker_config(
    backend: str | None = None,
    *,
    bridge_watchdog: str | None = None,
    overlay_watchers: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SOREN_ENV_FILE"] = str(REPO_ROOT / "tests" / "missing.env")
    if backend is None:
        env.pop("SOREN_STREAM_BACKEND", None)
    else:
        env["SOREN_STREAM_BACKEND"] = backend
    if bridge_watchdog is None:
        env.pop("SOREN_SOVIET_WATCHDOG_ENABLED", None)
    else:
        env["SOREN_SOVIET_WATCHDOG_ENABLED"] = bridge_watchdog
    if overlay_watchers is None:
        env.pop("SOREN_STATUS_OVERLAY_WATCHERS_ENABLED", None)
    else:
        env["SOREN_STATUS_OVERLAY_WATCHERS_ENABLED"] = overlay_watchers
    return subprocess.run(
        ["bash", str(START_ALL), "--print-worker-config"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class StartAllStreamBackendTests(unittest.TestCase):
    def test_obs_is_the_safe_default(self) -> None:
        result = print_worker_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["backend"], "obs")
        self.assertIn("obs_capture_watchdog", config["workers"])
        self.assertNotIn("direct_stream", config["workers"])

    def test_ffmpeg_replaces_only_the_obs_watchdog_worker(self) -> None:
        result = print_worker_config("ffmpeg")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["backend"], "ffmpeg")
        self.assertIn("direct_stream", config["workers"])
        self.assertNotIn("obs_capture_watchdog", config["workers"])
        self.assertIn("soren_loop", config["workers"])
        self.assertIn("audio_worker", config["workers"])

    def test_unknown_backend_fails_before_supervisor_mutation(self) -> None:
        result = print_worker_config("unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be obs or ffmpeg", result.stderr)

    def test_linux_runtime_can_supervise_the_existing_bridge_watchdog(self) -> None:
        result = print_worker_config("obs", bridge_watchdog="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["workers"][0], "soviet_watchdog")
        self.assertIn("soren_loop", config["workers"])

    def test_invalid_bridge_watchdog_flag_fails_before_supervisor_mutation(self) -> None:
        result = print_worker_config("obs", bridge_watchdog="yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("SOREN_SOVIET_WATCHDOG_ENABLED must be 0 or 1", result.stderr)

    def test_bridge_watchdog_never_adopts_a_pgrep_fallback(self) -> None:
        source = START_ALL.read_text(encoding="utf-8")
        watchdog_guard = "soviet_watchdog|status_overlay_watch|show_status_overlay_watch)"
        self.assertIn(watchdog_guard, source)
        self.assertLess(source.index(watchdog_guard), source.index('pid=$(pgrep -f "$pattern"'))

    def test_linux_runtime_supervises_status_overlay_watchers_without_tmux(self) -> None:
        result = print_worker_config("obs", bridge_watchdog="1", overlay_watchers="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertIn("status_overlay_watch", config["workers"])
        self.assertIn("show_status_overlay_watch", config["workers"])
        self.assertIn("soviet_watchdog", config["workers"])

    def test_invalid_overlay_watchers_flag_fails_before_supervisor_mutation(self) -> None:
        result = print_worker_config("obs", overlay_watchers="yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("SOREN_STATUS_OVERLAY_WATCHERS_ENABLED must be 0 or 1", result.stderr)

    def test_linux_profile_prepares_direct_stream_without_enabling_it(self) -> None:
        profile = (REPO_ROOT / "configure_linux_stream_profile.sh").read_text(encoding="utf-8")
        self.assertIn("set_env_value SOREN_STREAM_BACKEND obs", profile)
        self.assertIn("--prepare-direct-stream-only", profile)
        self.assertIn('if [ "$PREPARE_DIRECT_ONLY" -eq 1 ]; then', profile)
        self.assertIn("set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720", profile)
        self.assertIn("set_env_value SOREN_DIRECT_STREAM_FPS 30", profile)
        self.assertIn("set_env_value SOREN_DIRECT_STREAM_PULSE_SOURCE soren_null.monitor", profile)
        self.assertIn("set_env_value SOREN_DIRECT_STREAM_LOCAL_URL rtmp://127.0.0.1:1935/soren/live", profile)
        self.assertIn("set_env_value SOREN_DIRECT_CUTOVER_WARMUP_SEC 15", profile)
        self.assertIn("set_env_value SOREN_DIRECT_OVERLAY_ENABLED 1", profile)
        self.assertIn("install_soren_runtime_service.sh", profile)
        self.assertIn("stream_backend_condition.sh", profile)
        self.assertIn("wait_soren_runtime_prereqs.sh", profile)
        self.assertIn("soren-runtime.service", profile)
        self.assertIn("set_env_value SOREN_SOVIET_WATCHDOG_ENABLED 1", profile)
        self.assertIn("set_env_value SOREN_STATUS_OVERLAY_WATCHERS_ENABLED 1", profile)
        self.assertIn("required_commands=(python3 node ffmpeg ffprobe pactl paplay xdpyinfo systemctl ss zsh nproc)", profile)
        self.assertLess(profile.index("required_commands=("), profile.index('backup="${ENV_FILE}'))
        self.assertIn('if [ "$(nproc)" -le 2 ]; then', profile)
        self.assertIn("game_internal_size=480,270", profile)
        self.assertIn("game_internal_size=576,324", profile)
        self.assertIn('profile_internal_size="${SOREN_PROFILE_GAME_INTERNAL_SIZE:-}"', profile)
        self.assertIn(
            "576,324|640,360|704,396|768,432|832,468|896,504|960,540|"
            "1024,576|1088,612|1152,648|1216,684|1280,720",
            profile,
        )
        self.assertLess(
            profile.index('profile_internal_size="${SOREN_PROFILE_GAME_INTERNAL_SIZE:-}"'),
            profile.index('backup="${ENV_FILE}'),
        )


if __name__ == "__main__":
    unittest.main()
