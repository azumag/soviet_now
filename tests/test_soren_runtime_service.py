from pathlib import Path
import os
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONDITION = REPO_ROOT / "stream_backend_condition.sh"
PREREQS = REPO_ROOT / "wait_soren_runtime_prereqs.sh"
INSTALLER = REPO_ROOT / "install_soren_runtime_service.sh"
UNIT = REPO_ROOT / "deploy" / "soren-runtime" / "soren-runtime.service"
OBS_DROPIN = REPO_ROOT / "deploy" / "soren-runtime" / "obs-backend.conf"


class SorenRuntimeServiceTests(unittest.TestCase):
    @staticmethod
    def _run_condition(env_file: Path, expected: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SOREN_ENV_FILE"] = str(env_file)
        return subprocess.run(
            ["bash", str(CONDITION), expected],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_backend_condition_defaults_to_obs_and_selects_exact_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.env"
            self.assertEqual(self._run_condition(missing, "obs").returncode, 0)
            self.assertEqual(self._run_condition(missing, "ffmpeg").returncode, 1)

            env_file = Path(temp_dir) / ".env"
            env_file.write_text("SOREN_STREAM_BACKEND='ffmpeg'\n", encoding="utf-8")
            self.assertEqual(self._run_condition(env_file, "ffmpeg").returncode, 0)
            self.assertEqual(self._run_condition(env_file, "obs").returncode, 1)

    def test_backend_condition_rejects_invalid_value_without_disclosing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            secret_like = "invalid-sensitive-value"
            env_file.write_text(f"SOREN_STREAM_BACKEND='{secret_like}'\n", encoding="utf-8")
            result = self._run_condition(env_file, "obs")
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(secret_like, result.stdout + result.stderr)

    def test_prerequisite_helper_idempotently_creates_sink_and_sets_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            state = root / "state"
            state.mkdir()
            log = state / "pactl.log"
            pactl = fake_bin / "pactl"
            pactl.write_text(
                """#!/bin/bash
set -eu
printf '%s\n' "$*" >>"$FAKE_PACTL_LOG"
case "${1:-}" in
info) exit 0 ;;
list)
    if [ -f "$FAKE_PACTL_STATE/sink" ]; then
        printf '1\tsoren_null\tmodule-null-sink.c\ts16le 2ch 48000Hz\tIDLE\n'
    fi
    ;;
load-module) : >"$FAKE_PACTL_STATE/sink"; printf '42\n' ;;
set-default-sink) printf '%s\n' "$2" >"$FAKE_PACTL_STATE/default-sink" ;;
set-default-source) printf '%s\n' "$2" >"$FAKE_PACTL_STATE/default-source" ;;
get-default-sink) cat "$FAKE_PACTL_STATE/default-sink" ;;
get-default-source) cat "$FAKE_PACTL_STATE/default-source" ;;
*) exit 2 ;;
esac
""",
                encoding="utf-8",
            )
            pactl.chmod(0o755)
            xdpyinfo = fake_bin / "xdpyinfo"
            xdpyinfo.write_text(
                "#!/bin/bash\nprintf '  dimensions:    1280x720 pixels (338x190 millimeters)\\n'\n",
                encoding="utf-8",
            )
            xdpyinfo.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "FAKE_PACTL_STATE": str(state),
                    "FAKE_PACTL_LOG": str(log),
                    "SOREN_RUNTIME_PREREQ_WAIT_SEC": "5",
                }
            )
            result = subprocess.run(
                ["bash", str(PREREQS)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("runtime_prereqs=ok", result.stdout)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("load-module module-null-sink sink_name=soren_null", calls)
            self.assertIn("set-default-sink soren_null", calls)
            self.assertIn("set-default-source soren_null.monitor", calls)

    def test_system_unit_orders_runtime_before_obs_and_uses_foreground_supervisor(self) -> None:
        unit = UNIT.read_text(encoding="utf-8")
        dropin = OBS_DROPIN.read_text(encoding="utf-8")
        self.assertIn("Requires=xvfb.service", unit)
        self.assertIn("user@1001.service", unit)
        self.assertIn("ExecStartPre=/home/ubuntu/soren/wait_soren_runtime_prereqs.sh", unit)
        self.assertIn("ExecStart=/bin/bash /home/ubuntu/soren/start_all.sh --supervisor", unit)
        self.assertIn("Environment=SOREN_SOVIET_WATCHDOG_ENABLED=1", unit)
        self.assertIn("Environment=SOREN_STATUS_OVERLAY_WATCHERS_ENABLED=1", unit)
        self.assertIn("ExecStopPost=-/usr/bin/tmux kill-session -t soren_bridge", unit)
        self.assertIn("ExecStopPost=-/usr/bin/tmux kill-session -t soren_status_overlay", unit)
        self.assertIn("ExecStopPost=-/usr/bin/tmux kill-session -t soren_show_status_overlay", unit)
        self.assertIn("KillMode=control-group", unit)
        self.assertIn("After=soren-runtime.service", dropin)
        self.assertIn("ExecCondition=/home/ubuntu/soren/stream_backend_condition.sh obs", dropin)
        self.assertIn("ExecStart=\n", dropin)
        self.assertIn("--startstreaming", dropin)

    def test_supervisor_treats_systemd_term_as_a_clean_stop(self) -> None:
        source = (REPO_ROOT / "start_all.sh").read_text(encoding="utf-8")
        handler = source.split("_handle_supervisor_signal() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("SUPERVISOR_STOP_REQUESTED=1", handler)
        self.assertIn("_cleanup", handler)
        self.assertIn("exit 0", handler)
        self.assertNotIn("exit 130", handler)

    def test_installer_requires_explicit_migration_confirmation(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--install"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("confirm-runtime-migration", result.stderr)

    def test_installer_does_not_read_stream_destination_credentials(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("push.conf", source)
        self.assertNotIn("stream key", source.lower())
        self.assertIn("OBS_WAS_LIVE", source)
        self.assertNotIn("systemctl restart obs.service", source)

    def test_installer_refuses_to_clear_a_preexisting_stop_request(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        guard = 'if [ -e tmp/stop ]; then'
        self.assertIn(guard, source)
        self.assertIn("refusing to migrate a deliberately stopped runtime", source)
        self.assertLess(source.index(guard), source.index("sudo loginctl enable-linger"))

    def test_migration_wait_uses_pidfile_ownership_and_tolerates_zombie_reaping(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('owner=$(cat tmp/state/start_all.pid', source)
        self.assertIn('[ "$owner" = "$pid" ] || return 0', source)
        self.assertIn('case "$state" in Z*|*Z*) return 0', source)

    def test_installer_discovers_and_validates_the_local_cdp_endpoint(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("-iTCP:9222", source)
        self.assertIn("cdp_port_from_endpoint", source)
        self.assertIn('parsed.hostname not in {"localhost", "127.0.0.1", "::1"}', source)
        self.assertIn('parsed.scheme != "http"', source)
        self.assertIn("parsed.port != port", source)
        self.assertIn("pid = int(pid)", source)
        self.assertIn('lsof -nP -iTCP:"$cdp_port"', source)
        self.assertIn('lsof -nP -iTCP:8080 -sTCP:LISTEN -t 2>/dev/null | head -1 || true', source)
        self.assertIn('lsof -nP -iTCP:"$cdp_port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true', source)

    def test_soren_loop_does_not_spawn_tmux_overlay_duplicates_under_systemd(self) -> None:
        source = (REPO_ROOT / "soren_loop.sh").read_text(encoding="utf-8")
        self.assertIn('if [ "${SOREN_STATUS_OVERLAY_WATCHERS_ENABLED:-0}" != "1" ]; then', source)
        guarded = source.split(
            'if [ "${SOREN_STATUS_OVERLAY_WATCHERS_ENABLED:-0}" != "1" ]; then', 1
        )[1].split("\n\tfi", 1)[0]
        self.assertIn("--html-start", guarded)

    def test_linux_watchdog_uses_non_poisoning_gnu_stat_fallbacks(self) -> None:
        source = (REPO_ROOT / "soviet_watchdog.sh").read_text(encoding="utf-8")
        self.assertNotIn('stat -f %m "$RR_LOCK" 2>/dev/null || stat -c', source)
        self.assertNotIn('stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c', source)
        self.assertIn('|| lk=$(stat -c %Y "$RR_LOCK"', source)
        self.assertIn('|| mt=$(stat -c %Y "$LOCK_DIR"', source)


if __name__ == "__main__":
    unittest.main()
