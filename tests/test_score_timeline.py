import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import status_dashboard as sd


class ScoreTimelineTest(unittest.TestCase):
    def test_timeline_connects_samples_instead_of_filling_every_column(self):
        rows = sd.render_score_timeline(list(range(100)), chart_w=20, chart_h=7)
        plain = [sd.ANSI_RE.sub("", row) for row in rows]

        # The bottom row is the low-score baseline. A connected rising line
        # should leave most columns empty; the old area-fill renderer occupied
        # every column because each sample painted down to the baseline.
        glyphs = plain[-1][-20:]
        occupied = sum(
            bool((ord(glyph) - sd.BRAILLE_BASE) & 0xC0)
            for glyph in glyphs
        )
        self.assertLess(occupied, len(glyphs))

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
