import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import status_dashboard as sd
from lib.docich_corner_text_loader import load_docich_corner_text


VALID = """def render_docich_corner_stats(corner):
    return ["DOCICH-STUB"]
"""

INVALID = """def something_else(corner):
    return []
"""


class DocichCornerTextLoaderTest(unittest.TestCase):
    def test_missing_checkout_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(load_docich_corner_text(directory))

    def test_valid_module_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "src" / "docich" / "soren_corner_text.py"
            path.parent.mkdir(parents=True)
            path.write_text(VALID, encoding="utf-8")
            module = load_docich_corner_text(directory)
            self.assertIsNotNone(module)
            self.assertEqual(module.render_docich_corner_stats({}), ["DOCICH-STUB"])

    def test_contract_mismatch_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "src" / "docich" / "soren_corner_text.py"
            path.parent.mkdir(parents=True)
            path.write_text(INVALID, encoding="utf-8")
            self.assertIsNone(load_docich_corner_text(directory))

    def test_render_dashboard_uses_docich_when_loaded(self):
        corner = {"kind": "soren91", "label": "SOREN91", "status": "active", "game": "soren91"}
        stub = type("Stub", (), {"render_docich_corner_stats": lambda self, value: ["DOCICH-STUB"]})()
        with patch.object(sd, "_docich_corner_text", stub), patch.object(
            sd, "load_active_corner", return_value=corner
        ), patch.object(sd, "render_ai_backoff_header", return_value=[]), patch.object(
            sd.os, "chdir"
        ):
            output = sd.render_dashboard_text()
        self.assertIn("DOCICH-STUB", output)

    def test_render_dashboard_falls_back_without_docich(self):
        corner = {"kind": "soren91", "label": "SOREN91", "status": "active", "game": "soren91"}
        with patch.object(sd, "_docich_corner_text", None), patch.object(
            sd, "load_active_corner", return_value=corner
        ), patch.object(sd, "render_ai_backoff_header", return_value=[]), patch.object(
            sd.os, "chdir"
        ):
            output = sd.render_dashboard_text()
        self.assertIn("SOREN/CORNER: SOREN91", output)


if __name__ == "__main__":
    unittest.main()
