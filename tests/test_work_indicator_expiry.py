import importlib.util
import json
import os
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


class TestWorkIndicatorExpiry(unittest.TestCase):
    def test_keeps_recent_indicator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.json"
            path.write_text(json.dumps({"active": True, "ts": 100, "title": "調査中"}))
            with mock.patch.dict(os.environ, {"CODEX_WORK_OVERLAY_STALE_SEC": "60"}):
                item = MODULE.read_work_indicator(path, now=160)
            self.assertEqual(item["title"], "調査中")
            self.assertTrue(path.exists())

    def test_removes_indicator_after_updates_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.json"
            path.write_text(json.dumps({"active": True, "ts": 100, "title": "調査中"}))
            with mock.patch.dict(os.environ, {"CODEX_WORK_OVERLAY_STALE_SEC": "60"}):
                item = MODULE.read_work_indicator(path, now=161)
            self.assertIsNone(item)
            self.assertFalse(path.exists())

    def test_removes_invalid_timestamp_instead_of_leaving_it_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.json"
            path.write_text(json.dumps({"active": True, "title": "調査中"}))
            self.assertIsNone(MODULE.read_work_indicator(path, now=100))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
