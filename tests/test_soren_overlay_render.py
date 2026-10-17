"""Contracts for the long-lived unified overlay renderer (azumag/docich#970).

`generate_soren_overlay.sh watch` used to spawn a fresh `python3 -` interpreter
(plus a `sleep` child) on every refresh tick. The rendering code moved verbatim
into `lib/soren_overlay_render.py`; these tests pin the parts that make the
long-lived loop safe:

* `show_status_once` keeps the reviewed producer invocation and env, and is
  fail-open like the old `2>/dev/null || true`,
* `serve` renders on a fixed-delay cadence and keeps running through errors,
* `render` still writes the unified + legacy OPS/STATS files with the same
  refresh meta tag and raw-`<pre>` data contract.
"""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class SorenOverlayRenderTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("lib.soren_overlay_render")

    def test_show_status_once_keeps_the_reviewed_producer_contract(self):
        calls = {}

        class Proc:
            stdout = "OPS\n"

        def fake_run(args, **kwargs):
            calls["args"] = args
            calls["env"] = kwargs.get("env") or {}
            return Proc()

        with mock.patch.object(self.module.subprocess, "run", side_effect=fake_run):
            out = self.module.show_status_once()
        self.assertEqual(out, "OPS\n")
        self.assertEqual(calls["args"], ["./show_status.sh", "--once"])
        self.assertEqual(calls["env"]["SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH"], "1")
        self.assertEqual(calls["env"]["SHOW_STATUS_NO_FLICKER"], "1")

    def test_show_status_once_is_fail_open(self):
        with mock.patch.object(self.module.subprocess, "run", side_effect=OSError("boom")):
            self.assertEqual(self.module.show_status_once(), "")

    def test_serve_renders_every_tick_with_fixed_delay(self):
        ticks = []
        sleeps = []

        def fake_render(**kwargs):
            ticks.append(kwargs)
            if len(ticks) >= 3:
                raise KeyboardInterrupt

        with mock.patch.object(self.module, "render_from_show_status", side_effect=fake_render), \
                mock.patch.object(self.module.time, "sleep", side_effect=lambda s: sleeps.append(s)):
            with self.assertRaises(KeyboardInterrupt):
                self.module.serve(
                    2, out_file="/tmp/x.html", width="1", height="1",
                    event_events_file="e", event_html_file="h",
                    event_keep="1", event_visible_sec="1", event_work_state_file="w",
                )
        self.assertEqual(len(ticks), 3)
        self.assertEqual(sleeps, [2, 2])

    def test_serve_survives_a_render_error(self):
        calls = []

        def boom(**kwargs):
            calls.append(1)
            if len(calls) >= 2:
                raise KeyboardInterrupt
            raise RuntimeError("render failed")

        with mock.patch.object(self.module, "render_from_show_status", side_effect=boom), \
                mock.patch.object(self.module.time, "sleep", side_effect=lambda s: None):
            with self.assertRaises(KeyboardInterrupt):
                self.module.serve(2, out_file="o", width="1", height="1",
                                  event_events_file="e", event_html_file="h",
                                  event_keep="1", event_visible_sec="1",
                                  event_work_state_file="w")
        self.assertEqual(len(calls), 2)

    def test_render_writes_unified_and_legacy_files(self):
        fake_dashboard = types.ModuleType("status_dashboard")
        fake_dashboard.render_dashboard_text = lambda: "STATS"
        fake_event = types.ModuleType("generate_event_overlay")
        fake_event.render_event_overlay = lambda *a, **k: None
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / "unified.html"
            env = {
                "SHOW_STATUS_OVERLAY_HTML_FILE": str(tmp / "ops.html"),
                "STATUS_OVERLAY_HTML_FILE": str(tmp / "stats.html"),
            }
            with mock.patch.dict(sys.modules, {"status_dashboard": fake_dashboard,
                                               "generate_event_overlay": fake_event}), \
                    mock.patch.dict(self.module.os.environ, env, clear=False), \
                    mock.patch.object(self.module, "refresh_viewer_chat_monitor_if_changed",
                                      lambda *a, **k: None):
                self.module.render(
                    "\x1b[32mOK\x1b[0m", str(out), "1120", "980",
                    str(tmp / "ev.jsonl"), str(tmp / "event.html"), "180", "18",
                    str(tmp / "work.json"),
                )
            html = out.read_text(encoding="utf-8")
            self.assertIn('http-equiv="refresh" content="2"', html)
            self.assertIn("SOREN CONTROL DECK", html)
            self.assertIn("color:#22c55e", html)
            self.assertTrue((tmp / "ops.html").is_file())
            self.assertTrue((tmp / "stats.html").is_file())

    def test_direct_script_invocation_keeps_checkout_root_importable(self):
        # The watcher runs `python3 lib/soren_overlay_render.py`, which would
        # otherwise drop the checkout root from sys.path.
        text = (ROOT / "lib" / "soren_overlay_render.py").read_text(encoding="utf-8")
        self.assertIn("_CHECKOUT_ROOT", text)
        self.assertIn("sys.path.insert(0, _CHECKOUT_ROOT)", text)


if __name__ == "__main__":
    unittest.main()
