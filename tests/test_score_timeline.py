import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import status_dashboard as sd


class ScoreTimelineTest(unittest.TestCase):
    def test_timeline_preserves_y_scale_and_time_direction(self):
        rows = sd.render_score_timeline(list(range(100)), chart_w=20, chart_h=7)
        plain = [sd.ANSI_RE.sub("", row) for row in rows]

        self.assertEqual(len(plain), 10)
        self.assertFalse(any("\u2800" <= ch <= "\u28ff" for ch in "\n".join(plain)))
        plot = plain[1:8]
        self.assertTrue(all(len(row.split("│", 1)[1]) == 20 for row in plot))
        self.assertIn("*", "\n".join(plot))
        self.assertIn("/", "\n".join(plot))
        self.assertIn("old", plain[-1])
        self.assertIn("now", plain[-1])

    def test_timeline_keeps_extreme_labels(self):
        rows = sd.render_score_timeline([100, 250, 400], chart_w=12, chart_h=4)
        plain = "\n".join(sd.ANSI_RE.sub("", row) for row in rows)
        self.assertIn("  400", plain)
        self.assertIn("  100", plain)

    def test_short_history_still_shows_placeholder(self):
        rows = sd.render_score_timeline([100, 200], chart_w=12, chart_h=4)
        plain = "\n".join(sd.ANSI_RE.sub("", row) for row in rows)
        self.assertIn("not enough data", plain)


if __name__ == "__main__":
    unittest.main()
