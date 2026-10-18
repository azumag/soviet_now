import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_event_overlay", ROOT / "generate_event_overlay.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class EventOverlayRendererTests(unittest.TestCase):
    def test_reusable_renderer_preserves_expiry_indicators_and_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = root / "events.jsonl"
            out = root / "event_overlay.html"
            work = root / "work.json"
            comment = root / "comment_state"
            events.write_text(
                '{"ts":99,"category":"system","title":"fresh-title","body":"fresh-body"}\n'
                '{"ts":1,"category":"system","title":"stale-title","body":"stale-body"}\n',
                encoding="utf-8",
            )
            work.write_text(
                '{"active":true,"ts":90,"title":"work-title","body":"work-body"}',
                encoding="utf-8",
            )
            comment.write_text("generating:comment:98\n", encoding="utf-8")

            env = {
                "EVENT_OVERLAY_STATE_BASE": str(root),
                "EVENT_OVERLAY_COMMENT_GEN_STATE": str(comment),
                "EVENT_OVERLAY_RADIO_STATE": str(root / "missing-radio"),
                "EVENT_OVERLAY_SAY_QUEUE_DIR": str(root / "missing-say"),
                "CODEX_WORK_OVERLAY_STALE_SEC": "3600",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                MODULE.render_event_overlay(
                    events,
                    out,
                    keep=180,
                    visible_sec=18,
                    work_state_path=work,
                    now=100,
                )

            html = out.read_text(encoding="utf-8")
            self.assertIn("fresh-title", html)
            self.assertNotIn("stale-title", html)
            self.assertIn("work-title", html)
            self.assertIn("コメント生成中", html)
            self.assertIn('http-equiv="refresh" content="2"', html)
            self.assertIn("const VISIBLE_SEC = 18;", html)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o644)

    def test_cli_remains_a_thin_wrapper_around_reusable_renderer(self):
        argv = [
            "generate_event_overlay.py",
            "events.jsonl",
            "event_overlay.html",
            "12",
            "7",
            "work.json",
        ]
        with mock.patch.object(MODULE, "render_event_overlay") as render:
            with mock.patch.object(MODULE.sys, "argv", argv):
                MODULE.main()
        render.assert_called_once_with(
            Path("events.jsonl"),
            Path("event_overlay.html"),
            12,
            7,
            Path("work.json"),
        )


if __name__ == "__main__":
    unittest.main()
