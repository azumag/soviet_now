import unittest

from lib.overlay_text import normalize_overlay_text


class OverlayTextTests(unittest.TestCase):
    def test_surrogate_and_nul_from_runtime_logs_are_replaced(self) -> None:
        normalized = normalize_overlay_text("before\udcffmiddle\x00after")
        normalized.encode("utf-8")
        self.assertNotIn("\udcff", normalized)
        self.assertNotIn("\x00", normalized)
        self.assertIn("before", normalized)
        self.assertIn("after", normalized)


if __name__ == "__main__":
    unittest.main()
