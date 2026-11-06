"""Regression contracts for Hanjuku's bounded, non-rotating news strip."""
import re
import unittest
from pathlib import Path


OVERLAY = (Path(__file__).resolve().parents[1]
           / "overlays" / "direct_broadcast_overlay.html")


class HanjukuGapFixedNewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = OVERLAY.read_text(encoding="utf-8")
        css = cls.html.split("<style>", 1)[1].split("</style>", 1)[0]
        cls.gap_css = css.split("/* The news strip has a fixed height.", 1)[1]

    def test_news_has_fixed_height_and_hides_overflow(self):
        self.assertRegex(self.gap_css, r"\.hanjuku-gap-news \{[^}]*height: 210px;[^}]*overflow: hidden")
        self.assertRegex(self.gap_css, r"\.hanjuku-gap-decisions \{[^}]*overflow: hidden")
        self.assertRegex(self.gap_css, r"\.hanjuku-gap-toasts \{[^}]*overflow: hidden")
        self.assertNotIn("overflow-y: auto", self.gap_css)
        self.assertNotIn("text-overflow: ellipsis", self.gap_css)

    def test_news_does_not_scale_or_rotate_history(self):
        self.assertIn("gapDom.news.append(gapDom.decisionBox, gapDom.toastBox)", self.html)
        self.assertIn("gapDom.content.append(gapDom.news, gapDom.progress, gapDom.flow)", self.html)
        self.assertIn("while (gapDom.decisionBox.children.length > 4)", self.html)
        self.assertIn("item.textContent = fullText;", self.html)
        self.assertNotIn("fitHanjukuGap(", self.html)
        self.assertNotIn("hanjuku-gap-page-info", self.html)
        self.assertNotIn("hanjuku-gap-decision-chunk", self.html)
        self.assertNotIn("pagingStartedAt", self.html)
        self.assertNotIn("content.style.transform", self.html)

    def test_chart_remains_own_non_shrinking_region(self):
        self.assertRegex(self.gap_css, r"\.hanjuku-gap-content \{[^}]*display: flex;")
        self.assertRegex(self.gap_css, r"\.hanjuku-gap-flow \{[^}]*flex: 1 1 auto;")
        self.assertIn("if (sig === gapDom.flowSig) return;", self.html)
        self.assertIn("overflow-wrap: anywhere;", self.gap_css)


if __name__ == "__main__":
    unittest.main()
