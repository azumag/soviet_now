import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.objective import (
    OBJECTIVE_SCHEMA_VERSION,
    SOVIET_CREATED_BONUS,
    TYPE_BONUS,
    eval_score,
    is_current,
    mixed_versions,
    stamp,
    terminal_bonus,
    version_of,
)

EXPECTED_TABLE = {
    1: 0, 2: 0, 3: 1, 4: 3, 5: 7, 6: 15, 7: 32, 8: 67, 9: 141, 10: 296,
    11: 622, 12: 1306, 13: 2743, 14: 5760, 15: 12096, 16: 25402,
}


class ObjectiveSchemaTests(unittest.TestCase):
    def test_schema_bumped_for_terminal_band_semantics(self):
        self.assertEqual(OBJECTIVE_SCHEMA_VERSION, "soviet-terminal-v3")

    def test_type_table_is_unchanged(self):
        self.assertEqual(TYPE_BONUS, EXPECTED_TABLE)

    def test_soviet_bonus_reserves_a_separate_terminal_band(self):
        self.assertGreaterEqual(SOVIET_CREATED_BONUS, 1_000_000_000)
        self.assertGreater(SOVIET_CREATED_BONUS, 2 * TYPE_BONUS[15])

    def test_founding_dominates_extreme_nonterminal_frontier_score(self):
        # Deliberately use a non-terminal raw score far above normal observed runs.
        before = eval_score(10_000_000, [15, 15, 14, 14], False)
        after = eval_score(0, [16], True)
        self.assertGreater(after, before)

    def test_success_bonus_dominates_same_board_without_success_flag(self):
        types = [16, 14, 10, 3]
        self.assertEqual(
            eval_score(500, types, True) - eval_score(500, types, False),
            SOVIET_CREATED_BONUS,
        )

    def test_unknown_and_junk_types_are_zero(self):
        self.assertEqual(terminal_bonus([99, None, "bad"], False), 0)
        self.assertEqual(eval_score("bad", [99], False), 0)

    def test_monotonic_in_tier(self):
        for tier in range(3, 16):
            self.assertGreater(TYPE_BONUS[tier + 1], TYPE_BONUS[tier])

    def test_version_stamp_separates_v3_from_legacy(self):
        current = stamp({"score": 2})
        legacy = {"score": 1}
        self.assertTrue(is_current(current))
        self.assertEqual(version_of(current), OBJECTIVE_SCHEMA_VERSION)
        self.assertIsNone(version_of(legacy))
        self.assertEqual(len(mixed_versions([legacy, current])), 2)


if __name__ == "__main__":
    unittest.main()
