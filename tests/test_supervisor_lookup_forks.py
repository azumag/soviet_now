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


class SupervisorLookupForkTests(unittest.TestCase):
    def run_shell(self, body, extra_env=None):
        env = dict(os.environ)
        env.update(extra_env or {})
        result = subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + LOOKUPS + "\n" + body],
            env=env, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
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
        names = re.findall(r"^\t([a-z_]+)\) _worker_lookup_value=", function("_pidfile_for_worker"), re.M)
        self.assertEqual(len(names), 19)
        self.run_shell("\n".join(
            f'_pidfile_for_worker {name} path_value; _pattern_for_worker {name} pattern_value; '
            f'[ "$path_value" = "$(_pidfile_for_worker {name})" ]; '
            f'[ "$pattern_value" = "$(_pattern_for_worker {name})" ]'
            for name in names
        ))

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
        with tempfile.TemporaryDirectory() as temp:
            trace = str(Path(temp) / "subshells")
            self.run_shell(r'''
parent_shell=$BASHPID
set -T
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "subshell\n" >> "$TRACE"; fi' DEBUG
control="$(_pattern_for_worker soren_loop)"
trap - DEBUG
[ -s "$TRACE" ]
: > "$TRACE"
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "subshell\n" >> "$TRACE"; fi' DEBUG
_w_name=soren_loop
''' + hot_pattern + "\n" + hot_path + r'''
trap - DEBUG
[ ! -s "$TRACE" ]
[ "$_w_pattern" = "$control" ]
[ "$_w_pid_file" = 'tmp/.soren_loop.lock/pid' ]
''', {"TRACE": trace})


if __name__ == "__main__":
    unittest.main()
