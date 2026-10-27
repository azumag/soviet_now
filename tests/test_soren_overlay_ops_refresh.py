"""Contracts for the throttled OPS producer (azumag/docich#970 follow-up).

``show_status.sh --once`` is the heaviest per-tick path in the unified overlay
worker. The renderer still renders the event/chat/STATS surfaces every tick,
but reuses the OPS text for ``SOREN_OPS_REFRESH_SEC`` seconds. These tests pin
the cache semantics and the supervised worker's default without running the
real producer or a live overlay.
"""

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OpsRefreshTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("lib.soren_overlay_render")

    def test_invalid_or_unsafe_values_fall_back_to_every_tick(self):
        for raw in ("", "abc", "-1", "nan", "inf", "-inf"):
            with mock.patch.dict(os.environ, {"SOREN_OPS_REFRESH_SEC": raw}):
                self.assertEqual(self.module._ops_refresh_seconds(), 0.0, raw)

    def test_positive_value_is_used(self):
        with mock.patch.dict(os.environ, {"SOREN_OPS_REFRESH_SEC": "10"}):
            self.assertEqual(self.module._ops_refresh_seconds(), 10.0)

    def test_zero_refresh_calls_the_producer_every_tick(self):
        calls = []
        cache = self.module.OpsCache(0)

        def producer():
            calls.append(1)
            return f"OPS{len(calls)}"

        self.assertEqual(cache.get(0.0, producer), "OPS1")
        self.assertEqual(cache.get(1.0, producer), "OPS2")
        self.assertEqual(cache.get(2.0, producer), "OPS3")
        self.assertEqual(len(calls), 3)

    def test_interval_reuses_one_output_until_due(self):
        calls = []
        cache = self.module.OpsCache(10)

        def producer():
            calls.append(1)
            return f"OPS{len(calls)}"

        self.assertEqual(cache.get(0.0, producer), "OPS1")
        self.assertEqual(cache.get(9.99, producer), "OPS1")
        self.assertEqual(cache.get(10.0, producer), "OPS2")
        self.assertEqual(cache.get(19.99, producer), "OPS2")
        self.assertEqual(len(calls), 2)

    def test_empty_result_retries_and_keeps_the_last_good_text(self):
        results = iter(["OPS1", "", "", "OPS2"])
        calls = []
        cache = self.module.OpsCache(10)

        def producer():
            calls.append(1)
            return next(results)

        self.assertEqual(cache.get(0.0, producer), "OPS1")
        self.assertEqual(cache.get(9.0, producer), "OPS1")
        # A failed producer is retried on the next tick instead of being frozen
        # for the whole interval; the previous good text stays visible.
        self.assertEqual(cache.get(10.0, producer), "OPS1")
        self.assertEqual(cache.get(11.0, producer), "OPS1")
        self.assertEqual(cache.get(12.0, producer), "OPS2")
        self.assertEqual(len(calls), 4)

    def test_first_empty_result_is_not_cached_for_the_interval(self):
        calls = []
        results = iter(["", "OPS1"])
        cache = self.module.OpsCache(10)

        def producer():
            calls.append(1)
            return next(results)

        self.assertEqual(cache.get(0.0, producer), "")
        self.assertEqual(cache.get(1.0, producer), "OPS1")
        self.assertEqual(len(calls), 2)

    def test_serve_reuses_ops_within_the_interval(self):
        renders = []
        clock = iter(range(100))

        def fake_render(**kwargs):
            renders.append(kwargs["ops_raw"])
            if len(renders) >= 3:
                raise KeyboardInterrupt

        with mock.patch.dict(os.environ, {
                "SOREN_OPS_REFRESH_SEC": "10",
                "SOREN_OVERLAY_PARENT_PID": "",
        }), \
                mock.patch.object(self.module, "show_status_once", return_value="OPS") as producer, \
                mock.patch.object(self.module, "render", side_effect=fake_render), \
                mock.patch.object(self.module.time, "monotonic",
                                  side_effect=lambda: next(clock)), \
                mock.patch.object(self.module.time, "sleep", side_effect=lambda s: None), \
                mock.patch.object(self.module.os, "getppid", return_value=1):
            with self.assertRaises(KeyboardInterrupt):
                self.module.serve(2, out_file="o")
        self.assertEqual(renders, ["OPS", "OPS", "OPS"])
        self.assertEqual(producer.call_count, 1)

    def test_wrapper_defaults_to_ten_seconds(self):
        text = (ROOT / "generate_soren_overlay.sh").read_text(encoding="utf-8")
        self.assertIn('SOREN_OPS_REFRESH_SEC="${SOREN_OPS_REFRESH_SEC:-10}"', text)
        self.assertIn("export SOREN_OPS_REFRESH_SEC", text)


if __name__ == "__main__":
    unittest.main()
