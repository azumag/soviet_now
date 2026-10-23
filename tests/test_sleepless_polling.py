"""Regression tests for docich#2688: never run a sleepless polling loop.

The 2026-10-23 incident observed short-lived python3 children at ~85% CPU with
a vanished parent. The children were normal one-shot probes; the bug was a
loop that spawned them without waiting. These tests pin the three guards:

* ``docich_poll_interval`` refuses 0/invalid values,
* ``docich_poll_sleep`` always waits at least a second,
* the canonical probe obeys its interval and does not burn CPU,
* no tracked shell script grows a new infinite loop that spawns python3 with
  no wait/backoff in the loop body.
"""
import os
import re
import resource
import subprocess
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASH = "/bin/bash"

# Loops are "infinite pollers" when their condition can never become false.
_INFINITE_LOOP = re.compile(
    r"^[ \t]*(while\s+(?:true|:|\s*\[\[?\s*1\s*\]\]?|\(\(\s*1\s*\)\))|until\s+false)\b"
)
# Any of these means the iteration yields the CPU before polling again.
_WAIT_TOKENS = re.compile(
    r"\bsleep\b|docich_poll_sleep|\bread\s+-[a-zA-Z]*t|"
    r"\bwait\b|\bselect\b|\b_?wait_\w+|\bdocich_poll_interval\b"
)

FIXED_INTERVAL_SCRIPTS = (
    "improve_daemon.sh",
    "soren_loop.sh",
    "generate_improve_overlay.sh",
    "soren91/run_player_loop.sh",
    "start_all.sh",
)


def _poll_interval(raw, default, minimum):
    script = (
        f'source "{ROOT}/lib/poll_wait.sh"; '
        f'docich_poll_interval "{raw}" "{default}" "{minimum}"'
    )
    return subprocess.check_output([BASH, "-c", script], text=True).strip()


def _infinite_loop_bodies(text):
    """Yield (start_line, body) for every infinite shell loop."""
    lines = text.splitlines()
    stack = []
    heredoc = None
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if heredoc:
            if stripped == heredoc:
                heredoc = None
            continue
        marker = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", stripped)
        if marker and not stripped.startswith("#"):
            heredoc = marker.group(1)
        if stripped.startswith("#"):
            continue
        if _INFINITE_LOOP.match(line):
            stack.append(number)
        elif stripped == "done" or stripped.startswith("done;") or stripped.startswith("done "):
            if stack:
                start = stack.pop()
                yield start, "\n".join(lines[start - 1:number])


class PollIntervalTests(unittest.TestCase):
    def test_invalid_interval_falls_back_to_default(self):
        self.assertEqual(_poll_interval("", 30, 1), "30")
        self.assertEqual(_poll_interval("abc", 30, 1), "30")
        self.assertEqual(_poll_interval("-5", 30, 1), "30")

    def test_zero_and_sub_minimum_are_clamped(self):
        self.assertEqual(_poll_interval("0", 30, 1), "1")
        self.assertEqual(_poll_interval("1", 30, 5), "5")

    def test_minimum_of_one_second_is_enforced(self):
        self.assertEqual(_poll_interval("2", 2, 0), "2")
        self.assertEqual(_poll_interval("", 0, 0), "1")

    def test_poll_sleep_never_returns_immediately(self):
        script = (
            f'source "{ROOT}/lib/poll_wait.sh"; '
            'start=$SECONDS; docich_poll_sleep 0; echo $((SECONDS - start))'
        )
        elapsed = int(subprocess.check_output([BASH, "-c", script], text=True).strip())
        self.assertGreaterEqual(elapsed, 1)


class PollStatusProbeTests(unittest.TestCase):
    def test_probe_cadence_and_cpu(self):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        result = subprocess.run(
            [BASH, str(ROOT / "tools" / "poll_status.sh"), "--count", "3", "--interval", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHON": sys.executable},
        )
        elapsed = time.monotonic() - started
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.startswith("round_version=") for line in lines), lines)
        # Three ticks must actually wait between probes...
        self.assertGreaterEqual(elapsed, 2.0)
        # ...and must not spin while waiting.
        self.assertLess(cpu, 1.5)

    def test_probe_rejects_non_positive_interval(self):
        result = subprocess.run(
            [BASH, str(ROOT / "tools" / "poll_status.sh"), "--once", "--interval", "0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHON": sys.executable},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)


class NoSleeplessPythonLoopTests(unittest.TestCase):
    def test_no_sleepless_python_poll_loops(self):
        files = subprocess.check_output(
            ["git", "ls-files", "*.sh"], cwd=ROOT, text=True
        ).splitlines()
        offenders = []
        for relative in files:
            if relative.startswith("tests/"):
                continue
            path = ROOT / relative
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for start, body in _infinite_loop_bodies(text):
                if "python3" not in body:
                    continue
                if _WAIT_TOKENS.search(body):
                    continue
                offenders.append(f"{relative}:{start}")
        self.assertEqual(
            offenders,
            [],
            "infinite loop spawns python3 without sleep/backoff: " + ", ".join(offenders),
        )

    def test_fixed_scripts_use_validated_interval(self):
        for relative in FIXED_INTERVAL_SCRIPTS:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("docich_poll_interval", text, relative)


if __name__ == "__main__":
    unittest.main()
