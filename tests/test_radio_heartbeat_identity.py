"""Function-only radio heartbeat fixtures; never launch a worker or send signals."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "workers/radio_worker.sh").read_text()


def function(name):
    match = re.search(r"(?m)^" + name + r"\(\) \{\n.*?^\}", SOURCE, re.S)
    if match is None:
        raise AssertionError(f"missing function {name}")
    return match.group()


class RadioHeartbeatIdentityTests(unittest.TestCase):
    def run_fixture(self, change="unchanged", initially_readable=True):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "identity").write_text("birth-1" if initially_readable else "")
            script = function("_start_pid_heartbeat") + r'''
kill() { printf 'unexpected signal probe\n' >&2; exit 97; }
_radio_pid_identity() {
    local value
    IFS= read -r value < "$FIXTURE/identity" || [ -n "$value" ] || return 1
    [ -n "$value" ] || return 1
    printf -v "$2" '%s' "$value"
}
docich_poll_sleep() {
    calls=$((calls + 1))
    printf '%s\n' "$1" >> "$FIXTURE/waits"
    if [ "$calls" = 1 ]; then
        case "$CHANGE" in
        gone) printf '' > "$FIXTURE/identity" ;;
        reused) printf 'birth-2' > "$FIXTURE/identity" ;;
        esac
        if [ "$CHANGE" != unchanged ]; then
            printf 'replacement\n' > "$PID_FILE"
        fi
    fi
    # Bound the legacy positive control too, without killing any process.
    [ "$calls" -lt 3 ] || exit 0
}
PID_FILE="$FIXTURE/worker.pid"
calls=0
_HEARTBEAT_PID=''
if _start_pid_heartbeat; then printf 'started\n'; else printf 'unavailable\n'; fi
if [ -n "$_HEARTBEAT_PID" ]; then wait "$_HEARTBEAT_PID"; fi
printf '%s\n' "$$"
'''
            env = dict(os.environ, FIXTURE=temp, CHANGE=change,
                       WORKER_PID_HEARTBEAT_INTERVAL="5")
            result = subprocess.run(["bash", "-c", script], env=env,
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            if change == "unchanged" and initially_readable:
                self.assertEqual((root / "waits").read_text().splitlines(), ["5"] * 3)
            path = root / "worker.pid"
            return result.stdout.splitlines(), path.read_text() if path.exists() else None

    def test_live_same_identity_keeps_parent_pid_and_cadence(self):
        output, pidfile = self.run_fixture()
        self.assertEqual(output[0], "started")
        self.assertEqual(pidfile, output[1] + "\n")

    def test_parent_disappearance_stops_writes_and_preserves_replacement(self):
        _, pidfile = self.run_fixture("gone")
        self.assertEqual(pidfile, "replacement\n")

    def test_reused_pid_stops_writes_and_preserves_replacement(self):
        _, pidfile = self.run_fixture("reused")
        self.assertEqual(pidfile, "replacement\n")

    def test_unknown_initial_identity_never_starts_a_writer(self):
        output, pidfile = self.run_fixture(initially_readable=False)
        self.assertEqual(output[0], "unavailable")
        self.assertIsNone(pidfile)

    def test_change_between_capture_and_first_write_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            script = function("_start_pid_heartbeat") + r'''
kill() { exit 97; }
reads=0
_radio_pid_identity() {
    reads=$((reads + 1))
    printf -v "$2" '%s' "birth-$reads"
}
docich_poll_sleep() { exit 0; }
PID_FILE="$FIXTURE/worker.pid"
_start_pid_heartbeat
wait "$_HEARTBEAT_PID"
'''
            result = subprocess.run(["bash", "-c", script],
                                    env=dict(os.environ, FIXTURE=temp),
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(temp) / "worker.pid").exists())


class RadioProcessIdentityTests(unittest.TestCase):
    def run_shell(self, body, macos_fixture=False):
        funcs = "\n".join(function(name) for name in
                          ("_radio_linux_identity", "_radio_pid_identity"))
        if macos_fixture:
            # Mock platform detection only; process observations stay fixtures.
            self.assertIn("if [ -d /proc ]; then", funcs)
            funcs = funcs.replace("if [ -d /proc ]; then", "if false; then")
        result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + funcs + "\n" + body],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    @staticmethod
    def proc_stat(state="S", ticks="456"):
        # Field 22 is starttime; comm may contain spaces and closing parentheses.
        return "123 (radio ) helper) " + " ".join([state, "1"] + ["0"] * 17 + [ticks])

    def test_linux_starttime_parser_handles_comm_parentheses(self):
        raw = self.proc_stat()
        self.assertEqual(self.run_shell(
            f"_radio_linux_identity '{raw}' value\nprintf '%s' \"$value\""), "linux:456")

    def test_linux_dead_and_malformed_records_fail_closed(self):
        for raw in (self.proc_stat("Z"), self.proc_stat("X"), self.proc_stat("?"),
                    self.proc_stat(ticks="oops"), "", "123 (short) S 1"):
            with self.subTest(raw=raw):
                self.assertEqual(self.run_shell(
                    f"value=unchanged\nif _radio_linux_identity '{raw}' value; then exit 9; fi\n"
                    "[ \"$value\" = unchanged ]"), "")

    def test_macos_fixture_identity_is_stable_without_signals(self):
        self.assertEqual(self.run_shell(r'''
kill() { exit 97; }
ps() { printf 'Sun Oct 18 01:02:03 2026 S+\n'; }
_radio_pid_identity 123 first
_radio_pid_identity 123 second
[ "$first" = 'ps:Sun Oct 18 01:02:03 2026' ] && [ "$first" = "$second" ]
''', macos_fixture=True), "")

    @unittest.skipUnless(Path("/proc/self/stat").is_file(), "Linux /proc reader runs in Linux CI")
    def test_linux_reader_observes_only_its_fixture_shell(self):
        self.assertEqual(self.run_shell(r'''
kill() { exit 97; }; ps() { exit 98; }
_radio_pid_identity "$$" first
_radio_pid_identity "$$" second
case "$first" in linux:*) ;; *) exit 9 ;; esac
[ "$first" = "$second" ]
'''), "")

    def test_macos_dead_unreadable_and_empty_observations_fail_closed(self):
        for observation in ("", "Sun Oct 18 01:02:03 2026 Z+", "malformed"):
            with self.subTest(observation=observation):
                self.assertEqual(self.run_shell(
                    f"ps() {{ printf '%s\\n' '{observation}'; }}\n"
                    "if _radio_pid_identity 123 value; then exit 9; fi",
                    macos_fixture=True), "")
        self.assertEqual(self.run_shell(
            "ps() { return 1; }; if _radio_pid_identity 123 value; then exit 9; fi",
            macos_fixture=True), "")

    def test_invalid_pids_are_rejected_without_process_tools(self):
        for pid in ("", "0", "000", "-1", "1 2", "12;exit", "abc"):
            with self.subTest(pid=pid):
                self.assertEqual(self.run_shell(
                    "ps() { exit 97; }; kill() { exit 98; }\n"
                    f"if _radio_pid_identity '{pid}' value; then exit 9; fi"), "")


if __name__ == "__main__":
    unittest.main()
