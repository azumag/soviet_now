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
                    "2026-05-21T00:00:00+09:00\t100\n"
                    "2026-05-21T00:01:00+09:00\t300\n",
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


if __name__ == "__main__":
    unittest.main()
