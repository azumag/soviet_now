"""Tests for lib/instadeath_monitor.py (Phase 1 of soren-stat-gate-design.md).

Pure-Python, no bash/subprocess involved -- these exercise the monitor's
read-modify-write logic directly. Integration with update_rolling_scores()/
_update_current_strategy_run() (the bash call sites) is covered separately
in tests/test_instadeath_split.py.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import instadeath_monitor as im  # noqa: E402


def _rec(h="h1", s=10000, raw=1000, turns=200, d=False, archive=""):
    return {"h": h, "s": s, "raw": raw, "turns": turns, "d": d, "archive": archive}


class MonitorBasicsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, "instadeath_monitor.json")

    def tearDown(self):
        self.td.cleanup()

    def test_1_missing_file_first_observe_creates_valid_monitor(self):
        result = im.observe(self.path, _rec())
        self.assertEqual(result["status"], "updated")
        self.assertTrue(os.path.exists(self.path))
        data = json.load(open(self.path))
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["window"]), 1)
        self.assertNotIn("error", result)

    def test_2_window_capped_at_dead_monitor_window(self):
        cfg = {"dead_monitor_window": 5, "dead_quarantine_window": 3}
        for i in range(10):
            im.observe(self.path, _rec(s=10000 + i, archive=f"a{i}"), cfg)
        data = json.load(open(self.path))
        self.assertEqual(len(data["window"]), 5)
        self.assertEqual(data["window"][0]["s"], 10005)
        self.assertEqual(data["window"][-1]["s"], 10009)

    def test_3_dedup_same_archive_leaves_window_unchanged(self):
        cfg = {"dead_quarantine_window": 3}
        im.observe(self.path, _rec(archive="game_a.jsonl"), cfg)
        before = json.load(open(self.path))
        result = im.observe(self.path, _rec(s=99999, archive="game_a.jsonl"), cfg)
        self.assertEqual(result["status"], "dedup")
        after = json.load(open(self.path))
        self.assertEqual(before["window"], after["window"])
        self.assertEqual(after["counters"]["skipped_dedup"], 1)

    def test_4_by_hash_derived_and_shrinks_with_window(self):
        cfg = {"dead_monitor_window": 3, "dead_quarantine_window": 3}
        im.observe(self.path, _rec(h="old_hash", archive="a0"), cfg)
        for i in range(1, 4):
            im.observe(self.path, _rec(h="new_hash", archive=f"a{i}"), cfg)
        data = json.load(open(self.path))
        # window cap is 3, so old_hash's single record fell out
        self.assertNotIn("old_hash", data["by_hash"])
        self.assertIn("new_hash", data["by_hash"])
        self.assertEqual(data["by_hash"]["new_hash"]["n"], 3)

    def test_5_spans_hash_change_true_and_false(self):
        cfg = {"dead_quarantine_window": 3}
        for i in range(3):
            im.observe(self.path, _rec(h="hA", d=True, archive=f"a{i}"), cfg)
        data = json.load(open(self.path))
        self.assertFalse(data["runs"]["spans_hash_change"])

        im.observe(self.path, _rec(h="hB", d=True, archive="a3"), cfg)
        data = json.load(open(self.path))
        self.assertTrue(data["runs"]["spans_hash_change"])

    def test_6_cold_start_single_dead_does_not_quarantine(self):
        # R6: monitor doesn't exist yet, first observation is a total
        # instadeath (score=0, raw=0, turns=0). Must NOT quarantine on n=1.
        cfg = {"dead_quarantine_window": 20}
        result = im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True), cfg)
        self.assertEqual(result["evaluated"], False)
        self.assertEqual(result["verdict"], "INSUFFICIENT_WINDOW")
        self.assertEqual(result["quarantine_active"], 0)
        data = json.load(open(self.path))
        self.assertFalse(data["quarantine"]["active"])

    def test_7_sustained_outage_triggers_harness_quarantine(self):
        cfg = {"dead_quarantine_window": 20, "dead_quarantine_enabled": True}
        result = None
        for i in range(20):
            result = im.observe(
                self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        self.assertEqual(result["evaluated"], True)
        self.assertEqual(result["verdict"], "HARNESS")
        self.assertEqual(result["quarantine_active"], 1)
        self.assertEqual(result["transition"], "start")
        data = json.load(open(self.path))
        self.assertIsNotNone(data["quarantine"]["started_at"])
        self.assertEqual(data["quarantine"]["history"][-1]["event"], "start")

    def test_8_recovery_clears_quarantine(self):
        cfg = {"dead_quarantine_window": 20, "dead_quarantine_clear_window": 20,
               "dead_quarantine_clear_rate": 0.05}
        for i in range(20):
            im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        result = None
        for i in range(20):
            result = im.observe(
                self.path, _rec(s=10000, raw=1000, turns=200, d=False, archive=f"b{i}"), cfg)
        self.assertEqual(result["quarantine_active"], 0)
        self.assertEqual(result["transition"], "clear")
        data = json.load(open(self.path))
        self.assertIsNotNone(data["quarantine"]["cleared_at"])
        self.assertEqual(data["quarantine"]["history"][-1]["event"], "clear")

    def test_9_quarantine_disabled_records_verdict_but_stays_inactive(self):
        cfg = {"dead_quarantine_window": 20, "dead_quarantine_enabled": False}
        result = None
        for i in range(20):
            result = im.observe(
                self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        self.assertEqual(result["verdict"], "HARNESS")
        self.assertEqual(result["quarantine_active"], 0)
        self.assertEqual(result["transition"], "")

    def test_10_scattered_low_severity_deaths_stay_unknown(self):
        cfg = {"dead_quarantine_window": 20, "dead_alert_rate": 0.10}
        result = None
        for i in range(20):
            dead = (i % 3 == 0)  # ~35% rate, scattered (not clustered)
            result = im.observe(
                self.path,
                _rec(s=(0 if dead else 10000), raw=(500 if dead else 1000),
                     turns=(150 if dead else 200), d=dead, archive=f"a{i}"),
                cfg)
        self.assertIn(result["verdict"], ("UNKNOWN", "NORMAL"))
        self.assertEqual(result["quarantine_active"], 0)

    def test_11_corrupt_json_self_heals(self):
        with open(self.path, "w") as f:
            f.write("{not valid json")
        result = im.observe(self.path, _rec(), {"dead_quarantine_window": 3})
        self.assertEqual(result["status"], "updated")
        data = json.load(open(self.path))
        self.assertEqual(len(data["window"]), 1)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_12_missing_raw_and_turns_do_not_crash(self):
        cfg = {"dead_quarantine_window": 20}
        result = None
        for i in range(20):
            result = im.observe(
                self.path, _rec(s=0, raw=None, turns=None, d=True, archive=f"a{i}"), cfg)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["evaluated"], True)
        # No exception, and with both raw/turns unknown, only burst_ratio +
        # near_total_rate can vote (hard_ratio/median_turns detectors are
        # None -> no vote) -- still enough to reach HARNESS for a full
        # 20/20 outage, but must not have thrown getting there.
        data = json.load(open(self.path))
        self.assertIn(data["quarantine"]["verdict"], ("HARNESS", "UNKNOWN"))

    def test_13_no_double_transition_in_a_single_tick(self):
        # Build up to just-cleared state, then immediately feed another dead
        # game that alone wouldn't yet re-trigger (window still mostly
        # alive) -- must not start+clear inconsistently in one call.
        cfg = {"dead_quarantine_window": 5, "dead_quarantine_clear_window": 5,
               "dead_quarantine_clear_rate": 0.05}
        for i in range(5):
            im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        for i in range(5):
            im.observe(self.path, _rec(d=False, archive=f"b{i}"), cfg)
        data = json.load(open(self.path))
        self.assertFalse(data["quarantine"]["active"])
        # one more alive game: no transition, no exception
        result = im.observe(self.path, _rec(d=False, archive="c0"), cfg)
        self.assertEqual(result["transition"], "")


class MonitorStateTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, "instadeath_monitor.json")

    def tearDown(self):
        self.td.cleanup()

    def test_state_on_missing_file(self):
        result = im.state(self.path)
        self.assertEqual(result["quarantine_active"], 0)
        self.assertFalse(os.path.exists(self.path))  # state() never creates the file

    def test_state_reflects_active_quarantine(self):
        cfg = {"dead_quarantine_window": 5}
        for i in range(5):
            im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        result = im.state(self.path)
        self.assertEqual(result["quarantine_active"], 1)
        self.assertEqual(result["verdict"], "HARNESS")

    def test_note_diverted_increments_counter(self):
        im.observe(self.path, _rec(archive="a0"))
        im.note_diverted(self.path)
        im.note_diverted(self.path)
        data = json.load(open(self.path))
        self.assertEqual(data["quarantine"]["diverted_total"], 2)


if __name__ == "__main__":
    unittest.main()
