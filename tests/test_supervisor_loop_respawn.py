"""A missing Soren loop returns to normal supervision (docich #2061).

In the 2026-10-11 incident the Soren loop was absent while the bridge
watchdog and the common delivery processes stayed up; the normal supervisor
(`start_all.sh`, run by `soren-runtime.service`) then respawned the loop.
These tests pin both halves of that contract:

- an absent or unusable loop is *not* adopted, so the supervisor starts a
  fresh one (the recovery path #2061 relies on);
- the supervisor's worker set is game/worker-scoped and never manages the
  common delivery units (rtmp relay/bridge, bgm, shared overlay, xvfb), so
  respawning the loop cannot restart or stop the shared services.

Only function bodies are extracted from `start_all.sh`; the supervisor is
never sourced and no real worker is started.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "start_all.sh").read_text()

# The shared delivery surface that must never be part of a loop respawn.
COMMON_DELIVERY_UNITS = (
    "soren-rtmp-relay",
    "soren-rtmps-bridge",
    "soren-bgm",
    "soren-shared-overlay",
    "xvfb",
)


def function(name):
    match = re.search(r"(?m)^" + name + r"\(\) \{\n.*?^\}", SOURCE, re.S)
    if match is None:
        raise AssertionError(f"missing function {name}")
    return match.group()


RESPAWN_FUNCTIONS = "\n".join(
    function(name)
    for name in (
        "_pid_alive",
        "_pidfile_for_worker",
        "_pattern_for_worker",
        "_pid_matches_worker",
        "_soren_loop_adoptable",
        "_find_existing_worker_pid",
    )
)


class SupervisorLoopRespawnTests(unittest.TestCase):
    """An absent/stale loop is respawned; a healthy one is adopted."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tmp" / ".soren_loop.lock").mkdir(parents=True)
        (self.root / "logs").mkdir()
        self.write_state("MOVE")

    def tearDown(self):
        self.tmp.cleanup()

    def write_state(self, state):
        (self.root / "game_state.json").write_text(
            json.dumps({"state": state, "score": 1, "pieces": []}), encoding="utf-8"
        )

    def age_loop_log(self, seconds):
        path = self.root / "logs" / "soren_loop.log"
        path.write_text("loop\n", encoding="utf-8")
        stamp = path.stat().st_mtime - seconds
        os.utime(path, (stamp, stamp))

    def run_shell(self, body, extra_env=None):
        script = "set -euo pipefail\n" + RESPAWN_FUNCTIONS + "\n" + body
        env = dict(os.environ)
        env.update(extra_env or {})
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=self.root, env=env, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_absent_loop_is_returned_to_supervision(self):
        # No lock pidfile and no matching process: the supervisor must start a
        # fresh loop rather than treat the missing loop as already running.
        out = self.run_shell(
            r'''
_log() { :; }
pgrep() { return 1; }
if _find_existing_worker_pid soren_loop; then printf 'adopted'; else printf 'restart'; fi
'''
        )
        self.assertEqual(out, "restart")

    def test_dead_lock_pid_is_cleared_and_the_loop_restarted(self):
        lock = self.root / "tmp" / ".soren_loop.lock" / "pid"
        lock.write_text("4242\n", encoding="utf-8")
        out = self.run_shell(
            r'''
_log() { :; }
kill() { return 1; }
pgrep() { return 1; }
if _find_existing_worker_pid soren_loop; then printf 'adopted'; else printf 'restart'; fi
'''
        )
        self.assertEqual(out, "restart")
        self.assertFalse(lock.exists(), "a dead lock pid must be cleared before a restart")

    def test_stale_stop_loop_is_restarted_instead_of_adopted(self):
        # A loop parked on a STOP board with a stale log is the wedge shape:
        # adopting it would freeze the board, so the supervisor must replace it.
        (self.root / "tmp" / ".soren_loop.lock" / "pid").write_text("4242\n", encoding="utf-8")
        self.write_state("STOP")
        self.age_loop_log(3600)
        out = self.run_shell(
            r'''
_log() { :; }
kill() { return 0; }
pgrep() { printf '4242\n'; }
if _find_existing_worker_pid soren_loop; then printf 'adopted'; else printf 'restart'; fi
'''
        )
        self.assertEqual(out, "restart")

    def test_healthy_live_loop_is_adopted_without_a_restart(self):
        # Positive control: a live loop on a progressing board must not be
        # restarted, or normal supervision would churn the running game.
        (self.root / "tmp" / ".soren_loop.lock" / "pid").write_text("4242\n", encoding="utf-8")
        out = self.run_shell(
            r'''
_log() { :; }
kill() { return 0; }
pgrep() { printf '4242\n'; }
if pid=$(_find_existing_worker_pid soren_loop); then printf '%s' "$pid"; else printf 'restart'; fi
'''
        )
        self.assertEqual(out, "4242")

    def test_fresh_stop_loop_is_still_adopted(self):
        # A STOP board younger than the stale window may precede the founding
        # counter; it must keep being adopted (the #2012/#2015 fence).
        (self.root / "tmp" / ".soren_loop.lock" / "pid").write_text("4242\n", encoding="utf-8")
        self.write_state("STOP")
        self.age_loop_log(0)
        out = self.run_shell(
            r'''
_log() { :; }
kill() { return 0; }
pgrep() { printf '4242\n'; }
if pid=$(_find_existing_worker_pid soren_loop); then printf '%s' "$pid"; else printf 'restart'; fi
'''
        )
        self.assertEqual(out, "4242")


class SupervisorCommonDeliveryBoundaryTests(unittest.TestCase):
    """The supervisor owns workers, never the shared delivery units."""

    def worker_arrays(self):
        names = re.search(r"declare -a WORKER_NAMES=\((.*?)\n\)", SOURCE, re.S)
        cmds = re.search(r"declare -a WORKER_CMDS=\((.*?)\n\)", SOURCE, re.S)
        self.assertIsNotNone(names, "start_all.sh must declare WORKER_NAMES")
        self.assertIsNotNone(cmds, "start_all.sh must declare WORKER_CMDS")
        return (
            re.findall(r'"([^"]+)"', names.group(1)),
            re.findall(r'"([^"]+)"', cmds.group(1)),
        )

    def test_soren_loop_is_supervised(self):
        names, cmds = self.worker_arrays()
        self.assertIn("soren_loop", names)
        self.assertIn("./soren_loop.sh", cmds)
        self.assertEqual(len(names), len(cmds), "every supervised worker needs a command")

    def test_common_delivery_units_are_not_supervised_workers(self):
        names, cmds = self.worker_arrays()
        for unit in COMMON_DELIVERY_UNITS:
            with self.subTest(unit=unit):
                self.assertNotIn(unit, names)
                self.assertNotIn(unit, cmds)
                self.assertNotIn(unit, SOURCE)

    def test_supervisor_never_manages_systemd_units(self):
        # A loop respawn must not be able to restart or stop the shared
        # services; those stay systemd-owned (deploy/*.service).
        self.assertNotIn("systemctl", SOURCE)

    def test_stop_path_is_scoped_to_the_supervised_workers(self):
        stop = function("_stop_all_workers")
        self.assertIn("WORKER_PIDS", stop)
        self.assertIn("WORKER_NAMES", stop)
        for unit in COMMON_DELIVERY_UNITS:
            with self.subTest(unit=unit):
                self.assertNotIn(unit, stop)


if __name__ == "__main__":
    unittest.main()
