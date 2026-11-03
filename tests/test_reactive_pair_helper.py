import unittest

from strategy_helpers.reactive_pair import blocking_penalty


class ReactivePairBlockingHelperTests(unittest.TestCase):
    def setUp(self):
        self.positions = {"left": (-1.0, 0.5), "right": (1.0, 0.7)}

    def test_three_element_analyzer_pair_is_penalized(self):
        penalty = blocking_penalty(
            [("left", "right", 7)], self.positions, 3, 0.0, 0.8
        )
        self.assertEqual(penalty, 88.8)

    def test_both_pair_ids_define_the_span(self):
        penalty = blocking_penalty(
            [("left", "right", 7)], self.positions, 3, -0.8, 0.8
        )
        self.assertEqual(penalty, 88.8)

    def test_same_type_drop_is_not_penalized(self):
        self.assertEqual(
            blocking_penalty(
                [("left", "right", 7)], self.positions, 7, 0.0, 0.8
            ),
            0.0,
        )

    def test_outside_span_is_not_penalized(self):
        self.assertEqual(
            blocking_penalty(
                [("left", "right", 7)], self.positions, 3, 2.0, 0.8
            ),
            0.0,
        )

    def test_below_pair_is_not_penalized(self):
        self.assertEqual(
            blocking_penalty(
                [("left", "right", 7)], self.positions, 3, 0.0, 0.4
            ),
            0.0,
        )

    def test_malformed_or_missing_pairs_are_ignored(self):
        self.assertEqual(
            blocking_penalty(
                [("left",), ("missing", "right", 7), None],
                self.positions,
                3,
                0.0,
                0.8,
            ),
            0.0,
        )

    def test_penalty_is_capped(self):
        pairs = [("left", "right", n) for n in range(20, 40)]
        self.assertEqual(
            blocking_penalty(pairs, self.positions, 3, 0.0, 0.8), 810.9
        )


if __name__ == "__main__":
    unittest.main()
