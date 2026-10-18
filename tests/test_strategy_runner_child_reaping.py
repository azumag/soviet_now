"""Regression tests for strategy_runner's detached child ownership."""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import strategy_runner


class StrategyRunnerChildReapingTest(unittest.TestCase):
    def test_tracked_child_is_reaped_without_a_later_notification(self):
        if not Path("/proc").is_dir():
            self.skipTest("requires Linux procfs")

        strategy_runner._fire_and_forget_processes.clear()
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.05)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = proc.pid
        strategy_runner._track_fire_and_forget_process(proc)
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if proc.returncode is not None and not Path(f"/proc/{pid}").exists():
                    break
                time.sleep(0.01)
            self.assertIsNotNone(proc.returncode)
            self.assertFalse(Path(f"/proc/{pid}").exists(), "child remained as a zombie")
            self.assertNotIn(proc, strategy_runner._fire_and_forget_processes)
        finally:
            if proc.returncode is None:
                proc.kill()
                proc.wait(timeout=2)
            strategy_runner._fire_and_forget_processes.clear()


if __name__ == "__main__":
    unittest.main()
