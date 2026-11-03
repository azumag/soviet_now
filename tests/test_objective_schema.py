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
    def test_schema_bumped_for_terminal_success_semantics(self):
        self.assertEqual(OBJECTIVE_SCHEMA_VERSION, "soviet-terminal-v2")

    def test_type_table_is_unchanged(self):
        self.assertEqual(TYPE_BONUS, EXPECTED_TABLE)

    def test_soviet_bonus_exceeds_double_russia_frontier(self):
        self.assertGreater(SOVIET_CREATED_BONUS, 2 * TYPE_BONUS[15])

    def test_founding_has_decisive_margin_over_two_russias(self):
        before = eval_score(100, [15, 15, 7, 3], False)
        after = eval_score(236, [16, 7, 3], True)
        self.assertGreater(after, before)
        self.assertGreaterEqual(after - before, 25000)

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

    def test_version_stamp_separates_v2_from_legacy(self):
        current = stamp({"score": 2})
        legacy = {"score": 1}
        self.assertTrue(is_current(current))
        self.assertEqual(version_of(current), OBJECTIVE_SCHEMA_VERSION)
        self.assertIsNone(version_of(legacy))
        self.assertEqual(len(mixed_versions([legacy, current])), 2)


if __name__ == "__main__":
    unittest.main()
