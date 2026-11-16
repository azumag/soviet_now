"""Contract checks for the founding-funnel transition metrics in the improve brief.

The founding policy (prompts/game_theory.md §2) requires conditional transition
rates — each stage measured only over games that reached the previous stage —
instead of plain reach rates. summarize_nation_progress in eloop_improve.sh must
keep tracking them so the improve AI can see where the pipeline breaks.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FoundingFunnelContract(unittest.TestCase):
    def test_brief_emits_conditional_funnel_transitions(self):
        text = (ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        self.assertIn("funnel_transitions", text)
        self.assertIn("条件付き移行率", text)
        for stage in ("T13x2", "T14", "T14x2", "T15", "2nd_mat", "T15x2", "soviet"):
            self.assertIn(f'"{stage}"', text)

    def test_per_game_funnel_fields_tracked(self):
        text = (ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        # Post-first-Russia material must be measured only on rows after the
        # first T15 appears, so the second-Russia pipeline is not confused
        # with the pair that created the first Russia.
        self.assertIn("seen_russia", text)
        self.assertIn("post_russia_peaks", text)
        self.assertIn("post_2nd_material", text)
        self.assertIn("double_russia", text)
        self.assertIn("funnel_stage", text)
        self.assertIn("funnel={g.get('funnel_stage'", text)

    def test_second_material_excludes_first_russia_row(self):
        text = (ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        # seen_russia is only set after the row's counts are recorded into
        # post_russia_peaks, keeping the first-T15 row out of the denominator.
        seen_idx = text.index("if seen_russia:")
        flag_idx = text.index("if any(t >= 15 for t in row_type_counts):")
        self.assertLess(seen_idx, flag_idx)


if __name__ == "__main__":
    unittest.main()
