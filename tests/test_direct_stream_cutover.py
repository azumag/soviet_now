from pathlib import Path
import os
import signal
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "cutover_direct_stream.sh"


class DirectStreamCutoverTests(unittest.TestCase):
    @staticmethod
    def _write_executable(path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def _fake_cutover(
        self,
        root: Path,
        *,
        bad_quality: bool,
        system_runtime: bool = False,
        relay_reload_fails: bool = False,
        push_mode: str = "640",
        push_symlink: bool = False,
        startup_transient: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], int, dict[str, str], Path]:
        fake_bin = root / "bin"
        state = root / "fake-state"
        repo = root / "repo"
        for directory in (fake_bin, state, repo / "lib", repo / "tmp" / "state"):
            directory.mkdir(parents=True, exist_ok=True)

        script_source = SCRIPT.read_text(encoding="utf-8")
        relay_config = root / "nginx.conf"
        push_config = root / "push.conf"
        script_source = script_source.replace(
            'RELAY_CONFIG="/etc/soren-rtmp/nginx.conf"',
            f'RELAY_CONFIG="{relay_config}"',
        ).replace(
            'PUSH_CONFIG="/etc/soren-rtmp/push.conf"',
            f'PUSH_CONFIG="{push_config}"',
        )
        cutover = repo / SCRIPT.name
        self._write_executable(cutover, script_source)
        relay_config.write_text("events {}\n", encoding="utf-8")
        if push_symlink:
            push_target = root / "push-target.conf"
            push_target.write_text("push rtmp://example.invalid/app/placeholder;\n", encoding="utf-8")
            push_config.symlink_to(push_target)
        else:
            push_config.write_text("push rtmp://example.invalid/app/placeholder;\n", encoding="utf-8")
        env_file = repo / ".env"
        env_file.write_text("SOREN_STREAM_BACKEND='obs'\n", encoding="utf-8")

        self._write_executable(
            fake_bin / "uname",
            "#!/bin/bash\nprintf '%s\\n' Linux\n",
        )
        self._write_executable(
            fake_bin / "sleep",
            "#!/bin/bash\nexit 0\n",
        )
        self._write_executable(
            fake_bin / "sudo",
            "#!/bin/bash\n[ \"${1:-}\" = -n ] && shift\nexec \"$@\"\n",
        )
        self._write_executable(
            fake_bin / "stat",
            """#!/bin/bash
set -u
format=""
while [ "$#" -gt 0 ]; do
    case "$1" in
    -c) format="$2"; shift 2 ;;
    *) shift ;;
    esac
done
case "$format" in
%U) printf '%s\\n' root ;;
%G) printf '%s\\n' soren-relay ;;
%a) printf '%s\\n' "${FAKE_PUSH_MODE:-640}" ;;
*) exit 2 ;;
esac
""",
        )
        self._write_executable(fake_bin / "nginx", "#!/bin/bash\nexit 0\n")
        self._write_executable(
            fake_bin / "systemctl",
            """#!/bin/bash
set -u
state="$FAKE_STATE_DIR"
if [ "${1:-}" = "--user" ]; then
    exit 3
fi
action="${1:-}"
unit="${@: -1}"
case "$action:$unit" in
is-active:obs.service) [ "$(cat "$state/obs.service")" = active ] ;;
is-active:soren-rtmp-relay.service) [ "$(cat "$state/relay.service")" = active ] ;;
is-active:soren-runtime.service) [ -f "$state/runtime.active" ] ;;
reload:soren-rtmp-relay.service)
    printf '%s\n' reload >>"$state/relay.actions"
    [ "${FAKE_RELAY_RELOAD_FAILS:-0}" != 1 ]
    ;;
stop:obs.service) printf '%s\n' inactive >"$state/obs.service" ;;
start:obs.service) printf '%s\n' active >"$state/obs.service" ;;
stop:soren-runtime.service)
    printf '%s\n' stop >>"$state/runtime.actions"
    "$FAKE_REPO/stop_soren.sh"
    rm -f "$state/runtime.active"
    ;;
start:soren-runtime.service)
    printf '%s\n' start >>"$state/runtime.actions"
    "$FAKE_REPO/start_all.sh"
    : >"$state/runtime.active"
    ;;
*) exit 0 ;;
esac
""",
        )
        self._write_executable(
            repo / "obs_control.sh",
            """#!/bin/bash
set -u
case "${1:-}" in
stream-status)
    if [ "$(cat "$FAKE_STATE_DIR/obs.stream")" = on ]; then
        printf '%s\n' streaming=on
    else
        printf '%s\n' streaming=off
    fi
    ;;
stream-stop) printf '%s\n' off >"$FAKE_STATE_DIR/obs.stream" ;;
stream-start) printf '%s\n' on >"$FAKE_STATE_DIR/obs.stream" ;;
*) exit 2 ;;
esac
""",
        )
        self._write_executable(
            repo / "stop_soren.sh",
            """#!/bin/bash
pid=$(cat tmp/state/start_all.pid 2>/dev/null || true)
case "$pid" in ''|*[!0-9]*) exit 0 ;; esac
kill -TERM "$pid" 2>/dev/null || true
rm -f tmp/state/start_all.pid "$FAKE_STATE_DIR/direct.active"
""",
        )
        self._write_executable(
            repo / "start_all.sh",
            """#!/bin/bash
set -a
. "$SOREN_ENV_FILE"
set +a
if [ "${SOREN_STREAM_BACKEND:-obs}" = ffmpeg ]; then
    : >"$FAKE_STATE_DIR/direct.active"
else
    rm -f "$FAKE_STATE_DIR/direct.active"
fi
nohup /bin/sleep 300 >/dev/null 2>&1 &
printf '%s\n' "$!" >tmp/state/start_all.pid
""",
        )
        self._write_executable(
            repo / "direct_stream.sh",
            """#!/bin/bash
case "${1:-}" in
status)
    if [ ! -f "$FAKE_STATE_DIR/direct.active" ]; then
        printf '%s\n' '{"running":false,"state":"stopped"}'
        exit 0
    fi
    count=$(cat "$FAKE_STATE_DIR/direct.count" 2>/dev/null || printf 0)
    count=$((count + 1))
    printf '%s\n' "$count" >"$FAKE_STATE_DIR/direct.count"
    if [ ! -f "$FAKE_STATE_DIR/direct.started" ]; then
        date +%s >"$FAKE_STATE_DIR/direct.started"
    fi
    started_at=$(cat "$FAKE_STATE_DIR/direct.started")
    frame=$((count * 300))
    fps=30.0
    drop=0
    dup=0
    if [ "${FAKE_STARTUP_TRANSIENT:-0}" = 1 ] && [ "$count" -ge 2 ]; then
        drop=4
        dup=6
    fi
    if [ "${FAKE_DIRECT_BAD:-0}" = 1 ] && [ "$count" -ge 2 ]; then
        frame=350
        fps=10.0
    fi
    printf '{"running":true,"state":"running","started_at":%s,"frame":%s,"fps":%s,"speed":1.0,"drop_frames":%s,"dup_frames":%s}\n' "$started_at" "$frame" "$fps" "$drop" "$dup"
    ;;
stop) rm -f "$FAKE_STATE_DIR/direct.active" "$FAKE_STATE_DIR/direct.started" ;;
*) exit 2 ;;
esac
""",
        )
        (repo / "lib" / "direct_stream.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )

        (state / "obs.service").write_text("active\n", encoding="utf-8")
        (state / "relay.service").write_text("active\n", encoding="utf-8")
        (state / "obs.stream").write_text("on\n", encoding="utf-8")
        if system_runtime:
            (state / "runtime.active").touch()
        daemon = subprocess.run(
            ["/bin/bash", "-c", "nohup /bin/sleep 300 >/dev/null 2>&1 & printf '%s\\n' $!"],
            text=True,
            capture_output=True,
            check=True,
        )
        initial_pid = int(daemon.stdout.strip())
        (repo / "tmp" / "state" / "start_all.pid").write_text(
            f"{initial_pid}\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "FAKE_STATE_DIR": str(state),
                "FAKE_DIRECT_BAD": "1" if bad_quality else "0",
                "FAKE_STARTUP_TRANSIENT": "1" if startup_transient else "0",
                "FAKE_REPO": str(repo),
                "FAKE_RELAY_RELOAD_FAILS": "1" if relay_reload_fails else "0",
                "FAKE_PUSH_MODE": push_mode,
                "SOREN_ENV_FILE": str(env_file),
                "SOREN_DIRECT_CUTOVER_VERIFY_SEC": "10",
                "SOREN_DIRECT_CUTOVER_WARMUP_SEC": "15",
            }
        )
        result = subprocess.run(
            ["bash", str(cutover), "--cutover", "--confirm-live-cutover"],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        return result, initial_pid, env, cutover

    def test_print_plan_is_non_mutating_and_does_not_disclose_destinations(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--print-plan"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("automatic rollback", SCRIPT.read_text(encoding="utf-8"))
        self.assertIn("restore .env", result.stdout)
        self.assertNotIn("rtmp://", result.stdout)

    def test_live_cutover_requires_exact_confirmation_before_platform_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "must-not-exist"
            env = os.environ.copy()
            env["SOREN_ENV_FILE"] = str(marker)
            result = subprocess.run(
                ["bash", str(SCRIPT), "--cutover"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires", result.stderr)
        self.assertFalse(marker.exists())

    def test_live_rollback_requires_exact_confirmation_before_platform_checks(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--rollback"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires", result.stderr)

    def test_secret_file_is_only_counted_and_never_printed(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('PUSH_CONFIG="/etc/soren-rtmp/push.conf"', source)
        self.assertNotIn("SOREN_DIRECT_RELAY_PUSH_CONFIG", source)
        self.assertIn("awk '/^[[:space:]]*push", source)
        self.assertNotIn('cat "$PUSH_CONFIG"', source)
        self.assertNotIn('grep "$PUSH_CONFIG"', source)

    def test_simulated_cutover_switches_backend_and_stops_obs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, initial_pid, _env, _cutover = self._fake_cutover(root, bad_quality=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("SOREN_STREAM_BACKEND='ffmpeg'", (root / "repo" / ".env").read_text())
            self.assertEqual((root / "fake-state" / "obs.service").read_text().strip(), "inactive")
            self.assertEqual((root / "fake-state" / "obs.stream").read_text().strip(), "off")
            self.assertIn('"state": "ffmpeg_live"', result.stdout)
            self.assertNotIn("placeholder", result.stdout + result.stderr)
            self.assertFalse(self._pid_exists(initial_pid))
            self.assertEqual(
                (root / "fake-state" / "relay.actions").read_text().splitlines(),
                ["reload"],
            )
            self._stop_fake_supervisor(root)

    def test_startup_drop_and_dup_are_excluded_but_steady_window_stays_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, _initial_pid, _env, _cutover = self._fake_cutover(
                root,
                bad_quality=False,
                startup_transient=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"drop_delta": 0', result.stdout)
            self.assertIn('"dup_delta": 0', result.stdout)
            self.assertIn('"warmup_seconds": 15', result.stdout)
            self._stop_fake_supervisor(root)

    def test_relay_reload_failure_leaves_obs_and_environment_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, initial_pid, _env, _cutover = self._fake_cutover(
                root,
                bad_quality=False,
                relay_reload_fails=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed to reload", result.stderr)
            self.assertIn("SOREN_STREAM_BACKEND='obs'", (root / "repo" / ".env").read_text())
            self.assertEqual((root / "fake-state" / "obs.service").read_text().strip(), "active")
            self.assertEqual((root / "fake-state" / "obs.stream").read_text().strip(), "on")
            self.assertTrue(self._pid_exists(initial_pid))
            self._stop_fake_supervisor(root)

    def test_insecure_push_permissions_leave_obs_and_environment_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, initial_pid, _env, _cutover = self._fake_cutover(
                root,
                bad_quality=False,
                push_mode="644",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("root:soren-relay mode 0640", result.stderr)
            self.assertIn("SOREN_STREAM_BACKEND='obs'", (root / "repo" / ".env").read_text())
            self.assertEqual((root / "fake-state" / "obs.service").read_text().strip(), "active")
            self.assertEqual((root / "fake-state" / "obs.stream").read_text().strip(), "on")
            self.assertTrue(self._pid_exists(initial_pid))
            self.assertFalse((root / "fake-state" / "relay.actions").exists())
            self._stop_fake_supervisor(root)

    def test_symlinked_push_config_leaves_obs_and_environment_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, initial_pid, _env, _cutover = self._fake_cutover(
                root,
                bad_quality=False,
                push_symlink=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symbolic link", result.stderr)
            self.assertIn("SOREN_STREAM_BACKEND='obs'", (root / "repo" / ".env").read_text())
            self.assertEqual((root / "fake-state" / "obs.service").read_text().strip(), "active")
            self.assertEqual((root / "fake-state" / "obs.stream").read_text().strip(), "on")
            self.assertTrue(self._pid_exists(initial_pid))
            self.assertFalse((root / "fake-state" / "relay.actions").exists())
            self._stop_fake_supervisor(root)

    def test_simulated_quality_failure_restores_obs_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, initial_pid, _env, _cutover = self._fake_cutover(root, bad_quality=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SOREN_STREAM_BACKEND='obs'", (root / "repo" / ".env").read_text())
            self.assertEqual((root / "fake-state" / "obs.service").read_text().strip(), "active")
            self.assertEqual((root / "fake-state" / "obs.stream").read_text().strip(), "on")
            self.assertIn("restoring OBS path", result.stderr)
            self.assertFalse(self._pid_exists(initial_pid))
            self._stop_fake_supervisor(root)

    def test_simulated_cutover_controls_boot_persistent_system_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result, initial_pid, _env, _cutover = self._fake_cutover(
                root,
                bad_quality=False,
                system_runtime=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self._pid_exists(initial_pid))
            actions = (root / "fake-state" / "runtime.actions").read_text().splitlines()
            self.assertEqual(actions, ["stop", "start"])
            self.assertTrue((root / "fake-state" / "runtime.active").exists())
            self._stop_fake_supervisor(root)

    def test_simulated_successful_cutover_can_rollback_to_obs_within_60_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cutover_result, initial_pid, env, cutover = self._fake_cutover(root, bad_quality=False)
            self.assertEqual(cutover_result.returncode, 0, cutover_result.stderr)
            rollback_result = subprocess.run(
                ["bash", str(cutover), "--rollback", "--confirm-live-rollback"],
                cwd=root / "repo",
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(rollback_result.returncode, 0, rollback_result.stderr)
            self.assertIn("SOREN_STREAM_BACKEND='obs'", (root / "repo" / ".env").read_text())
            self.assertEqual((root / "fake-state" / "obs.service").read_text().strip(), "active")
            self.assertEqual((root / "fake-state" / "obs.stream").read_text().strip(), "on")
            self.assertFalse((root / "fake-state" / "direct.active").exists())
            state = (root / "repo" / "tmp" / "state" / "direct_cutover.json").read_text()
            self.assertIn('"state": "obs_rolled_back"', state)
            self.assertIn('"rollback_within_60_sec": true', state)
            self.assertNotIn("placeholder", rollback_result.stdout + rollback_result.stderr)
            self.assertFalse(self._pid_exists(initial_pid))
            self._stop_fake_supervisor(root)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    @staticmethod
    def _stop_fake_supervisor(root: Path) -> None:
        pid_file = root / "repo" / "tmp" / "state" / "start_all.pid"
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pass


if __name__ == "__main__":
    unittest.main()
