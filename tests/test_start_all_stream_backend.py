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
    youtube_guard: str | None = None,
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
    if youtube_guard is None:
        env.pop("YOUTUBE_BROADCAST_GUARD_ENABLED", None)
    else:
        env["YOUTUBE_BROADCAST_GUARD_ENABLED"] = youtube_guard
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
        self.assertNotIn("youtube_broadcast_guard", config["workers"])

    def test_ffmpeg_can_supervise_youtube_broadcast_guard(self) -> None:
        result = print_worker_config("ffmpeg", youtube_guard="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["workers"].count("youtube_broadcast_guard"), 1)

    def test_obs_never_starts_youtube_broadcast_guard(self) -> None:
        result = print_worker_config("obs", youtube_guard="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertNotIn("youtube_broadcast_guard", config["workers"])

    def test_invalid_youtube_guard_flag_fails_closed(self) -> None:
        result = print_worker_config("ffmpeg", youtube_guard="yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("YOUTUBE_BROADCAST_GUARD_ENABLED must be 0 or 1", result.stderr)

    def test_improvement_daemon_is_always_supervised_exactly_once(self) -> None:
        for backend in ("obs", "ffmpeg"):
            with self.subTest(backend=backend):
                result = print_worker_config(backend)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads(result.stdout)
                self.assertEqual(config["workers"].count("improve_daemon"), 1)

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
        watchdog_guard = "soviet_watchdog|status_overlay_watch|show_status_overlay_watch|soren_overlay_watch)"
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


if __name__ == "__main__":
    unittest.main()
