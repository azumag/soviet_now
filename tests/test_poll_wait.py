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

    def test_zero_wait_returns_immediately(self):
        start = time.monotonic()
        result = self.run_bash('source "$LIB"\ndocich_poll_sleep 0\necho ok\n')
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")
        self.assertLess(elapsed, 1.0, elapsed)
        self.assertEqual(self.sleep_invocations(), [])

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


if __name__ == "__main__":
    unittest.main()
