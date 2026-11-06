"""An aged sortie is unconfirmed, not automatically finished (2026-11-06)."""
import unittest

from lib.docich_corner_stats import _hanjuku_moves
from status_dashboard import render_hanjuku_status


def policy(age, status="en_route"):
    return {
        "tick": 2000,
        "sorties": {
            "6-A": {
                "general": "ナストリーニ", "source": "アルマムーン",
                "target": "セレーネ", "tick": 2000 - age,
                "status": status,
            }
        },
    }


class MarchChartEvidenceTest(unittest.TestCase):
    def test_400_observations_does_not_remove_march(self):
        self.assertEqual(_hanjuku_moves(policy(399), 2000)[0]["status"], "en_route")
        rows = _hanjuku_moves(policy(400), 2000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "tracking_unknown")
        self.assertEqual(rows[0]["target"], "セレーネ")
        self.assertEqual(_hanjuku_moves(policy(1500), 2000)[0]["status"], "tracking_unknown")

    def test_overdue_does_not_claim_battle(self):
        rows = _hanjuku_moves(policy(80, "overdue"), 2000)
        self.assertEqual(rows[0]["status"], "tracking_unknown")
        value = {
            "gap": {
                "left": 0, "width": 239, "side": "left", "until": 10000.0,
                "moves": rows,
            }
        }
        output = "\n".join(render_hanjuku_status(value))
        self.assertIn("到着未確認（追跡期限超過）", output)
        self.assertNotIn("交戦中", output)

    def test_observed_terminal_status_is_not_a_march(self):
        self.assertEqual(_hanjuku_moves(policy(20, "arrived"), 2000), [])
        self.assertEqual(_hanjuku_moves(policy(20, "cancelled"), 2000), [])

    def test_future_tick_is_not_treated_as_live(self):
        self.assertEqual(_hanjuku_moves(policy(-1), 2000), [])


if __name__ == "__main__":
    unittest.main()
