"""Supervisor lookups keep liveness inputs without polling subprocesses."""
import os
from itertools import product
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

PID_OVERRIDE_VALUES = (
    "", "fixture with spaces/worker.pid", r"fixture\backslash\worker.pid",
    "fixture%08s/worker.pid", "fixture$(printf injected)$HOME/worker.pid",
    "-fixture.pid", "-", "--", "-nX",
    "fixture.pid\n", "fixture.pid\n\n", "fixture\nworker.pid", "\n\n",
    "-n\n", "-ne\n\n",
) + tuple("-" + "".join(flags) for length in range(1, 4)
          for flags in product("neE", repeat=length))


class SupervisorLookupForkTests(unittest.TestCase):
    def run_shell(self, body, extra_env=None, lookups=LOOKUPS,
                  shell_options="set +o posix\nshopt -u xpg_echo"):
        env = dict(os.environ)
        env.pop("IMPROVE_DAEMON_PID_FILE", None)
        env.pop("YOUTUBE_BROADCAST_GUARD_PID_FILE", None)
        env.update(extra_env or {})
        result = subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + shell_options + "\n" + lookups + "\n" + body],
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

    def test_pid_overrides_keep_legacy_bytes_status_and_caller_scope(self):
        for worker, variable in (("improve_daemon", "IMPROVE_DAEMON_PID_FILE"),
                                 ("youtube_broadcast_guard", "YOUTUBE_BROADCAST_GUARD_PID_FILE")):
            for value in PID_OVERRIDE_VALUES:
                with self.subTest(worker=worker, override=value):
                    env = {"FIXTURE_WORKER": worker, variable: value}
                    raw = '_pidfile_for_worker "$FIXTURE_WORKER"'
                    self.assertEqual(self.run_shell(raw, env), self.run_shell(raw, env, LEGACY_LOOKUPS))
                    before = self.run_shell(self.scope_probe(
                        '_w_pid_file="$(_pidfile_for_worker "$FIXTURE_WORKER")"'), env, LEGACY_LOOKUPS)
                    after = self.run_shell(self.scope_probe(
                        '_pidfile_for_worker "$FIXTURE_WORKER" _w_pid_file'), env)
                    self.assertEqual(after, before)
                    cold = self.run_shell(self.scope_probe(
                        '_w_pid_file="$(_pidfile_for_worker "$FIXTURE_WORKER")"'), env)
                    self.assertEqual(cold, before)

    def test_alternate_echo_modes_keep_existing_semantics(self):
        # Do not change the caller's options. For XPG escapes use echo itself,
        # rather than attempting another general-purpose escape interpreter.
        for options in ("set -o posix\nshopt -u xpg_echo",
                        "set +o posix\nshopt -s xpg_echo",
                        "set -o posix\nshopt -s xpg_echo"):
            for value in PID_OVERRIDE_VALUES + (r"fixture\cignored", r"fixture\0123.pid"):
                with self.subTest(options=options, override=value):
                    env = {"YOUTUBE_BROADCAST_GUARD_PID_FILE": value}
                    raw = '_pidfile_for_worker youtube_broadcast_guard'
                    self.assertEqual(self.run_shell(raw, env, shell_options=options),
                                     self.run_shell(raw, env, LEGACY_LOOKUPS, options))
                    before = self.run_shell(self.scope_probe(
                        '_w_pid_file="$(_pidfile_for_worker youtube_broadcast_guard)"'),
                        env, LEGACY_LOOKUPS, options)
                    after = self.run_shell(self.scope_probe(
                        '_pidfile_for_worker youtube_broadcast_guard _w_pid_file'),
                        env, shell_options=options)
                    self.assertEqual(after, before)
                    cold = self.run_shell(self.scope_probe(
                        '_w_pid_file="$(_pidfile_for_worker youtube_broadcast_guard)"'),
                        env, shell_options=options)
                    self.assertEqual(cold, before)

    @staticmethod
    def scope_probe(statement):
        return r'''
_w_pid_file=outer
_w_pattern=outer-pattern
_worker_lookup_value=outer-internal
probe() {
    local _w_pid_file=inner _w_pattern=inner-pattern _worker_lookup_value=caller-internal
    local fixture_xpg=0 fixture_posix=0
    if shopt -q xpg_echo; then fixture_xpg=1; fi
    if [[ -o posix ]]; then fixture_posix=1; fi
''' + statement + r'''
    if shopt -q xpg_echo; then [ "$fixture_xpg" = 1 ]; else [ "$fixture_xpg" = 0 ]; fi
    if [[ -o posix ]]; then [ "$fixture_posix" = 1 ]; else [ "$fixture_posix" = 0 ]; fi
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
        # The old 19-worker path is a positive control: 2 subshells per worker.
        # Execute the actual new hot statements with every override boundary.
        for value in PID_OVERRIDE_VALUES:
            with self.subTest(override=value):
                self.assert_scan_subshell_counts({
                    "IMPROVE_DAEMON_PID_FILE": value,
                    "YOUTUBE_BROADCAST_GUARD_PID_FILE": value,
                }, expected_after=0)

    def test_xpg_escape_fallback_is_limited_to_affected_override(self):
        for posix in (False, True):
            for xpg in (False, True):
                options = ("set " + ("-" if posix else "+") + "o posix\nshopt " +
                           ("-s" if xpg else "-u") + " xpg_echo")
                for value in (r"fixture\cignored", "-n\n"):
                    with self.subTest(posix=posix, xpg=xpg, override=value):
                        self.assert_scan_subshell_counts(
                            {"YOUTUBE_BROADCAST_GUARD_PID_FILE": value},
                            expected_after=int(xpg and "\\" in value), shell_options=options)

    def assert_scan_subshell_counts(self, extra_env, expected_after, shell_options=None):
        hot_pattern = next(line.strip() for line in SOURCE.splitlines()
                           if line.strip() == '_pattern_for_worker "$_w_name" _w_pattern')
        hot_path = next(line.strip() for line in SOURCE.splitlines()
                        if line.strip() == '_pidfile_for_worker "$_w_name" _w_pid_file')
        names = re.findall(r"^\t([a-z_]+)\) echo", LEGACY_LOOKUPS.split("_pattern_for_worker()")[0], re.M)
        with tempfile.TemporaryDirectory() as temp:
            trace = Path(temp) / "subshells"
            trace.touch()
            env = dict(extra_env, TRACE=str(trace))
            prefix = r'''
parent_shell=$BASHPID
set -T
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "%s\n" "$BASHPID" >> "$TRACE"; fi' DEBUG
''' + "for _w_name in " + " ".join(names) + "; do\n"
            suffix = "\ndone\ntrap - DEBUG\n"
            options = {} if shell_options is None else {"shell_options": shell_options}
            self.assertEqual(self.run_shell(prefix + r'''
_w_pattern="$(_pattern_for_worker "$_w_name")"
_w_pid_file="$(_pidfile_for_worker "$_w_name")"
''' + suffix, env, LEGACY_LOOKUPS, **options), "")
            self.assertEqual(len(set(trace.read_text().splitlines())), 38)
            trace.write_text("")
            self.assertEqual(self.run_shell(prefix + hot_pattern + "\n" + hot_path + suffix,
                                           env, **options), "")
            self.assertEqual(len(set(trace.read_text().splitlines())), expected_after)


if __name__ == "__main__":
    unittest.main()
