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
        # No exception, and this is fully deterministic despite raw/turns
        # being unknown throughout: burst_ratio hits its p>=1.0 maximal case
        # (one run spanning the whole 20-window -> ratio 20.0, past
        # dead_burst_ratio's 3.0) for 1 vote, near_total_rate (rate=1.0 >=
        # 0.90) for a 2nd vote -- hard_ratio/median_turns stay None (no
        # vote, since raw/turns are None) but 2 votes alone reaches HARNESS.
        # This was previously asserted as `in ("HARNESS", "UNKNOWN")`, which
        # would have passed either way and caught nothing.
        data = json.load(open(self.path))
        self.assertEqual(data["quarantine"]["verdict"], "HARNESS")
        self.assertIsNone(data["quarantine"]["detail"].get("hard_ratio"))
        self.assertIsNone(data["quarantine"]["detail"].get("median_death_turns"))

    def test_13_clear_transition_never_falls_through_to_a_set_check(self):
        # _apply_quarantine_transition checks "should we clear?" and returns
        # immediately either way when quarantine is already active -- it
        # never falls through to the "should we start?" branch below in the
        # same call. That branch is only reachable when quarantine was NOT
        # already active at the start of the call, so a single observation
        # can never both start and clear. (Previous version of this test's
        # docstring claimed to feed "another dead game" post-clear but the
        # code fed an alive one, and doubled as an assertion that could not
        # actually distinguish "checked, no transition" from "code path
        # unreachable, so trivially no transition.")
        cfg = {"dead_quarantine_window": 5, "dead_quarantine_clear_window": 5,
               "dead_quarantine_clear_rate": 0.05}
        for i in range(5):
            im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        data = json.load(open(self.path))
        self.assertTrue(data["quarantine"]["active"])  # confirm it actually started

        results = []
        for i in range(5):
            results.append(im.observe(self.path, _rec(d=False, archive=f"b{i}"), cfg))
        # exactly one "clear" transition, on the observation whose trailing
        # 5-window first drops below dead_quarantine_clear_rate
        transitions = [r["transition"] for r in results]
        self.assertEqual(transitions.count("clear"), 1, msg=transitions)
        self.assertEqual(transitions.count("start"), 0, msg=transitions)
        data = json.load(open(self.path))
        self.assertFalse(data["quarantine"]["active"])
        self.assertEqual(data["quarantine"]["verdict"], "NORMAL")  # re-classified fresh, not stale HARNESS

        # one more alive game while already cleared: still no transition
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

    def test_diverted_total_tracks_dead_games_observed_while_active(self):
        # observe() self-tracks diverted_total for every dead game seen
        # while quarantine.active is (post-transition) true -- this is
        # exactly the set of games the bash caller will actually divert,
        # since its own divert decision is "_dead and quarantine_active"
        # read from observe()'s own return value (2026-08-20 Phase 1
        # review, next-best item #1: the standalone note_diverted() API
        # this replaced was never called by anything and always read 0).
        cfg = {"dead_quarantine_window": 5}
        for i in range(5):
            im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"a{i}"), cfg)
        data = json.load(open(self.path))
        # the 5th observation is the one that flips active=True, and it is
        # itself counted (dead=True at the moment active becomes True).
        self.assertEqual(data["quarantine"]["diverted_total"], 1)
        for i in range(2):
            im.observe(self.path, _rec(s=0, raw=0, turns=0, d=True, archive=f"b{i}"), cfg)
        data = json.load(open(self.path))
        self.assertEqual(data["quarantine"]["diverted_total"], 3)
        # an alive game while still active must not increment it
        im.observe(self.path, _rec(d=False, archive="c0"), cfg)
        data = json.load(open(self.path))
        self.assertEqual(data["quarantine"]["diverted_total"], 3)


if __name__ == "__main__":
    unittest.main()
