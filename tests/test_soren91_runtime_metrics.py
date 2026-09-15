from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("soren91_runtime_metrics", ROOT / "soren91" / "runtime_metrics.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RuntimeMetricsTest(unittest.TestCase):
    def test_decision_intervals_hold_and_observation_tags(self):
        state = MODULE.MetricsState(started_at=100.0)
        self.assertTrue(state.record("[game] Turn 0: state=MOVE, pieces=0, rank=?, conf=0.8, reason=stable\n", now=100.0))
        self.assertTrue(state.record("[game] Decision: x=0.00, reason=structured-stack; risk=0\n", now=100.2))
        self.assertTrue(state.record("[game] Turn 1: state=MOVE, pieces=1, rank=?, conf=0.8, reason=stable-slow-advance-temporal-next-hold-empty\n", now=102.5))
        self.assertTrue(state.record("[game] Decision: x=1.00, reason=HOLD: structured-stack; risk=0 [HOLD]\n", now=102.7))
        payload = state.payload(generated_at=103.0)
        self.assertEqual(payload["decisions"], 2)
        self.assertEqual(payload["hold_decisions"], 1)
        self.assertEqual(payload["temporal_next_observations"], 1)
        self.assertEqual(payload["hold_empty_observations"], 1)
        self.assertEqual(payload["observation_reasons"]["stable-slow-advance"], 1)
        self.assertEqual(payload["decision_interval_ms"]["samples"], 1)
        self.assertEqual(payload["decision_interval_ms"]["p50"], 2500)

    def test_summary_resets_cross_round_interval_and_bounds_rank(self):
        state = MODULE.MetricsState(started_at=0)
        state.record("[game] Decision: x=0.00, reason=structured-stack\n", now=1.0)
        state.record("[game] Summary: turns=18, rank=42, hash=abc\n", now=2.0)
        state.record("[game] Decision: x=0.00, reason=structured-stack\n", now=100.0)
        payload = state.payload(generated_at=101.0)
        self.assertEqual(payload["games_completed"], 1)
        self.assertEqual(payload["early_games"], 1)
        self.assertEqual(payload["rank_median"], 42)
        self.assertEqual(payload["last_turns"], 18)
        self.assertEqual(payload["decision_interval_ms"]["samples"], 0)

    def test_atomic_output_is_numeric_and_contains_no_raw_line(self):
        state = MODULE.MetricsState(started_at=1)
        secretish = "viewer-name-do-not-copy"
        state.record(f"[game] Decision: x=0.00, reason=structured-stack {secretish}\n", now=2)
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "metrics.json"
            MODULE.atomic_write(path, state.payload(generated_at=3))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(secretish, raw)
            data = json.loads(raw)
            self.assertEqual(data["schema"], 1)
            self.assertEqual(data["decisions"], 1)
            self.assertLess(len(raw), 4096)


if __name__ == "__main__":
    unittest.main()
