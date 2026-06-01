import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard_data


class DashboardDataTest(unittest.TestCase):
    def test_eval_score_history_is_reported_separately_from_raw_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("score_history.txt").write_text(
                    "2026-05-21T00:00:00+09:00\t100\n2026-05-21T00:01:00+09:00\t300\n",
                    encoding="utf-8",
                )
                Path("eval_score_history.txt").write_text(
                    "2026-05-21T00:00:00+09:00\t1100\n"
                    "2026-05-21T00:01:00+09:00\t3300\n",
                    encoding="utf-8",
                )
                Path("game_count.txt").write_text("2\n", encoding="utf-8")

                data = dashboard_data.build_dashboard_data(chart_games=10)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(data["scoreStats"]["best"], 300)
        self.assertEqual(data["scoreStats"]["average"], 200)
        self.assertEqual([d["score"] for d in data["chartScores"]], [100, 300])
        self.assertEqual([d["score"] for d in data["chartEvalScores"]], [1100, 3300])
        self.assertEqual(data["evalScoreStats"]["best"], 3300)
        self.assertEqual(data["evalScoreStats"]["average"], 2200)
        self.assertEqual(data["evalScoreStats"]["count"], 2)

    def test_current_gate_stats_do_not_fall_back_to_all_history(self):
        rows = [
            {"maxType": 11, "hashes": ["old"]},
            {"maxType": 14, "hashes": ["old"]},
        ]

        stats = dashboard_data.stage_gate_stats_for_hash(rows, "current")

        self.assertEqual(stats["hash"], "current")
        self.assertEqual(stats["window"], 0)
        self.assertIsNone(stats["focus"])

    def test_russia_rate_series_caps_window_at_current_game(self):
        rows = [
            {
                "ts": "2026-06-01T00:00:00+09:00",
                "label": "x",
                "game": 50,
                "score": 1000,
                "turns": 50,
            },
            {
                "ts": "2026-06-01T00:01:00+09:00",
                "label": "x",
                "game": 150,
                "score": 1000,
                "turns": 50,
            },
            {
                "ts": "2026-06-01T00:02:00+09:00",
                "label": "x",
                "game": 199,
                "score": 1000,
                "turns": 50,
            },
            {
                "ts": "2026-06-01T00:03:00+09:00",
                "label": "x",
                "game": 200,
                "score": 1000,
                "turns": 50,
            },
            {
                "ts": "2026-06-01T00:04:00+09:00",
                "label": "x",
                "game": 210,
                "score": 1000,
                "turns": 50,
            },
        ]

        series = dashboard_data.russia_rate_series(
            rows, current_game=200, window=100, max_points=50
        )

        self.assertEqual(series["window"], 100)
        self.assertEqual(series["current"]["game"], 200)
        self.assertEqual(series["current"]["count"], 3)
        self.assertEqual(series["current"]["rate"], 3.0)
        self.assertGreater(len(series["points"]), 0)
        for point in series["points"]:
            self.assertGreaterEqual(point["count"], 0)
            self.assertLessEqual(point["count"], 100)
        for point in series["points"]:
            self.assertLessEqual(point["game"], 200)

    def test_russia_rate_series_returns_empty_when_window_exceeds_history(self):
        rows = [
            {
                "ts": "2026-06-01T00:00:00+09:00",
                "label": "x",
                "game": 10,
                "score": 1000,
                "turns": 50,
            },
        ]

        series = dashboard_data.russia_rate_series(rows, current_game=50, window=100)

        self.assertEqual(series["points"], [])
        self.assertIsNone(series["current"])

    def test_russia_rate_series_handles_no_creation_events(self):
        series = dashboard_data.russia_rate_series([], current_game=200, window=100)

        self.assertEqual(series["points"], [])
        self.assertIsNone(series["current"])


if __name__ == "__main__":
    unittest.main()
