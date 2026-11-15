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

    def write_rotation(self, *, pending=None, manual=None, schema=1):
        payload = {
            "schema_version": schema,
            "pending": pending,
            "manual_pending": manual,
        }
        path = self.state / "corner_rotation.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def write_switch(self, **overrides):
        payload = {
            "schema_version": 2,
            "phase": "ready",
            "active": {
                "game": "sorengame",
                "adapter": "soren",
                "runtime_id": "g1-abcdef12",
                "lease_id": "lease-1",
            },
        }
        payload.update(overrides)
        path = self.state / "game_switch.json"
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

    def test_soren_game_on_air_allows_prediction(self):
        self.write_switch()
        self.assertFalse(cns.corner_active(self.state))
        self.assertFalse(cns.prediction_blocked(self.state, now=self.now))
        self.assertFalse(cns.improvement_blocked(self.state, now=self.now))

    def test_corner_view_blocks_prediction_even_during_pending_handoff(self):
        # The rotation withdraws next_corner.json when the corner starts; a
        # leftover pending notice must not override the on-air corner.
        self.write(hold="prediction-pending")
        self.write_switch(active={"game": "paper-view", "adapter": "paper"})
        self.assertTrue(cns.corner_active(self.state))
        self.assertTrue(cns.prediction_blocked(self.state, now=self.now))

    def test_confirmed_slot_blocks_prediction_before_the_switch(self):
        slot = {"corner": "paper", "phase": "selected", "request_id": "r1"}
        self.write_rotation(pending=slot)
        self.assertFalse(cns.corner_active(self.state))
        self.assertTrue(cns.corner_pending(self.state))
        self.assertTrue(cns.prediction_blocked(self.state, now=self.now))
        # Improvement accumulation is not changed by a slot reservation.
        self.assertFalse(cns.improvement_blocked(self.state, now=self.now))
        self.write_rotation(pending=None, manual=slot)
        self.assertTrue(cns.corner_pending(self.state))
        self.assertTrue(cns.prediction_blocked(self.state, now=self.now))

    def test_corner_slot_pending_does_not_override_a_clear_rotation(self):
        self.write_rotation(pending={}, manual={})
        self.write_switch()
        self.assertFalse(cns.corner_pending(self.state))
        self.assertFalse(cns.prediction_blocked(self.state, now=self.now))

    def test_missing_or_invalid_rotation_is_clear(self):
        self.assertFalse(cns.corner_pending(self.state))
        path = self.state / "corner_rotation.json"
        for payload in ("{", "[]", json.dumps({"schema_version": 2, "pending": {"corner": "paper"}})):
            path.write_text(payload, encoding="utf-8")
            self.assertFalse(cns.corner_pending(self.state), payload)
        path.write_text("x" * (cns.GAME_SWITCH_LIMIT + 1), encoding="utf-8")
        self.assertFalse(cns.corner_pending(self.state))

    def test_corner_view_does_not_block_improvement_accumulation(self):
        # Improvement is gated by the notice only: a game completed at the
        # corner boundary must still be accumulated into its batch.
        self.write_switch(active={"game": "paper-view", "adapter": "paper"})
        self.assertFalse(cns.improvement_blocked(self.state, now=self.now))

    def test_transition_and_failed_phases_block_prediction(self):
        for phase in ("validating", "draining", "stopping", "rolling_back",
                      "failed", "recovery_required", "idle"):
            self.write_switch(phase=phase)
            self.assertTrue(cns.corner_active(self.state), phase)
            self.assertTrue(cns.prediction_blocked(self.state, now=self.now), phase)

    def test_ready_without_active_game_blocks_prediction(self):
        self.write_switch(active=None)
        self.assertTrue(cns.corner_active(self.state))
        self.write_switch(active={"adapter": "soren"})
        self.assertTrue(cns.corner_active(self.state))

    def test_missing_or_invalid_game_switch_is_clear(self):
        self.assertFalse(cns.corner_active(self.state))
        path = self.state / "game_switch.json"
        for payload in ("{", "[]", json.dumps({"phase": "future-phase"}),
                        json.dumps({"active": {"game": "paper-view"}})):
            path.write_text(payload, encoding="utf-8")
            self.assertFalse(cns.corner_active(self.state), payload)
            self.assertFalse(cns.prediction_blocked(self.state, now=self.now), payload)

    def test_game_switch_symlink_and_oversize_are_clear(self):
        target = self.root / "outside.json"
        target.write_text(json.dumps({
            "phase": "ready", "active": {"game": "paper-view"},
        }), encoding="utf-8")
        link = self.state / "game_switch.json"
        link.symlink_to(target)
        self.assertFalse(cns.corner_active(self.state))
        link.unlink()
        link.write_text("x" * (cns.GAME_SWITCH_LIMIT + 1), encoding="utf-8")
        self.assertFalse(cns.corner_active(self.state))

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
        self.write_switch(active={"game": "paper-view", "adapter": "paper"})
        result = subprocess.run(
            [sys.executable, str(script), "corner"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "1")
        result = subprocess.run(
            [sys.executable, str(script), "blocked", "prediction"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "1")
        self.write_switch()
        result = subprocess.run(
            [sys.executable, str(script), "corner"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "0")
        self.write_rotation(pending={"corner": "paper", "phase": "selected"})
        result = subprocess.run(
            [sys.executable, str(script), "corner"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
