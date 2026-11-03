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
    def test_schema_bumped_for_type16_terminal_inference(self):
        self.assertEqual(OBJECTIVE_SCHEMA_VERSION, "soviet-terminal-v4")

    def test_type_table_is_unchanged(self):
        self.assertEqual(TYPE_BONUS, EXPECTED_TABLE)

    def test_soviet_bonus_reserves_a_separate_terminal_band(self):
        self.assertGreaterEqual(SOVIET_CREATED_BONUS, 1_000_000_000)
        self.assertGreater(SOVIET_CREATED_BONUS, 2 * TYPE_BONUS[15])

    def test_founding_dominates_extreme_nonterminal_frontier_score(self):
        before = eval_score(10_000_000, [15, 15, 14, 14], False)
        after = eval_score(0, [16], True)
        self.assertGreater(after, before)

    def test_type16_infers_success_when_flag_is_missing(self):
        inferred = eval_score(500, [16, 14, 10, 3], False)
        explicit = eval_score(500, [16, 14, 10, 3], True)
        self.assertEqual(inferred, explicit)
        self.assertGreaterEqual(inferred, SOVIET_CREATED_BONUS)

    def test_type15_does_not_infer_soviet_success(self):
        without_flag = eval_score(500, [15, 15, 14], False)
        with_flag = eval_score(500, [15, 15, 14], True)
        self.assertEqual(with_flag - without_flag, SOVIET_CREATED_BONUS)

    def test_unknown_and_junk_types_are_zero(self):
        self.assertEqual(terminal_bonus([99, None, "bad"], False), SOVIET_CREATED_BONUS)
        self.assertEqual(eval_score("bad", [3], False), TYPE_BONUS[3])

    def test_monotonic_in_tier(self):
        for tier in range(3, 16):
            self.assertGreater(TYPE_BONUS[tier + 1], TYPE_BONUS[tier])

    def test_version_stamp_separates_v4_from_legacy(self):
        current = stamp({"score": 2})
        legacy = {"score": 1}
        self.assertTrue(is_current(current))
        self.assertEqual(version_of(current), OBJECTIVE_SCHEMA_VERSION)
        self.assertIsNone(version_of(legacy))
        self.assertEqual(len(mixed_versions([legacy, current])), 2)


if __name__ == "__main__":
    unittest.main()
