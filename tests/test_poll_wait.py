"""Regression tests for lib/poll_wait.sh (#970 forkless poll waits).

The resident workers split their poll interval into slices so they can notice
tmp/stop promptly. Those slices used to be `/bin/sleep` children, which the
production CPU profile counted as the largest single process-spawn group
(`sleep <- worker:*`). These tests pin the two properties that make the
replacement safe:

* it still waits the requested wall-clock time, and
* it does not execute `sleep` at all while doing so,

plus the fallback path and the "no fifo left behind" hygiene that keeps the
runtime tree clean.
"""

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib" / "poll_wait.sh"

RESIDENT_WORKERS = (
    "workers/audio_worker.sh",
    "workers/chat_worker.sh",
    "workers/kick_worker.sh",
    "workers/youtube_worker.sh",
    "workers/poll_worker.sh",
    "workers/prediction_worker.sh",
    "workers/stream_noon_audit.sh",
    "workers/radio_worker.sh",
    "soviet_watchdog.sh",
    "bgm_worker.sh",
)


class PollWaitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="poll-wait-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wait_dir = self.root / "wait"
        self.wait_dir.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.sleep_log = self.root / "sleep.log"
        self._install_sleep_shim()
        self.env = {
            "PATH": f"{self.bin}:{os.path.dirname(shutil.which('sleep') or '/bin')}:/usr/bin:/bin",
            "HOME": str(self.root),
            "DOCICH_POLL_WAIT_DIR": str(self.wait_dir),
            "SLEEP_LOG": str(self.sleep_log),
            "LIB": str(LIB),
            "TMPDIR": str(self.root),
        }

    def _install_sleep_shim(self):
        real_sleep = shutil.which("sleep") or "/bin/sleep"
        shim = self.bin / "sleep"
        shim.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SLEEP_LOG"\nexec ' + real_sleep + ' "$@"\n'
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def sleep_invocations(self):
        if not self.sleep_log.exists():
            return []
        return [line for line in self.sleep_log.read_text().splitlines() if line]

    def run_bash(self, script, env=None, timeout=60, cwd=None):
        merged = dict(self.env)
        if env:
            merged.update(env)
        return subprocess.run(
            ["bash", "-c", script],
            env=merged,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_builtin_wait_matches_sleep_without_running_it(self):
        start = time.monotonic()
        result = self.run_bash('source "$LIB"\ndocich_poll_sleep 2\necho "$_DOCICH_POLL_WAIT_READY"\n')
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1", result.stderr)
        self.assertEqual(self.sleep_invocations(), [], "forkless wait must not exec sleep")
        self.assertGreaterEqual(elapsed, 1.5, elapsed)
        self.assertLess(elapsed, 5.0, elapsed)

    def test_one_second_slice_keeps_stop_file_latency(self):
        marker = self.root / "stop"
        script = (
            'source "$LIB"\n'
            'marker="$STOP_MARKER"\n'
            '( sleep 0.4; touch "$marker" ) &\n'
            'i=5\n'
            'while [ "$i" -gt 0 ]; do\n'
            '  [ -f "$marker" ] && break\n'
            '  docich_poll_sleep 1\n'
            '  i=$((i - 1))\n'
            'done\n'
            'echo "$((5 - i))"\n'
        )
        start = time.monotonic()
        result = self.run_bash(script, {"STOP_MARKER": str(marker)})
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1")
        self.assertLess(elapsed, 3.0, elapsed)

    def test_open_leaves_no_fifo_behind_and_is_idempotent(self):
        script = (
            'source "$LIB"\n'
            'docich_poll_wait_open || exit 3\n'
            'docich_poll_wait_open || exit 4\n'
            'echo "$_DOCICH_POLL_WAIT_READY"\n'
            'ls -A "$DOCICH_POLL_WAIT_DIR"\n'
        )
        result = self.run_bash(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["1"], result.stdout)

    def test_falls_back_to_sleep_when_fifo_unavailable(self):
        start = time.monotonic()
        result = self.run_bash(
            'source "$LIB"\ndocich_poll_sleep 2\n',
            {"DOCICH_POLL_WAIT_DIR": str(self.root / "missing")},
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sleep_invocations(), ["2"])
        self.assertGreaterEqual(elapsed, 1.5, elapsed)

    def test_zero_wait_is_clamped_to_one_second(self):
        # docich#2688: 0 is never a valid poll wait. It used to return
        # immediately, which let a loop whose interval was configured to 0 spin
        # on python3/state reads and pin a core.
        start = time.monotonic()
        result = self.run_bash('source "$LIB"\ndocich_poll_sleep 0\necho ok\n')
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")
        self.assertGreaterEqual(elapsed, 0.9, elapsed)

    def test_missing_lib_degrades_to_sleep_instead_of_aborting(self):
        # Some callers (and several existing tests) run a worker file copied
        # without the shared lib. The worker must keep working there, not die.
        bare = self.root / "bare"
        bare.mkdir()
        script = (
            'source ./lib/poll_wait.sh 2>/dev/null || '
            'docich_poll_sleep() { sleep "${1:-1}" 2>/dev/null || true; }\n'
            "docich_poll_sleep 1\n"
            "echo degraded\n"
        )
        start = time.monotonic()
        result = self.run_bash(script, cwd=str(bare))
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "degraded")
        self.assertEqual(self.sleep_invocations(), ["1"])
        self.assertGreaterEqual(elapsed, 0.8, elapsed)

    def test_resident_workers_use_the_shared_helper(self):
        converted = (
            "\t\t[ -f tmp/stop ] && break 2\n\t\tsleep 1\n",
            "\t\t[ -f tmp/stop ] && return 0\n\t\tsleep 1\n",
            '\t\tsleep "$_sleep_slice" || true\n',
            '\twhile [ "$remaining" -gt 0 ]; do\n\t\tsleep 1\n',
        )
        for relative in RESIDENT_WORKERS:
            body = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(worker=relative):
                self.assertIn("lib/poll_wait.sh", body)
                self.assertIn("docich_poll_sleep", body)
                # A missing helper lib must degrade to `sleep`, never abort the
                # worker (partial checkouts/tests copy worker files alone).
                self.assertIn('docich_poll_sleep() { sleep "${1:-1}"', body)
                # The per-slice wait must not regress to a bare `sleep`.
                for snippet in converted:
                    self.assertNotIn(snippet, body)

    def test_pid_file_heartbeats_are_forkless(self):
        # The pid-file heartbeat subshell runs every WORKER_PID_HEARTBEAT_INTERVAL
        # seconds; a bare `sleep` there was a second `sleep <- worker:*` source
        # (12/60s per worker in the 2026-10-08 production profile).
        heartbeat_users = (
            "workers/audio_worker.sh",
            "workers/chat_worker.sh",
            "workers/kick_worker.sh",
            "workers/radio_worker.sh",
            "workers/youtube_worker.sh",
        )
        for relative in heartbeat_users:
            body = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(worker=relative):
                # A tab immediately before `sleep` cannot be our `docich_poll_sleep`
                # call (which has `_` there), so this catches a bare fork.
                self.assertNotIn('\tsleep "${WORKER_PID_HEARTBEAT_INTERVAL', body)
                self.assertIn('\tdocich_poll_sleep "${WORKER_PID_HEARTBEAT_INTERVAL', body)

    def test_say_enqueue_waits_are_forkless(self):
        # say_enqueue.sh runs as a short-lived child of audio_worker and its
        # per-second heartbeat / player-wait loops were attributed to
        # `sleep <- worker:audio_worker`.
        body = (ROOT / "say_enqueue.sh").read_text(encoding="utf-8")
        self.assertIn("lib/poll_wait.sh", body)
        self.assertIn('docich_poll_sleep() { sleep "${1:-1}"', body)
        self.assertIn("\t\tdocich_poll_sleep 1\n\t\twaited=$((waited + 1))\n", body)
        self.assertNotIn("\t\t_touch_lock_heartbeat\n\t\tsleep 1\n", body)
        self.assertNotIn('\t\tsleep 1\n\t\tif [ "$max_wait_sec" -gt 0 ]; then', body)


RELOAD_GATED_SCRIPTS = (
    "workers/youtube_worker.sh",
    "workers/chat_worker.sh",
    "workers/kick_worker.sh",
    "workers/audio_worker.sh",
    "workers/prediction_worker.sh",
    "improve_daemon.sh",
)


def _find_modern_bash():
    """Return a bash >= 4.2 (needed for printf '%(%s)T'), or None."""
    for candidate in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if not os.path.exists(candidate):
            continue
        try:
            out = subprocess.run(
                [candidate, "-c", 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"'],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            major, minor = (int(part) for part in out.split("."))
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if (major, minor) >= (4, 2):
            return candidate
    return None


MODERN_BASH = _find_modern_bash()


class ReloadGateTest(unittest.TestCase):
    """docich#4144: docich_reload_due/mark/retry skip the per-tick re-source."""

    def setUp(self):
        if MODERN_BASH is None:
            self.skipTest("bash >= 4.2 (printf %(%s)T) is not available")
        self.temp = tempfile.TemporaryDirectory(prefix="reload-gate-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "core").mkdir()
        (self.root / "tmp" / "state").mkdir(parents=True)
        (self.root / "eloop_lib.sh").write_text("# eloop\n")
        (self.root / ".env").write_text("# env\n")
        (self.root / "core" / "a.sh").write_text("# a\n")
        self.env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.root),
            "LIB": str(LIB),
            "TMPDIR": str(self.root),
        }

    def run_bash(self, script, env=None, timeout=60):
        merged = dict(self.env)
        if env:
            merged.update(env)
        return subprocess.run(
            [MODERN_BASH, "-c", 'source "$LIB"\nstamp=tmp/state/w.reload_stamp\n' + script],
            env=merged,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def due(self, script, env=None, quiet=True):
        """Run script, then print 'due' or 'idle' for the final docich_reload_due."""
        result = self.run_bash(
            script + '\nif docich_reload_due "$stamp"; then echo due; else echo idle; fi\n',
            env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if quiet:
            self.assertEqual(result.stderr, "")
        return result.stdout.split()[-1]

    def test_due_before_any_mark_even_if_stamp_exists(self):
        self.assertEqual(self.due(': >"$stamp"'), "due")

    def test_not_due_right_after_mark(self):
        self.assertEqual(self.due('docich_reload_mark "$stamp"'), "idle")

    def test_due_when_watched_file_is_newer_than_stamp(self):
        for rel in (".env", "eloop_lib.sh", "core/a.sh"):
            with self.subTest(watched=rel):
                self.assertEqual(
                    self.due(
                        'docich_reload_mark "$stamp"\n'
                        'touch -t 200101010000 "$stamp"\n'
                        'touch "$WATCHED"\n',
                        {"WATCHED": rel},
                    ),
                    "due",
                )

    def test_not_due_when_watched_files_are_older_than_stamp(self):
        self.assertEqual(
            self.due(
                'touch -t 200101010000 .env eloop_lib.sh core/a.sh\n'
                'docich_reload_mark "$stamp"'
            ),
            "idle",
        )

    def test_due_when_max_age_is_zero(self):
        self.assertEqual(
            self.due('docich_reload_mark "$stamp"', {"WORKER_RELOAD_MAX_AGE_SEC": "0"}),
            "due",
        )

    def test_due_after_max_age_elapsed(self):
        self.assertEqual(
            self.due(
                'docich_reload_mark "$stamp"\n'
                'sleep 2',
                {"WORKER_RELOAD_MAX_AGE_SEC": "1"},
            ),
            "due",
        )

    def test_retry_makes_it_due_again(self):
        self.assertEqual(
            self.due('docich_reload_mark "$stamp"\ndocich_reload_retry'),
            "due",
        )

    def test_due_when_stamp_is_empty_or_removed(self):
        self.assertEqual(self.due('docich_reload_mark ""'), "due")
        self.assertEqual(
            self.due('docich_reload_mark "$stamp"\nrm -f "$stamp"'),
            "due",
        )

    def test_mark_failure_keeps_reloading(self):
        # Stamp directory missing: mark fails, so the gate must stay open.
        self.assertEqual(
            self.due(
                'stamp=tmp/missing/w.stamp\ndocich_reload_mark "$stamp" || true',
                quiet=False,
            ),
            "due",
        )

    def test_gate_helpers_spawn_no_child_process(self):
        # Prove "forkless" structurally: no command substitution, pipe or
        # backtick in the helper bodies, and no external command resolved from
        # PATH while they run (PATH is empty, so any fork+exec would fail the
        # stamp check or print "command not found").
        result = self.run_bash(
            'declare -f docich_reload_due docich_reload_mark docich_reload_retry\n',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = result.stdout
        self.assertIn("docich_reload_due", body)
        # `$((` is arithmetic expansion and does not fork.
        self.assertNotRegex(body, r"\$\((?!\()")
        for token in ("`", "<(", ">("):
            self.assertNotIn(token, body, token)
        # `||` lists and the `'' | *[!0-9]*)` case alternation are not pipes.
        no_pipe = re.sub(r"(?m)^\s*'' \| \*\[!0-9\]\*\)$", "", body).replace("||", "")
        self.assertNotIn("|", no_pipe, "pipe in helper body")
        result = self.run_bash(
            'PATH=/nonexistent\n'
            'docich_reload_mark "$stamp" || exit 3\n'
            'docich_reload_due "$stamp"; rc=$?\n'
            'echo "rc=$rc"\n',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "rc=1")
        self.assertEqual(result.stderr, "")


class ReloadGatedScriptsTest(unittest.TestCase):
    def test_per_tick_sources_are_guarded_by_the_reload_gate(self):
        for relative in RELOAD_GATED_SCRIPTS:
            with self.subTest(script=relative):
                body = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn('docich_reload_due "$RELOAD_STAMP_FILE"', body)
                self.assertRegex(body, r'(?m)^RELOAD_STAMP_FILE="tmp/state/[^"\n]*\.reload_stamp"$')
                for name in ("docich_reload_due", "docich_reload_mark", "docich_reload_retry"):
                    self.assertIn(f"declare -F {name} >/dev/null 2>&1 || {name}() {{ return 0; }}", body)
                lines = body.splitlines()
                sources = [
                    index
                    for index, line in enumerate(lines)
                    if re.match(r"^\s*(if ! )?source \./eloop_lib\.sh\b", line)
                ]
                guarded = [
                    index
                    for index in sources
                    if any("docich_reload_due" in lines[j] for j in range(max(0, index - 10), index))
                ]
                self.assertEqual(len(guarded), 1, (sources, guarded))
                gate_line = next(
                    j
                    for j in range(guarded[0] - 1, max(-1, guarded[0] - 11), -1)
                    if "docich_reload_due" in lines[j]
                )
                self.assertIn("docich_reload_mark", "\n".join(lines[gate_line:guarded[0]]))
                # Startup source stays unconditional, and it is the only other
                # top-level one besides _reload_runtime's.
                self.assertIn("source ./eloop_lib.sh", lines)


if __name__ == "__main__":
    unittest.main()
