import importlib.util
import os
from pathlib import Path
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
        "SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://127.0.0.1:1935/soren/live",
    }
    env.update(overrides)
    return env


def write_fake_pactl(directory: Path, *, fail: bool = False) -> Path:
    """Build a pactl stub whose stdout is driven by SOREN_FAKE_PACTL_MODE."""
    script = directory / "pactl"
    script.write_text(
        """#!/bin/bash
printf '%s\\n' "$*" >>"$FAKE_PACTL_LOG"
if [ "${FAKE_PACTL_FAIL:-0}" = "1" ]; then
  exit 3
fi
case "$FAKE_PACTL_MODE" in
  missing_monitor)
    if [ "$1 $2 $3" = "list short sources" ]; then
      printf '1\\tunrelated\\tmodule.c\\ts16le 2ch 48000Hz\\n'
      printf '2\\tanother\\tmodule.c\\ts16le 2ch 48000Hz\\n'
    else
      printf 'Source Output #7\\n\\tSource: 2\\n'
    fi
    ;;
  attached)
    if [ "$1 $2 $3" = "list short sources" ]; then
      printf '1\\tunrelated\\tmodule.c\\ts16le 2ch 48000Hz\\n'
      printf '2\\tsoren_null.monitor\\tmodule-null-sink.c\\ts16le 2ch 48000Hz\\n'
    else
      printf 'Source Output #7\\n\\tSource: 2\\n'
      printf 'Source Output #9\\n\\tSource: 1\\n'
    fi
    ;;
  detached)
    if [ "$1 $2 $3" = "list short sources" ]; then
      printf '2\\tsoren_null.monitor\\tmodule-null-sink.c\\ts16le 2ch 48000Hz\\n'
    else
      printf 'Source Output #9\\n\\tSource: 1\\n'
    fi
    ;;
  none)
    if [ "$1 $2 $3" = "list short sources" ]; then
      printf '2\\tsoren_null.monitor\\tmodule-null-sink.c\\ts16le 2ch 48000Hz\\n'
    fi
    ;;
  unparseable)
    if [ "$1 $2 $3" = "list short sources" ]; then
      printf '2\\tsoren_null.monitor\\tmodule-null-sink.c\\ts16le 2ch 48000Hz\\n'
    else
      printf 'Source Output #7\\n\\tOwner Module: n/a\\n'
    fi
    ;;
  *) exit 4 ;;
esac
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    assert not fail or True  # fail is driven via FAKE_PACTL_FAIL at runtime
    return script


class PulseWatchdogProbeTests(unittest.TestCase):
    def _probe(self, mode: str, *, fail: bool = False) -> bool | None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_fake_pactl(fake_bin)
            log = root / "pactl.log"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "FAKE_PACTL_LOG": str(log),
                    "FAKE_PACTL_MODE": mode,
                    "FAKE_PACTL_FAIL": "1" if fail else "0",
                }
            )
            with mock.patch.dict(os.environ, env, clear=True):
                return direct_stream.pulse_source_output_present("soren_null.monitor")

    def test_attached_source_output_is_present(self) -> None:
        self.assertIs(self._probe("attached"), True)

    def test_source_output_on_another_source_is_not_present(self) -> None:
        self.assertIs(self._probe("detached"), False)

    def test_missing_monitor_source_is_not_present(self) -> None:
        self.assertIs(self._probe("missing_monitor"), False)

    def test_no_source_outputs_at_all_is_not_present(self) -> None:
        self.assertIs(self._probe("none"), False)

    def test_unparseable_long_form_is_unknown_and_never_false(self) -> None:
        self.assertIsNone(self._probe("unparseable"))

    def test_pactl_failure_is_unknown(self) -> None:
        self.assertIsNone(self._probe("attached", fail=True))

    def test_missing_pactl_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"PATH": str(temp_dir)}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertIsNone(
                    direct_stream.pulse_source_output_present("soren_null.monitor")
                )


class PulseWatchdogConfigTests(unittest.TestCase):
    def test_defaults_are_enabled_with_bounded_cadence(self) -> None:
        config = direct_stream.load_pulse_watchdog_config(base_env())
        self.assertTrue(config.enabled)
        self.assertEqual(config.interval_sec, 5)
        self.assertEqual(config.strikes, 3)
        self.assertEqual(
            config.public_dict(),
            {"enabled": True, "interval_sec": 5, "strikes": 3},
        )

    def test_overrides_and_bounds(self) -> None:
        config = direct_stream.load_pulse_watchdog_config(
            base_env(
                SOREN_DIRECT_STREAM_PULSE_WATCHDOG_ENABLED="0",
                SOREN_DIRECT_STREAM_PULSE_WATCHDOG_INTERVAL_SEC="60",
                SOREN_DIRECT_STREAM_PULSE_WATCHDOG_STRIKES="1",
            )
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.interval_sec, 60)
        self.assertEqual(config.strikes, 1)
        with self.assertRaises(direct_stream.ConfigError):
            direct_stream.load_pulse_watchdog_config(
                base_env(SOREN_DIRECT_STREAM_PULSE_WATCHDOG_INTERVAL_SEC="0")
            )
        with self.assertRaises(direct_stream.ConfigError):
            direct_stream.load_pulse_watchdog_config(
                base_env(SOREN_DIRECT_STREAM_PULSE_WATCHDOG_STRIKES="31")
            )


class PulseWatchdogThreadTests(unittest.TestCase):
    @staticmethod
    def _child() -> subprocess.Popen[str]:
        return subprocess.Popen(
            ["sleep", "30"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )

    @staticmethod
    def _ds_config(root: Path) -> direct_stream.DirectStreamConfig:
        return direct_stream.DirectStreamConfig(
            backend="ffmpeg",
            display=":99",
            width=1280,
            height=720,
            fps=30,
            video_kbps=4500,
            audio_kbps=160,
            audio_delay_ms=0,
            pulse_source="soren_null.monitor",
            pulse_sample_rate=48000,
            pulse_channels=2,
            local_rtmp_url="rtmp://127.0.0.1:1935/soren/live",
            ffmpeg_bin="ffmpeg",
            closed_captions_enabled=False,
            closed_captions_socket=Path("/tmp/cc.sock"),
            state_dir=root / "state",
            log_file=root / "log",
        )

    def test_confirmed_loss_requests_reconnect_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            config = direct_stream.PulseWatchdogConfig(
                enabled=True, interval_sec=0.02, strikes=3
            )
            child = self._child()
            monitor_state: dict[str, object] = {
                "stopping": False,
                "reconnect": False,
                "reconnect_reason": None,
            }
            with mock.patch.object(
                direct_stream, "pulse_source_output_present", return_value=False
            ), mock.patch.object(direct_stream.shutil, "which", return_value="/usr/bin/pactl"):
                thread = direct_stream._start_pulse_watchdog(
                    self._ds_config(Path(temp_dir)),
                    config,
                    child,
                    monitor_state,
                )
                self.assertIsNotNone(thread)
                thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertIs(monitor_state["reconnect"], True)
            self.assertEqual(monitor_state["reconnect_reason"], "pulse_input_lost")
            self.assertEqual(monitor_state["pulse_watchdog"]["missing_strikes"], 3)
            child.wait(timeout=10)
            self.assertNotEqual(child.returncode, 0)
            if child.stdin:
                child.stdin.close()

    def test_present_audio_never_requests_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = direct_stream.PulseWatchdogConfig(
                enabled=True, interval_sec=0.02, strikes=2
            )
            child = self._child()
            monitor_state: dict[str, object] = {
                "stopping": False,
                "reconnect": False,
                "reconnect_reason": None,
            }
            with mock.patch.object(
                direct_stream, "pulse_source_output_present", return_value=True
            ), mock.patch.object(direct_stream.shutil, "which", return_value="/usr/bin/pactl"):
                thread = direct_stream._start_pulse_watchdog(
                    self._ds_config(Path(temp_dir)), config, child, monitor_state
                )
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not monitor_state.get("pulse_watchdog"):
                    time.sleep(0.01)
                monitor_state["stopping"] = True
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertIs(monitor_state["reconnect"], False)
            self.assertEqual(monitor_state["pulse_watchdog"]["missing_strikes"], 0)
            child.kill()
            child.wait(timeout=10)

    def test_unknown_probe_result_does_not_accumulate_strikes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = direct_stream.PulseWatchdogConfig(
                enabled=True, interval_sec=0.02, strikes=2
            )
            child = self._child()
            monitor_state: dict[str, object] = {
                "stopping": False,
                "reconnect": False,
                "reconnect_reason": None,
            }
            with mock.patch.object(
                direct_stream, "pulse_source_output_present", return_value=None
            ), mock.patch.object(direct_stream.shutil, "which", return_value="/usr/bin/pactl"):
                thread = direct_stream._start_pulse_watchdog(
                    self._ds_config(Path(temp_dir)), config, child, monitor_state
                )
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not monitor_state.get("pulse_watchdog"):
                    time.sleep(0.01)
                monitor_state["stopping"] = True
                thread.join(timeout=5)
            self.assertIs(monitor_state["reconnect"], False)
            self.assertEqual(monitor_state["pulse_watchdog"]["missing_strikes"], 0)
            child.kill()
            child.wait(timeout=10)

    def test_missing_pactl_disables_the_thread(self) -> None:
        child = self._child()
        try:
            with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
                direct_stream.shutil, "which", return_value=None
            ):
                thread = direct_stream._start_pulse_watchdog(
                    self._ds_config(Path(temp_dir)),
                    direct_stream.PulseWatchdogConfig(
                        enabled=True, interval_sec=0.02, strikes=1
                    ),
                    child,
                    {"stopping": False, "reconnect": False},
                )
            self.assertIsNone(thread)
        finally:
            child.kill()
            child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
