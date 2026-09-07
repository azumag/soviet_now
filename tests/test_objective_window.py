import pathlib
import unittest

SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "strategy/regression.sh").read_text()
start = SOURCE.index("def objective_progress(data, scores):")
end = SOURCE.index("\ndef gap(", start)
ns = {}
exec(SOURCE[start:end], ns)

class ObjectiveWindowTest(unittest.TestCase):
    def test_historical_count_is_not_window_count(self):
        result = ns["objective_progress"]({"max_types": [13, 15, 14], "soviet_count": 8, "best_max_type": 16}, [1]*100)
        self.assertEqual(result["soviet_count"], 0)
        self.assertEqual(result["best_max_type"], 16)

    def test_counts_observed_soviet_games_within_score_window(self):
        result = ns["objective_progress"]({"max_types": [16, 16, 14], "soviet_count": 8}, [1, 2])
        self.assertEqual(result["soviet_count"], 1)

if __name__ == "__main__":
    unittest.main()
