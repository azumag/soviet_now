import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib import corner_notice_state as cns


class CornerNoticeStateTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state = self.root / "docich" / "run-soren-live"
        self.state.mkdir(parents=True)
        self.now = 1_000_000.0

    def tearDown(self):
        self.tempdir.cleanup()

    def write(self, **overrides):
        payload = {
            "schema": 1,
            "corner": "ninvaders",
            "label": "スペースインベーダー",
            "when": "18:00",
            "due_at": int(self.now + 600),
            "source": "auto",
            "hold": None,
            "decided_at": int(self.now),
            "updated_at": int(self.now),
        }
        payload.update(overrides)
        path = self.state / "next_corner.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def mode(self, **overrides):
        if overrides:
            self.write(**overrides)
        return cns.mode(self.state, now=self.now)

    def test_missing_record_is_clear(self):
        self.assertEqual(cns.load(self.state, now=self.now), None)
        self.assertEqual(cns.mode(self.state, now=self.now), "clear")
        self.assertFalse(cns.prediction_blocked(self.state, now=self.now))
        self.assertFalse(cns.improvement_blocked(self.state, now=self.now))

    def test_clock_promise_blocks_prediction_and_improvement(self):
        self.write()
        self.assertEqual(self.mode(), "time")
        self.assertTrue(cns.prediction_blocked(self.state, now=self.now))
        self.assertTrue(cns.improvement_blocked(self.state, now=self.now))

    def test_pending_allows_prediction_but_not_improvement(self):
        self.write(hold="prediction-pending")
        self.assertEqual(self.mode(), "pending")
        self.assertFalse(cns.prediction_blocked(self.state, now=self.now))
        self.assertTrue(cns.improvement_blocked(self.state, now=self.now))

    def test_prediction_hold_blocks_both_new_cycles(self):
        self.write(hold="prediction", remaining_games=48)
        self.assertEqual(self.mode(), "prediction")
        self.assertTrue(cns.prediction_blocked(self.state, now=self.now))
        self.assertTrue(cns.improvement_blocked(self.state, now=self.now))

    def test_unknown_hold_is_fail_safe(self):
        self.write(hold="weather")
        self.assertEqual(self.mode(), "blocked")
        self.assertTrue(cns.prediction_blocked(self.state, now=self.now))
        self.assertTrue(cns.improvement_blocked(self.state, now=self.now))

    def test_stale_and_future_records_are_clear(self):
        self.write(updated_at=int(self.now - cns.MAX_AGE_SECONDS - 1))
        self.assertEqual(cns.mode(self.state, now=self.now), "clear")
        self.write(updated_at=int(self.now + 60))
        self.assertEqual(cns.mode(self.state, now=self.now), "clear")

    def test_invalid_records_are_clear(self):
        path = self.state / "next_corner.json"
        for payload in (
            "{",
            json.dumps({"schema": 2}),
            json.dumps({"schema": 1, "corner": "", "label": "x", "when": "18:00",
                        "due_at": 1, "source": "auto", "hold": None,
                        "updated_at": int(self.now)}),
            json.dumps({"schema": 1, "corner": "x", "label": "x", "when": "18:00",
                        "due_at": "soon", "source": "auto", "hold": None,
                        "updated_at": int(self.now)}),
            json.dumps({"schema": 1, "corner": "x", "label": "x", "when": "18:00",
                        "due_at": 1, "source": "guess", "hold": None,
                        "updated_at": int(self.now)}),
        ):
            path.write_text(payload, encoding="utf-8")
            self.assertEqual(cns.mode(self.state, now=self.now), "clear")

    def test_resolve_state_dir_prefers_explicit_then_env(self):
        self.assertEqual(cns.resolve_state_dir(self.state), self.state)
        old = os.environ.get("DOCICH_STATE_DIR")
        try:
            os.environ["DOCICH_STATE_DIR"] = str(self.state)
            self.assertEqual(cns.resolve_state_dir(), self.state)
        finally:
            if old is None:
                os.environ.pop("DOCICH_STATE_DIR", None)
            else:
                os.environ["DOCICH_STATE_DIR"] = old

    def test_resolve_state_dir_uses_the_sibling_checkout(self):
        old = os.environ.pop("DOCICH_STATE_DIR", None)
        cwd = os.getcwd()
        soren = self.root / "soren"
        soren.mkdir()
        try:
            os.chdir(soren)
            self.assertEqual(cns.resolve_state_dir().resolve(), self.state.resolve())
        finally:
            os.chdir(cwd)
            if old is not None:
                os.environ["DOCICH_STATE_DIR"] = old

    def test_cli_modes(self):
        script = REPO_ROOT / "lib" / "corner_notice_state.py"
        env = dict(os.environ, DOCICH_STATE_DIR=str(self.state))
        now = int(time.time())
        self.write(hold="prediction-pending", due_at=now + 600, updated_at=now)
        result = subprocess.run(
            [sys.executable, str(script), "mode"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "pending")
        result = subprocess.run(
            [sys.executable, str(script), "blocked", "prediction"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "0")
        result = subprocess.run(
            [sys.executable, str(script), "blocked", "improvement"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
