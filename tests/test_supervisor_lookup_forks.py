"""Supervisor lookups keep liveness inputs without polling subprocesses."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "start_all.sh").read_text()


def function(name):
    match = re.search(r"(?m)^" + name + r"\(\) \{\n.*?^\}", SOURCE, re.S)
    if match is None:
        raise AssertionError(f"missing function {name}")
    return match.group()


LOOKUPS = function("_pidfile_for_worker") + "\n" + function("_pattern_for_worker")

# Frozen function-only fixture from cf4797f05; never source start_all.sh here.
# Compare the complete old mapping, rather than two interfaces of the new one.
LEGACY_LOOKUPS = r'''
_pidfile_for_worker() {
	case "$1" in
	soren_loop) echo "tmp/.soren_loop.lock/pid" ;;
	chat_worker) echo "tmp/state/chat_worker.pid" ;;
	youtube_worker) echo "tmp/state/youtube_worker.pid" ;;
	kick_worker) echo "tmp/state/kick_worker.pid" ;;
	audio_worker) echo "tmp/state/audio_worker.pid" ;;
	deadline_monitor) echo "tmp/state/deadline_monitor.pid" ;;
	radio_worker) echo "tmp/state/radio_worker.pid" ;;
	prediction_worker) echo "tmp/state/prediction_worker.pid" ;;
	poll_worker) echo "tmp/state/poll_worker.pid" ;;
	goal_worker) echo "tmp/state/goal_worker.pid" ;;
	improve_daemon) echo "${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}" ;;
	obs_capture_watchdog) echo "tmp/state/obs_capture_watchdog.pid" ;;
	soviet_watchdog) echo "tmp/state/.soviet_watchdog.lock/owner" ;;
	status_overlay_watch) echo "tmp/state/status_overlay_watch.pid" ;;
	show_status_overlay_watch) echo "tmp/state/show_status_overlay_watch.pid" ;;
	soren_overlay_watch) echo "tmp/state/soren_overlay_watch.pid" ;;
	direct_stream) echo "tmp/state/direct_stream.pid" ;;
	stream_noon_audit) echo "tmp/state/stream_noon_audit.pid" ;;
	youtube_broadcast_guard) echo "${YOUTUBE_BROADCAST_GUARD_PID_FILE:-tmp/state/youtube_broadcast_guard.pid}" ;;
	*) echo "" ;;
	esac
}
_pattern_for_worker() {
	case "$1" in
	soren_loop) echo '[/ ]soren_loop[.]sh([[:space:]]|$)' ;;
	chat_worker) echo '[/ ]workers/chat_worker[.]sh([[:space:]]|$)' ;;
	youtube_worker) echo '[/ ]workers/youtube_worker[.]sh([[:space:]]|$)' ;;
	kick_worker) echo '[/ ]workers/kick_worker[.]sh([[:space:]]|$)' ;;
	audio_worker) echo '[/ ]workers/audio_worker[.]sh([[:space:]]|$)' ;;
	deadline_monitor) echo '[/ ]workers/deadline_monitor[.]sh([[:space:]]|$)|[/ ]deadline_misplacement_monitor[.]py([[:space:]]|$)' ;;
	radio_worker) echo '[/ ]workers/radio_worker[.]sh([[:space:]]|$)' ;;
	prediction_worker) echo '[/ ]workers/prediction_worker[.]sh([[:space:]]|$)' ;;
	poll_worker) echo '[/ ]workers/poll_worker[.]sh([[:space:]]|$)' ;;
	goal_worker) echo '[/ ]workers/goal_worker[.]sh([[:space:]]|$)' ;;
	improve_daemon) echo '[/ ]improve_daemon[.]sh([[:space:]]|$)' ;;
	obs_capture_watchdog) echo '[/ ]obs_capture_watchdog[.]sh([[:space:]]|$)' ;;
	soviet_watchdog) echo '[/ ]soviet_watchdog[.]sh([[:space:]]|$)' ;;
	status_overlay_watch) echo '[/ ]generate_status_overlay[.]sh[[:space:]]+watch([[:space:]]|$)' ;;
	show_status_overlay_watch) echo '[/ ]generate_show_status_overlay[.]sh[[:space:]]+watch([[:space:]]|$)' ;;
	soren_overlay_watch) echo '[/ ]generate_soren_overlay[.]sh[[:space:]]+watch([[:space:]]|$)' ;;
	direct_stream) echo '[/ ]lib/direct_stream[.]py[[:space:]]+run([[:space:]]|$)' ;;
	stream_noon_audit) echo '[/ ]workers/stream_noon_audit[.]sh([[:space:]]|$)' ;;
	youtube_broadcast_guard) echo '[/ ]lib/youtube_broadcast_guard[.]py[[:space:]]+run([[:space:]]|$)' ;;
	*) echo "" ;;
	esac
}
'''


class SupervisorLookupForkTests(unittest.TestCase):
    def run_shell(self, body, extra_env=None, lookups=LOOKUPS):
        env = dict(os.environ)
        env.pop("IMPROVE_DAEMON_PID_FILE", None)
        env.pop("YOUTUBE_BROADCAST_GUARD_PID_FILE", None)
        env.update(extra_env or {})
        result = subprocess.run(
            ["bash", "-c", "set -euo pipefail\nshopt -u xpg_echo\n" + lookups + "\n" + body],
            env=env, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_caller_local_destinations_and_stdout_keep_worker_identity(self):
        result = self.run_shell(r'''
probe() {
    local name="$1" pattern=stale pid_file=stale
    _pattern_for_worker "$name" pattern
    _pidfile_for_worker "$name" pid_file
    [ "$pattern" = "$(_pattern_for_worker "$name")" ]
    [ "$pid_file" = "$(_pidfile_for_worker "$name")" ]
    printf '%s\n%s\n' "$pattern" "$pid_file"
}
probe soren_loop
probe soviet_watchdog
probe deadline_monitor
probe direct_stream
''')
        self.assertEqual(result.splitlines(), [
            "[/ ]soren_loop[.]sh([[:space:]]|$)", "tmp/.soren_loop.lock/pid",
            "[/ ]soviet_watchdog[.]sh([[:space:]]|$)", "tmp/state/.soviet_watchdog.lock/owner",
            "[/ ]workers/deadline_monitor[.]sh([[:space:]]|$)|[/ ]deadline_misplacement_monitor[.]py([[:space:]]|$)",
            "tmp/state/deadline_monitor.pid",
            "[/ ]lib/direct_stream[.]py[[:space:]]+run([[:space:]]|$)", "tmp/state/direct_stream.pid",
        ])

    def test_all_registered_workers_keep_both_lookup_interfaces(self):
        names = re.findall(r"^\t([a-z_]+)\) echo", LEGACY_LOOKUPS.split("_pattern_for_worker()")[0], re.M)
        self.assertEqual(len(names), 19)
        for helper in ("_pidfile_for_worker", "_pattern_for_worker"):
            self.assertEqual(re.findall(r"^\t([a-z_]+)\) _worker_lookup_value=", function(helper), re.M), names)
        for name in names + ["", "unknown_worker"]:
            for helper in ("_pidfile_for_worker", "_pattern_for_worker"):
                with self.subTest(worker=name, helper=helper):
                    env = {"FIXTURE_WORKER": name}
                    body = f'{helper} "$FIXTURE_WORKER"'
                    expected = self.run_shell(body, env, LEGACY_LOOKUPS)
                    self.assertEqual(self.run_shell(body, env), expected)
                    self.assertEqual(self.run_shell(
                        f'{helper} "$FIXTURE_WORKER" value; printf "%s\\n" "$value"', env), expected)
                    destination = "_w_pid_file" if helper == "_pidfile_for_worker" else "_w_pattern"
                    before = self.run_shell(self.scope_probe(
                        f'{destination}="$({helper} "$FIXTURE_WORKER")"'), env, LEGACY_LOOKUPS)
                    after = self.run_shell(self.scope_probe(
                        f'{helper} "$FIXTURE_WORKER" {destination}'), env)
                    self.assertEqual(after, before)

    def test_literal_pid_overrides_keep_legacy_bytes_and_caller_scope(self):
        # Option-only echo values and trailing newlines have distinct historical
        # semantics; do not silently include them in the normal-path contract.
        for worker, variable in (("improve_daemon", "IMPROVE_DAEMON_PID_FILE"),
                                 ("youtube_broadcast_guard", "YOUTUBE_BROADCAST_GUARD_PID_FILE")):
            for value in ("", "fixture with spaces/worker.pid", r"fixture\backslash\worker.pid",
                          "fixture%08s/worker.pid", "fixture$(printf injected)$HOME/worker.pid",
                          "-fixture.pid"):
                with self.subTest(worker=worker, override=value):
                    env = {"FIXTURE_WORKER": worker, variable: value}
                    raw = '_pidfile_for_worker "$FIXTURE_WORKER"'
                    self.assertEqual(self.run_shell(raw, env), self.run_shell(raw, env, LEGACY_LOOKUPS))
                    before = self.run_shell(self.scope_probe(
                        '_w_pid_file="$(_pidfile_for_worker "$FIXTURE_WORKER")"'), env, LEGACY_LOOKUPS)
                    after = self.run_shell(self.scope_probe(
                        '_pidfile_for_worker "$FIXTURE_WORKER" _w_pid_file'), env)
                    self.assertEqual(after, before)

    @staticmethod
    def scope_probe(statement):
        return r'''
_w_pid_file=outer
_w_pattern=outer-pattern
_worker_lookup_value=outer-internal
probe() {
    local _w_pid_file=inner _w_pattern=inner-pattern _worker_lookup_value=caller-internal
''' + statement + r'''
    printf '%s\0%s\0%s\0' "$_w_pid_file" "$_w_pattern" "$_worker_lookup_value"
}
probe
printf '%s\0%s\0%s\0' "$_w_pid_file" "$_w_pattern" "$_worker_lookup_value"
'''

    def test_unknown_worker_clears_previous_identity(self):
        self.run_shell(r'''
path_value=stale pattern_value=stale
_pidfile_for_worker unknown_worker path_value
_pattern_for_worker unknown_worker pattern_value
[ -z "$path_value" ] && [ -z "$pattern_value" ]
[ -z "$(_pidfile_for_worker unknown_worker)" ]
[ -z "$(_pattern_for_worker unknown_worker)" ]
''')

    def test_dynamic_pid_paths_are_not_cached(self):
        result = self.run_shell(r'''
IMPROVE_DAEMON_PID_FILE='fixture one/daemon.pid'
_pidfile_for_worker improve_daemon value
printf '%s\n' "$value"
IMPROVE_DAEMON_PID_FILE='fixture two/daemon.pid'
_pidfile_for_worker improve_daemon value
printf '%s\n' "$value"
YOUTUBE_BROADCAST_GUARD_PID_FILE='fixture/guard.pid'
_pidfile_for_worker youtube_broadcast_guard value
printf '%s\n' "$value"
''')
        self.assertEqual(result.splitlines(), [
            "fixture one/daemon.pid", "fixture two/daemon.pid", "fixture/guard.pid",
        ])

    def test_poll_lookup_path_runs_in_parent_shell(self):
        # Execute the actual hot lookup statements, with DEBUG inherited into
        # command substitutions. A stdout call is a positive control for the
        # detector; its subshell must be observed before checking the hot path.
        hot_pattern = next(line.strip() for line in SOURCE.splitlines()
                           if line.strip() == '_pattern_for_worker "$_w_name" _w_pattern')
        hot_path = next(line.strip() for line in SOURCE.splitlines()
                        if line.strip() == '_pidfile_for_worker "$_w_name" _w_pid_file')
        names = re.findall(r"^\t([a-z_]+)\) echo", LEGACY_LOOKUPS.split("_pattern_for_worker()")[0], re.M)
        with tempfile.TemporaryDirectory() as temp:
            trace = str(Path(temp) / "subshells")
            self.run_shell(r'''
parent_shell=$BASHPID
set -T
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "subshell\n" >> "$TRACE"; fi' DEBUG
control="$(_pattern_for_worker youtube_broadcast_guard)"
trap - DEBUG
[ -s "$TRACE" ]
: > "$TRACE"
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "subshell\n" >> "$TRACE"; fi' DEBUG
''' + "for _w_name in " + " ".join(names) + "; do\n" + hot_pattern + "\n" + hot_path + r'''
done
trap - DEBUG
[ ! -s "$TRACE" ]
[ "$_w_pattern" = "$control" ]
[ "$_w_pid_file" = 'tmp/state/youtube_broadcast_guard.pid' ]
''', {"TRACE": trace})


if __name__ == "__main__":
    unittest.main()
