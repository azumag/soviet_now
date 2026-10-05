"""Keep supervisor liveness semantics while avoiding successful-probe forks.

Extract functions only: do not source the supervisor or start real workers.
All real process probes use signal zero; failure diagnostics use shell fakes.
"""
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


# Frozen function-only positive control from main 6e17e0842c4d0924.
LEGACY_LIVENESS = r'''
_pid_alive() {
    local pid="${1:-}"
    local err=""
    case "$pid" in
    ''|*[!0-9]*) return 1 ;;
    esac
    err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
    case "$err" in
    *"operation not permitted"*|*"Operation not permitted"*) return 0 ;;
    esac
    return 1
}
'''
LIVENESS = function("_pid_alive")


class SupervisorLivenessForkTests(unittest.TestCase):
    def run_shell(self, body, functions=LIVENESS, extra_env=None, cwd=None):
        env = dict(os.environ)
        env.update(extra_env or {})
        result = subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + functions + "\n" + body],
            env=env, cwd=cwd, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_stable_success_and_failure_diagnostics_keep_legacy_verdict(self):
        for result_code, message in (
            (0, ""), (1, "operation not permitted"),
            (1, "Operation not permitted"), (1, "No such process"),
            (1, "Permission denied"), (1, "unknown failure"), (1, ""),
        ):
            with self.subTest(code=result_code, diagnostic=message):
                env = {"FIXTURE_RC": str(result_code), "FIXTURE_ERROR": message}
                body = r'''
kill() { printf '%s' "$FIXTURE_ERROR" >&2; return "$FIXTURE_RC"; }
if _pid_alive 123; then printf 'alive'; else printf 'gone'; fi
'''
                self.assertEqual(self.run_shell(body, extra_env=env),
                                 self.run_shell(body, LEGACY_LIVENESS, env))

    def test_invalid_pid_never_calls_kill(self):
        for pid in ("", "-1", "1 2", "123\n", "not-a-pid", "1;exit", "1.5"):
            with self.subTest(pid=pid):
                body = r'''
kill() { printf 'unexpected probe'; return 0; }
if _pid_alive "$FIXTURE_PID"; then exit 9; fi
'''
                self.assertEqual(self.run_shell(body, extra_env={"FIXTURE_PID": pid}), "")
                self.assertEqual(self.run_shell(body, LEGACY_LIVENESS,
                                               {"FIXTURE_PID": pid}), "")

    def test_live_probe_runs_once_in_parent_and_never_sends_a_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / "calls"
            body = r'''
parent_shell=$BASHPID
kill() { printf '%s:%s:%s\n' "$BASHPID" "$1" "$2" >> "$CALLS"; return 0; }
_pid_alive 123
printf '%s' "$parent_shell"
'''
            parent = self.run_shell(body, extra_env={"CALLS": str(calls)})
            self.assertEqual(calls.read_text().splitlines(), [f"{parent}:-0:123"])

    def test_failure_reprobes_only_with_signal_zero_and_keeps_quiet(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / "calls"
            body = r'''
kill() {
    printf '%s:%s\n' "$1" "$2" >> "$CALLS"
    printf 'Operation not permitted' >&2
    return 1
}
_pid_alive 123
'''
            self.assertEqual(self.run_shell(body, extra_env={"CALLS": str(calls)}), "")
            self.assertEqual(calls.read_text().splitlines(), ["-0:123", "-0:123"])

    def test_successful_retry_after_failed_fast_probe_is_alive(self):
        # A process can become probeable between checks. Pattern ownership
        # remains the caller's separate check, not a liveness guarantee.
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / "first"
            body = r'''
kill() {
    if [ ! -f "$CALLS" ]; then
        printf first > "$CALLS"
        printf 'No such process' >&2
        return 1
    fi
    return 0
}
_pid_alive 123
'''
            self.assertEqual(self.run_shell(body, extra_env={"CALLS": str(calls)}), "")

    def test_owned_real_live_and_reaped_process_keep_legacy_verdict(self):
        body = r'''
_pid_alive "$BASHPID"
sleep 0 & child=$!
wait "$child"
if _pid_alive "$child"; then exit 9; fi
'''
        self.assertEqual(self.run_shell(body), "")
        self.assertEqual(self.run_shell(body, LEGACY_LIVENESS), "")

    def test_nineteen_successful_probes_remove_nineteen_subshells(self):
        with tempfile.TemporaryDirectory() as temp:
            trace = Path(temp) / "trace"
            body = r'''
parent_shell=$BASHPID
set -T
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "%s\n" "$BASHPID" >> "$TRACE"; fi' DEBUG
for ((i=0; i<19; i++)); do _pid_alive "$parent_shell"; done
trap - DEBUG
'''
            for funcs, expected in ((LEGACY_LIVENESS, 19), (LIVENESS, 0)):
                with self.subTest(expected_subshells=expected):
                    trace.write_text("")
                    self.assertEqual(self.run_shell(body, funcs, {"TRACE": str(trace)}), "")
                    self.assertEqual(len(set(trace.read_text().splitlines())), expected)

    def test_eperm_path_retains_one_diagnostic_subshell_per_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            trace = Path(temp) / "trace"
            trace.write_text("")
            body = r'''
kill() { printf 'Operation not permitted' >&2; return 1; }
parent_shell=$BASHPID
set -T
trap 'if [ "$BASHPID" != "$parent_shell" ]; then printf "%s\n" "$BASHPID" >> "$TRACE"; fi' DEBUG
for ((i=0; i<19; i++)); do _pid_alive 123; done
trap - DEBUG
'''
            self.assertEqual(self.run_shell(body, extra_env={"TRACE": str(trace)}), "")
            self.assertEqual(len(set(trace.read_text().splitlines())), 19)

    def test_live_foreign_pid_is_still_rejected_by_pattern_match(self):
        funcs = LIVENESS + "\n" + function("_pid_matches_worker")
        body = r'''
kill() { return 0; }
pgrep() { printf '122\n124\n'; }
if _pid_matches_worker 123 fixture; then exit 9; fi
'''
        self.assertEqual(self.run_shell(body, funcs), "")

    def test_alive_result_is_not_cached_across_polls(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            body = r'''
kill() {
    if [ ! -f "$FIRST" ]; then printf first > "$FIRST"; return 0; fi
    printf 'No such process' >&2
    return 1
}
_pid_alive 123
if _pid_alive 123; then exit 9; fi
'''
            self.assertEqual(self.run_shell(body, extra_env={"FIRST": str(first)}), "")

    def test_existing_worker_adoption_keeps_matching_pidfile(self):
        funcs = "\n".join(function(name) for name in (
            "_pid_alive", "_pid_matches_worker", "_pidfile_for_worker",
            "_pattern_for_worker", "_find_existing_worker_pid",
        ))
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "tmp/state/chat_worker.pid"
            pidfile.parent.mkdir(parents=True)
            pidfile.write_text("123\n")
            body = r'''
kill() { return 0; }
pgrep() { printf '122\n123\n124\n'; }
_find_existing_worker_pid chat_worker
'''
            self.assertEqual(self.run_shell(body, funcs, cwd=temp), "123\n")
            self.assertEqual(pidfile.read_text(), "123\n")

    def test_multi_hit_pattern_keeps_correct_worker_without_pipefail(self):
        funcs = LIVENESS + "\n" + function("_pid_matches_worker")
        body = r'''
kill() { return 0; }
pgrep() { printf '122\n123\n124\n'; }
_pid_matches_worker 123 fixture
'''
        self.assertEqual(self.run_shell(body, funcs), "")

    def test_dead_pid_cannot_become_matching_worker(self):
        funcs = LIVENESS + "\n" + function("_pid_matches_worker")
        body = r'''
kill() { printf 'No such process' >&2; return 1; }
pgrep() { printf '123\n'; }
if _pid_matches_worker 123 fixture; then exit 9; fi
'''
        self.assertEqual(self.run_shell(body, funcs), "")


if __name__ == "__main__":
    unittest.main()
