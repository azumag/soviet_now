"""Regression contracts for the half-boiled hero's fixed 540px gap overlay.

The gap is noninteractive in the broadcast; hiding overflow or relying on an
internal scrollbar can silently lose decisions and progress lines.
"""
import re
import unittest
from pathlib import Path


OVERLAY = (Path(__file__).resolve().parents[1]
           / "overlays" / "direct_broadcast_overlay.html")


class HanjukuGapFullVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = OVERLAY.read_text(encoding="utf-8")
        css = cls.html.split("<style>", 1)[1].split("</style>", 1)[0]
        cls.gap_css = css.split("/* Hanjuku gap: every retained", 1)[1]

    def test_gap_blocks_have_no_scrollbars_or_fixed_height_caps(self):
        # The entire rendered column, not independent parts, must be fitted.
        self.assertNotIn("overflow-y: auto", self.gap_css)
        self.assertNotIn("max-height:", self.gap_css)
        self.assertNotIn("text-overflow: ellipsis", self.gap_css)
        self.assertIn(".hanjuku-gap-content { width: 100%; transform-origin: top left; }", self.gap_css)
        self.assertRegex(self.gap_css, r"\.hanjuku-gap-progress \{[^}]*overflow-wrap: anywhere")

    def test_fitter_measures_complete_unscaled_content_and_available_space(self):
        # A transform preserves intrinsic scrollHeight on the next poll, so
        # shrinking does not make the content falsely appear to fit at 1x.
        self.assertIn("content.scrollHeight", self.html)
        self.assertIn("content.scrollWidth", self.html)
        self.assertIn("surface.clientHeight", self.html)
        self.assertIn("surface.clientWidth", self.html)
        self.assertIn("content.style.transform = scale < 1", self.html)
        self.assertIn("gapDom.content.append(gapDom.progress, gapDom.decisionBox, gapDom.toastBox, gapDom.flow)", self.html)
        self.assertIn("surface.appendChild(gapDom.content)", self.html)
        self.assertIn("gapDom.decisionBox", self.html)
        self.assertIn(".hanjuku-gap-decisions { display: flex;", self.gap_css)

    def test_unchanged_chart_still_refits_changing_notifications(self):
        self.assertIn(
            "if (sig === gapDom.flowSig) { fitHanjukuGap(surface); return; }",
            self.html,
        )
        # The rebuilt chart also triggers a fit.
        self.assertRegex(
            self.html,
            r"flow\.appendChild\(row\);\s*}\s*}\s*fitHanjukuGap\(surface\);",
        )
        self.assertIn("white-space: normal; overflow-wrap: anywhere;", self.gap_css)


if __name__ == "__main__":
    unittest.main()
