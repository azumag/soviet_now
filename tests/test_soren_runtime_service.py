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
CHAT_WORKER = REPO_ROOT / "workers" / "chat_worker.sh"
# docich #2008: the shared PulseAudio permanence surface.
BGM_UNIT = REPO_ROOT / "deploy" / "soren-bgm" / "soren-bgm.service"
BGM_PULSE_GUARD = REPO_ROOT / "wait_soren_bgm_pulse.sh"
PULSE_CLIENT_CONF = REPO_ROOT / "deploy" / "pulse" / "client.conf"


def write_fake_pactl(
    fake_bin: Path,
    *,
    info_ready_marker: Path | None = None,
    sink_present: bool = True,
    log: Path | None = None,
) -> None:
    """Install a pactl stub for the prerequisite scripts.

    ``info_ready_marker`` keeps ``pactl info`` failing until a helper creates
    the marker, which models the canonical server not answering yet.
    """
    fake_bin.mkdir(parents=True, exist_ok=True)
    script = fake_bin / "pactl"
    marker_check = (
        f'if [ ! -f "{info_ready_marker}" ]; then exit 1; fi'
        if info_ready_marker is not None
        else "true"
    )
    sink_line = (
        "printf '1\\tsoren_null\\tmodule-null-sink.c\\ts16le 2ch 48000Hz\\tIDLE\\n'"
        if sink_present
        else "true"
    )
    log_line = f'printf \'%s\\n\' "$*" >>"{log}"' if log is not None else "true"
    script.write_text(
        f"""#!/bin/bash
set -eu
{log_line}
case "${{1:-}}" in
info) {marker_check} ;;
list) {sink_line} ;;
load-module) : ;;
set-default-sink) printf '%s\\n' "$2" >"$PWD/.fake-default-sink" ;;
set-default-source) printf '%s\\n' "$2" >"$PWD/.fake-default-source" ;;
get-default-sink) cat "$PWD/.fake-default-sink" ;;
get-default-source) cat "$PWD/.fake-default-source" ;;
*) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    script.chmod(0o755)


def write_fake_xdpyinfo(fake_bin: Path) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    script = fake_bin / "xdpyinfo"
    script.write_text(
        "#!/bin/bash\nprintf '  dimensions:    1280x720 pixels (338x190 millimeters)\\n'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)



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

    def test_chat_worker_reexecs_with_current_classifier_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "workers").mkdir()
            (root / "tmp" / "state").mkdir(parents=True)
            (root / "workers" / "chat_worker.sh").write_text(
                CHAT_WORKER.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (root / "lib").mkdir()
            (root / "lib" / "chat_clip_queue.sh").write_text(
                (REPO_ROOT / "lib" / "chat_clip_queue.sh").read_text(), encoding="utf-8"
            )
            (root / "eloop_lib.sh").write_text(
                'printf \'backend=%s key=%s\\n\' "${COMMENT_CLASSIFIER_BACKEND:-empty}" '
                '"${TYPESAFE_API_KEY:+present}" >>"$PWD/tmp/env_seen"\n',
                encoding="utf-8",
            )
            (root / "tmp" / "stop").touch()
            (root / "tmp" / "state" / "chat_worker.paused").touch()
            env = os.environ.copy()
            env.update(
                {
                    "COMMENT_CLASSIFIER_BACKEND": "jev",
                    "TYPESAFE_API_KEY": "stale-inherited-value",
                }
            )
            env.pop("CHAT_WORKER_ENV_REEXEC", None)

            (root / ".env").write_text(
                "COMMENT_CLASSIFIER_BACKEND=jev\n"
                "TYPESAFE_API_KEY=fresh-test-value\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", str(root / "workers" / "chat_worker.sh"), "test"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (root / "tmp" / "env_seen").read_text(encoding="utf-8").splitlines(),
                ["backend=jev key=present"],
            )

            (root / "tmp" / "env_seen").unlink()
            (root / ".env").write_text("COMMENT_CLASSIFIER_BACKEND=\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(root / "workers" / "chat_worker.sh"), "test"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (root / "tmp" / "env_seen").read_text(encoding="utf-8").splitlines(),
                ["backend=empty key="],
            )

    def test_chat_respawn_refreshes_managed_classifier_environment(self) -> None:
        source = (REPO_ROOT / "start_all.sh").read_text(encoding="utf-8")
        refresh = source.split("_refresh_chat_worker_env() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("COMMENT_CLASSIFIER_BACKEND", refresh)
        self.assertIn("TYPESAFE_API_KEY", refresh)
        self.assertIn('if [ "$name" = "chat_worker" ]; then', source)
        self.assertIn("_refresh_chat_worker_env", source)
        self.assertIn("exec $cmd", source)

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


    def test_soren_loop_reloads_committed_player_policy_per_game(self) -> None:
        source = (REPO_ROOT / "soren_loop.sh").read_text(encoding="utf-8")
        # The startup read stays as the process-wide fail-closed default.
        self.assertIn("game_lifecycle_load_player_policy", source)
        # A player_change is committed at a game boundary while this loop keeps
        # running, so the main loop must re-read the committed snapshot after
        # the per-match .env reload; otherwise the next game never adopts the
        # new policy (the JEV corner activation silently no-ops).
        main_loop = source.split("# --- メインループ: 1試合ずつ ---", 1)[1]
        env_reload = main_loop.index("# .env を毎試合再読込")
        reload_call = main_loop.index("game_lifecycle_load_player_policy")
        self.assertGreater(reload_call, env_reload)

    # ---- docich #2008: shared PulseAudio permanence -------------------------

    def test_bgm_unit_pins_the_shared_pulse_and_fails_closed(self) -> None:
        unit = BGM_UNIT.read_text(encoding="utf-8")
        self.assertIn("Environment=HOME=/home/ubuntu", unit)
        self.assertIn("Environment=XDG_RUNTIME_DIR=/run/user/1001", unit)
        self.assertIn(
            "Environment=PULSE_SERVER=unix:/run/user/1001/pulse/native", unit
        )
        self.assertIn(
            "ExecStartPre=/home/ubuntu/soren/wait_soren_bgm_pulse.sh", unit
        )
        self.assertIn("ExecStart=/home/ubuntu/soren/bgm_worker.sh", unit)
        # The unit must never own or start an audio server (the comments may
        # name the incident, but no directive may enable autospawn).
        self.assertNotIn("pulseaudio --start", unit)
        self.assertNotIn("autospawn = yes", unit)
        self.assertNotIn("autospawn=yes", unit)

    def test_bgm_pulse_guard_never_starts_a_server(self) -> None:
        source = BGM_PULSE_GUARD.read_text(encoding="utf-8")
        self.assertIn("refusing to start BGM", source)
        self.assertIn("this unit must not autospawn a private PulseAudio server", source)
        self.assertNotIn("pulseaudio --start", source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("kill", source)

    def test_bgm_pulse_guard_fails_closed_while_pulse_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            write_fake_pactl(
                fake_bin, info_ready_marker=root / "never-created"
            )
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "SOREN_BGM_PULSE_WAIT_SEC": "5",
            }
            result = subprocess.run(
                ["bash", str(BGM_PULSE_GUARD)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("did not become ready", result.stderr)
            self.assertIn("must not autospawn", result.stderr)

    def test_bgm_pulse_guard_reports_ok_and_loads_the_sink_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            log = root / "pactl.log"
            write_fake_pactl(fake_bin, sink_present=False, log=log)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "SOREN_BGM_PULSE_WAIT_SEC": "5",
            }
            result = subprocess.run(
                ["bash", str(BGM_PULSE_GUARD)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("soren_bgm_pulse=ok", result.stdout)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("load-module module-null-sink sink_name=soren_null", calls)
            # The guard is a client only: it must not touch server defaults.
            self.assertNotIn("set-default-sink", calls)
            self.assertNotIn("set-default-source", calls)

    def test_runtime_prereqs_start_the_user_pulse_units_when_socket_is_silent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            marker = root / "pulse-ready"
            systemctl_log = root / "systemctl.log"
            write_fake_pactl(fake_bin, info_ready_marker=marker)
            write_fake_xdpyinfo(fake_bin)
            fake_systemctl = fake_bin / "systemctl"
            fake_systemctl.write_text(
                "#!/bin/bash\n"
                f'printf \'%s\\n\' "$*" >>"{systemctl_log}"\n'
                f': >"{marker}"\n',
                encoding="utf-8",
            )
            fake_systemctl.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "SOREN_RUNTIME_PREREQ_WAIT_SEC": "15",
                "SOREN_RUNTIME_DISPLAY": ":99",
            }
            result = subprocess.run(
                ["bash", str(PREREQS)],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("runtime_prereqs=ok", result.stdout)
            calls = systemctl_log.read_text(encoding="utf-8")
            self.assertIn("--user start pulseaudio.socket pulseaudio.service", calls)

    def test_pulse_client_conf_disables_autospawn_and_pins_the_socket(self) -> None:
        template = PULSE_CLIENT_CONF.read_text(encoding="utf-8")
        self.assertIn("autospawn = no", template)
        self.assertIn(
            "default-server = unix:/run/user/1001/pulse/native", template
        )

    def test_installer_manages_pulse_permanence_assets(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            'PULSE_CLIENT_CONF_SOURCE="$SCRIPT_DIR/deploy/pulse/client.conf"', source
        )
        self.assertIn(
            'PULSE_CLIENT_CONF_TARGET="/home/$EXPECTED_USER/.config/pulse/client.conf"',
            source,
        )
        self.assertIn('"$PULSE_CLIENT_CONF_SOURCE" \\', source)
        self.assertIn("PULSE_USER_UNITS=(pulseaudio.socket pulseaudio.service)", source)
        self.assertIn('systemctl --user --machine="${EXPECTED_USER}@.host" enable "$unit"', source)
        self.assertIn("pulse_client_conf=%s", source)
        self.assertIn("pulse_user_units=%s", source)
        self.assertIn("pulse_socket=%s", source)
        # Permanence must be installed before the runtime migration mutates
        # anything, and must never restart a live audio server.
        self.assertLess(
            source.index("PULSE_CLIENT_CONF_TARGET"),
            source.index("MUTATION_STARTED=1"),
        )
        self.assertNotIn("pulseaudio --start", source)

    def _run_client_conf_writer(
        self,
        target: Path,
        server: str = "unix:/run/user/1001/pulse/native",
        confirm_audio: bool = True,
    ):
        """Run the shipped writer against ``target`` with only sudo stubbed.

        The helper bodies are extracted from the installer itself (anchored on
        their function definitions) so this exercises the production code, not
        a copy of it. ``sudo`` is stubbed to run the command directly because
        the test cannot escalate.
        """
        text = INSTALLER.read_text(encoding="utf-8")
        parts = []
        for marker in ("pulse_client_conf_setting() {", "install_pulse_client_conf() {"):
            start = text.index(marker)
            end = text.index("\n}\n", start) + len("\n}\n")
            parts.append(text[start:end])
        script = "\n".join(parts) + """
set -euo pipefail
sudo() {
  if [ "$1" = "-n" ]; then shift; fi
  "$@"
}
PULSE_CLIENT_CONF_TARGET="$TARGET"
PULSE_CLIENT_CONF_SERVER="$SERVER"
PULSE_AUDIO_CHANGE_CONFIRMED="${CONFIRM_AUDIO:-0}"
EXPECTED_USER="$(id -un)"
install_pulse_client_conf
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            stub = Path(temp_dir) / "run.sh"
            stub.write_text(script, encoding="utf-8")
            env = {
                **os.environ,
                "TARGET": str(target),
                "SERVER": server,
                "CONFIRM_AUDIO": "1" if confirm_audio else "0",
            }
            return subprocess.run(
                ["bash", str(stub)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

    def test_client_conf_writer_preserves_operator_lines_with_a_backup(self) -> None:
        # Review 2026-10-20: an existing client.conf must never be silently
        # overwritten. Production carried operator lines (2026-09-07 record).
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "client.conf"
            original = (
                "# operator note\n"
                "autospawn = yes\n"
                "default-server = unix:/tmp/stale/pulse\n"
                "disable-shm = yes\n"
            )
            target.write_text(original, encoding="utf-8")
            result = self._run_client_conf_writer(target)
            self.assertEqual(result.returncode, 0, result.stderr)

            written = target.read_text(encoding="utf-8")
            # Managed settings are corrected...
            self.assertIn("autospawn = no", written)
            self.assertIn(
                "default-server = unix:/run/user/1001/pulse/native", written
            )
            # ...every other operator line survives...
            self.assertIn("# operator note", written)
            self.assertIn("disable-shm = yes", written)
            # ...and the previous content is recoverable byte-for-byte.
            backups = sorted(target.parent.glob("client.conf.*.bak"))
            self.assertEqual(len(backups), 1, backups)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_client_conf_writer_is_idempotent_without_extra_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "client.conf"
            target.write_text(
                "disable-shm = yes\n"
                "autospawn = no\n"
                "default-server = unix:/run/user/1001/pulse/native\n",
                encoding="utf-8",
            )
            first = self._run_client_conf_writer(target)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("pulse_client_conf=unchanged", first.stdout)
            self.assertEqual(sorted(target.parent.glob("client.conf.*.bak")), [])

    def test_client_conf_writer_creates_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "pulse" / "client.conf"
            result = self._run_client_conf_writer(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = target.read_text(encoding="utf-8")
            self.assertIn("autospawn = no", written)
            self.assertIn(
                "default-server = unix:/run/user/1001/pulse/native", written
            )

    def test_client_conf_change_is_gated_behind_audio_confirmation(self) -> None:
        # Review 2026-10-20: client.conf is a broadcast-audio setting, so the
        # runtime migration must not change it implicitly. This mirrors the
        # dedicated installer's --confirm-audio-session gate.
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "client.conf"
            original = "autospawn = yes\ndisable-shm = yes\n"
            target.write_text(original, encoding="utf-8")

            result = self._run_client_conf_writer(target, confirm_audio=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pulse_client_conf=needs_confirmation", result.stdout)
            self.assertIn("--confirm-audio-session", result.stderr)
            # Nothing changed and no backup was taken.
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(sorted(target.parent.glob("client.conf.*.bak")), [])

    def test_already_managed_client_conf_needs_no_audio_confirmation(self) -> None:
        # Idempotent path: a host already converged by the dedicated installer
        # keeps working under the plain runtime migration.
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "client.conf"
            managed = (
                "disable-shm = yes\n"
                "autospawn = no\n"
                "default-server = unix:/run/user/1001/pulse/native\n"
            )
            target.write_text(managed, encoding="utf-8")
            result = self._run_client_conf_writer(target, confirm_audio=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pulse_client_conf=unchanged", result.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), managed)
            self.assertEqual(sorted(target.parent.glob("client.conf.*.bak")), [])

    def test_runtime_installer_requires_explicit_audio_confirmation_flag(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("PULSE_AUDIO_CHANGE_CONFIRMED=0", source)
        self.assertIn("PULSE_AUDIO_CHANGE_CONFIRMED=1", source)
        self.assertIn(
            "--install\\ --confirm-runtime-migration\\ --confirm-audio-session",
            source,
        )
        # The gate must be checked before any write or backup happens.
        gate = source.index("PULSE_AUDIO_CHANGE_CONFIRMED:-0")
        self.assertLess(gate, source.index(".bak"))
        self.assertLess(gate, source.index("pulse_client_conf=written"))

    def test_client_conf_writer_aborts_unchanged_when_backup_fails(self) -> None:
        # Review 5580497362: a failing backup must abort the change, and the
        # abort must be explicit rather than inherited from `set -e`. The
        # harness deliberately does NOT run under `set -e`, so a writer that
        # merely relies on it would fall through to the overwrite and fail
        # this test.
        text = INSTALLER.read_text(encoding="utf-8")
        parts = []
        for marker in (
            "pulse_client_conf_setting() {",
            "install_pulse_client_conf() {",
        ):
            start = text.index(marker)
            end = text.index("\n}\n", start) + len("\n}\n")
            parts.append(text[start:end])
        script = "\n".join(parts) + """
set -uo pipefail
sudo() {
  if [ "$1" = "-n" ]; then shift; fi
  if [ "$1" = "cp" ]; then
    echo "cp: simulated failure" >&2
    return 1
  fi
  "$@"
}
PULSE_CLIENT_CONF_TARGET="$TARGET"
PULSE_CLIENT_CONF_SERVER="$SERVER"
PULSE_AUDIO_CHANGE_CONFIRMED=1
EXPECTED_USER="$(id -un)"
install_pulse_client_conf
echo "writer_reached_the_end"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "client.conf"
            original = "autospawn = yes\ndisable-shm = yes\n"
            target.write_text(original, encoding="utf-8")
            stub = Path(temp_dir) / "run.sh"
            stub.write_text(script, encoding="utf-8")
            env = {
                **os.environ,
                "TARGET": str(target),
                "SERVER": "unix:/run/user/1001/pulse/native",
            }
            result = subprocess.run(
                ["bash", str(stub)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot back up", result.stderr)
            # The writer must have stopped before writing anything.
            self.assertNotIn("writer_reached_the_end", result.stdout)
            self.assertNotIn("pulse_client_conf=written", result.stdout)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(sorted(target.parent.glob("client.conf.*.bak")), [])

    def test_client_conf_state_uses_effective_settings(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("pulse_client_conf_setting() {", source)
        # Last assignment wins, so an operator line before the managed pair
        # must not make the state look unmanaged.
        self.assertIn(
            'sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p"', source
        )
        self.assertIn("tail -n 1", source)

    def test_installer_refuses_to_drift_from_the_reviewed_template(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("does not disable autospawn", source)
        self.assertIn("does not pin $PULSE_CLIENT_CONF_SERVER", source)

    def test_installer_print_config_publishes_pulse_target(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--print-config"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"pulse_client_conf": "/home/ubuntu/.config/pulse/client.conf"', result.stdout)
        self.assertIn('"pulse_user_units": ["pulseaudio.socket", "pulseaudio.service"]', result.stdout)


if __name__ == "__main__":
    unittest.main()
