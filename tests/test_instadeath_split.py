"""Integration tests for Phase 1 of soren-stat-gate-design.md (section B):
actually invokes update_rolling_scores()/_update_current_strategy_run() as
real bash functions (via subprocess), the way the live game loop does,
rather than testing lib/instadeath_monitor.py in isolation (see
tests/test_instadeath_monitor.py for that).

Kept as a separate file from tests/test_escape_mechanisms.py (already
12,000+ lines) per the Phase 1 review's own recommendation.
"""

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _preamble(td, extra_env=""):
    """Shared bash preamble: source config.sh, redirect every file this
    machinery touches into the tmpdir (so tests never read/write the real
    repo's tmp/state, strategy_versions/, etc.), then source the functions
    under test. `extra_env` is inserted after the file-path overrides so
    callers can add e.g. INSTADEATH_SPLIT_ENABLED=1."""
    return textwrap.dedent(f"""\
        set -e
        source ./core/config.sh
        ROLLING_SCORES_FILE='{td}/rolling_scores.json'
        CURRENT_STRATEGY_RUN_FILE='{td}/current_strategy_run.json'
        DEAD_MONITOR_FILE='{td}/instadeath_monitor.json'
        TMP_STATE_DIR='{td}/tmp_state'
        STRATEGY_HASH_ARCHIVE_DIR='{td}/by_hash'
        STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{td}/by_hash_perm'
        HASH_ARCHIVE_PRUNE_BACKGROUND=0
        STRATEGY_FILE='{td}/nonexistent_strategy.py'
        {extra_env}
        mkdir -p '{td}/tmp_state'
        source ./core/helpers.sh
        source ./strategy/regression.sh
        source ./strategy/improve.sh
        """)


def _run(script, timeout=30):
    return subprocess.run(
        ["bash", "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)


class InstadeathSplitFlagOffTests(unittest.TestCase):
    """#16: with INSTADEATH_SPLIT_ENABLED=0 (the shipped default), both
    functions must be indistinguishable from their pre-Phase-1 behavior."""

    def test_no_progress_key_no_monitor_file_when_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            script = _preamble(td) + textwrap.dedent(f"""\
                ROLLING_SCORE_STRATEGY_HASH=h1 update_rolling_scores 10000 ''
                _update_current_strategy_run h1 10000 ''
                ROLLING_SCORE_STRATEGY_HASH=h1 update_rolling_scores 11000 ''
                _update_current_strategy_run h1 11000 ''
                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))
run = json.load(open('{td}/current_strategy_run.json'))
assert "progress" not in rs["h1"], rs["h1"]
assert "progress" not in run, run
assert "quarantined_scores" not in rs["h1"]
assert "quarantined_scores" not in run
assert rs["h1"]["scores"] == [10000, 11000]
assert run["scores"] == [10000, 11000]
PY
                test ! -f '{td}/instadeath_monitor.json'
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_rolling_log_line_format_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            script = _preamble(td) + """\
                ROLLING_SCORE_STRATEGY_HASH=h1 update_rolling_scores 10000 '' 2>&1 | grep -E '^\\[[0-9:]+\\] \\[ROLLING\\] updated: hash=h1 n=1 total=1 score=10000 file=$'
                _update_current_strategy_run h1 10000 '' 2>&1 | grep -E '^\\[[0-9:]+\\] \\[CURRENT-RUN\\] updated: hash=h1 n=1 total=1 score=10000 file=$'
                """
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitInvariantTests(unittest.TestCase):
    """#17: with the flag on, len(progress) == len(scores) must hold at
    every step, in both files."""

    def test_progress_len_matches_scores_len_across_many_games(self):
        with tempfile.TemporaryDirectory() as td:
            calls = "\n".join(
                f"ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 "
                f"INSTADEATH_RECORD_RAW={900+i} INSTADEATH_RECORD_TURNS={100+i} "
                f"update_rolling_scores {9000 + i * 10} 'g{i}.jsonl'\n"
                f"INSTADEATH_RECORD_RAW={900+i} INSTADEATH_RECORD_TURNS={100+i} "
                f"_update_current_strategy_run h1 {9000 + i * 10} 'g{i}.jsonl'"
                for i in range(15)
            )
            script = _preamble(td, "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=5") + calls + \
                textwrap.dedent(f"""

                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))['h1']
run = json.load(open('{td}/current_strategy_run.json'))
for label, entry in (("rolling", rs), ("current_run", run)):
    scores = entry["scores"]
    progress = entry["progress"]
    assert len(scores) == len(progress), (label, len(scores), len(progress))
    for i, (s, p) in enumerate(zip(scores, progress)):
        assert p is not None, (label, i, "unexpectedly None")
        assert p["s"] == s, (label, i, p["s"], s)
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitLegacyPaddingTests(unittest.TestCase):
    """#18: a hash with scores but no `progress` key (pre-Phase-1 data) must
    left-pad with null on its first Phase-1-era write, not crash or
    silently corrupt."""

    def test_legacy_hash_without_progress_pads_with_null(self):
        with tempfile.TemporaryDirectory() as td:
            rolling_path = Path(td) / "rolling_scores.json"
            rolling_path.write_text(json.dumps({
                "h1": {"scores": list(range(1000, 1030)), "games_total": 30,
                       "_recent_archives": []},
            }))
            script = _preamble(td, "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=5") + \
                textwrap.dedent(f"""\
                ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 \\
                    INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 \\
                    update_rolling_scores 9999 'new.jsonl'
                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))['h1']
assert len(rs["scores"]) == 31, len(rs["scores"])
assert len(rs["progress"]) == 31, len(rs["progress"])
assert rs["progress"][:30] == [None] * 30, rs["progress"][:30]
assert rs["progress"][30]["s"] == 9999, rs["progress"][30]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitKeepTruncationTests(unittest.TestCase):
    """#19: scores/progress truncate together under a small keep window."""

    def test_keep_truncation_stays_aligned(self):
        with tempfile.TemporaryDirectory() as td:
            calls = "\n".join(
                f"ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 "
                f"INSTADEATH_RECORD_RAW={i} INSTADEATH_RECORD_TURNS={i} "
                f"update_rolling_scores {9000 + i} 'g{i}.jsonl'\n"
                f"INSTADEATH_RECORD_RAW={i} INSTADEATH_RECORD_TURNS={i} "
                f"_update_current_strategy_run h1 {9000 + i} 'g{i}.jsonl'"
                for i in range(10)
            )
            script = _preamble(
                td, "INSTADEATH_SPLIT_ENABLED=1\nROLLING_SCORE_KEEP=5\nCURRENT_RUN_SCORE_KEEP=5\n"
                    "DEAD_QUARANTINE_WINDOW=5\nHOT_STREAK_EXTEND_ENABLED=0"
                    # scores are monotonically increasing (9000+i) in this test, which would
                    # otherwise repeatedly trigger hot-streak's wider keep window and mask
                    # truncation entirely -- disable it so this test isolates keep-truncation.
            ) + calls + textwrap.dedent(f"""

                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))['h1']
run = json.load(open('{td}/current_strategy_run.json'))
assert rs["scores"] == [9005, 9006, 9007, 9008, 9009], rs["scores"]
assert len(rs["progress"]) == 5
assert [p["s"] for p in rs["progress"]] == rs["scores"]
assert run["scores"] == [9005, 9006, 9007, 9008, 9009], run["scores"]
assert [p["s"] for p in run["progress"]] == run["scores"]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitDedupTests(unittest.TestCase):
    """#21: replaying the same archive_file is a no-op for scores,
    progress, and the monitor window."""

    def test_dedup_leaves_everything_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            script = _preamble(td, "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=5") + \
                textwrap.dedent(f"""\
                ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 \\
                    INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 \\
                    update_rolling_scores 9000 'dup.jsonl'
                cp '{td}/rolling_scores.json' '{td}/before.json'
                cp '{td}/instadeath_monitor.json' '{td}/monitor_before.json'
                ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 \\
                    INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 \\
                    update_rolling_scores 99999 'dup.jsonl' 2>&1 | grep -q 'duplicate skip'
                python3 - <<'PY'
import json
before = json.load(open('{td}/before.json'))
after = json.load(open('{td}/rolling_scores.json'))
assert before == after, (before, after)
mbefore = json.load(open('{td}/monitor_before.json'))
mafter = json.load(open('{td}/instadeath_monitor.json'))
assert mbefore["window"] == mafter["window"]
assert mafter["counters"]["skipped_dedup"] == 1
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitSymmetryTests(unittest.TestCase):
    """#22 (handoff §13 guard): rolling and current_run must record the
    exact same s/d sequence when fed the exact same game stream."""

    def test_rolling_and_current_run_progress_match(self):
        with tempfile.TemporaryDirectory() as td:
            games = [(9000, False), (0, True), (11000, False), (500, True), (12000, False)]
            calls = []
            for i, (s, dead) in enumerate(games):
                raw = 0 if dead else 900 + i
                turns = 0 if dead else 150 + i
                calls.append(
                    f"ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 "
                    f"INSTADEATH_RECORD_RAW={raw} INSTADEATH_RECORD_TURNS={turns} "
                    f"update_rolling_scores {s} 'g{i}.jsonl'")
                calls.append(
                    f"INSTADEATH_RECORD_RAW={raw} INSTADEATH_RECORD_TURNS={turns} "
                    f"_update_current_strategy_run h1 {s} 'g{i}.jsonl'")
            script = _preamble(td, "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=20") + \
                "\n".join(calls) + textwrap.dedent(f"""

                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))['h1']
run = json.load(open('{td}/current_strategy_run.json'))
# both files only ever grow scores for NON-dead games in this test (quarantine
# never activates -- rate too low), so both should have all 5 games recorded
rs_sd = [(p["s"], p["d"]) for p in rs["progress"]]
run_sd = [(p["s"], p["d"]) for p in run["progress"]]
assert rs_sd == run_sd, (rs_sd, run_sd)
assert rs_sd == [(9000, 0), (0, 1), (11000, 0), (500, 1), (12000, 0)]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitMonitorSingleWriterTests(unittest.TestCase):
    """#24: one live game must add exactly one entry to the monitor window,
    not two (both functions run per game, but only update_rolling_scores
    is supposed to touch the monitor)."""

    def test_one_game_adds_exactly_one_window_entry(self):
        with tempfile.TemporaryDirectory() as td:
            script = _preamble(td, "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=5") + \
                textwrap.dedent(f"""\
                ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 \\
                    INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 \\
                    update_rolling_scores 9000 'g0.jsonl'
                INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 \\
                    _update_current_strategy_run h1 9000 'g0.jsonl'
                python3 - <<'PY'
import json
m = json.load(open('{td}/instadeath_monitor.json'))
assert len(m["window"]) == 1, m["window"]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitSeedTests(unittest.TestCase):
    """#25: seeding current_run from rolling must carry `progress` over,
    not silently drop it."""

    def test_seed_carries_progress_over(self):
        with tempfile.TemporaryDirectory() as td:
            rolling_path = Path(td) / "rolling_scores.json"
            scores = list(range(1000, 1030))
            progress = [{"s": s, "raw": s - 900, "turns": 150, "d": 0, "ts": 1000000 + i,
                         "t": None, "r": 0, "v": 0} for i, s in enumerate(scores)]
            rolling_path.write_text(json.dumps({
                "seedhash": {"scores": scores, "progress": progress, "games_total": 30,
                              "_recent_archives": [], "max_types": [], "russia_count": 0,
                              "soviet_count": 0, "best_max_type": 0, "frontier_hints": [],
                              "peak_high_type_counts": [], "deadline_guard_counts": [],
                              "deadline_guard_reason_tops": []},
            }))
            script = _preamble(td, "CURRENT_RUN_SCORE_KEEP=100") + textwrap.dedent(f"""\
                _seed_current_strategy_run_from_rolling seedhash
                python3 - <<'PY'
import json
run = json.load(open('{td}/current_strategy_run.json'))
assert len(run["progress"]) == len(run["scores"]) == 30, (len(run["progress"]), len(run["scores"]))
assert run["progress"][0]["s"] == 1000
assert run["progress"][-1]["s"] == 1029
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitMergeTests(unittest.TestCase):
    """#26: merging a stale hash's rolling entry into the actual hash must
    keep progress aligned with the merged scores, not desync."""

    def test_merge_keeps_progress_aligned(self):
        with tempfile.TemporaryDirectory() as td:
            stale_scores = [1000, 1001, 1002]
            stale_progress = [{"s": s, "raw": 1, "turns": 1, "d": 0, "ts": 1, "t": None,
                                "r": 0, "v": 0} for s in stale_scores]
            actual_scores = [2000, 2001]
            actual_progress = [{"s": s, "raw": 2, "turns": 2, "d": 0, "ts": 2, "t": None,
                                 "r": 0, "v": 0} for s in actual_scores]
            rolling_path = Path(td) / "rolling_scores.json"
            rolling_path.write_text(json.dumps({
                "stale_hash": {"scores": stale_scores, "progress": stale_progress,
                                "games_total": 3, "_recent_archives": []},
                "actual_hash": {"scores": actual_scores, "progress": actual_progress,
                                 "games_total": 2, "_recent_archives": []},
            }))
            script = _preamble(td, "ROLLING_SCORE_KEEP=20") + textwrap.dedent(f"""\
                _merge_rolling_scores_on_normalize stale_hash actual_hash
                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))
assert "stale_hash" not in rs
e = rs["actual_hash"]
assert e["scores"] == [1000, 1001, 1002, 2000, 2001], e["scores"]
assert len(e["progress"]) == len(e["scores"]), (len(e["progress"]), len(e["scores"]))
assert [p["s"] for p in e["progress"]] == e["scores"]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitQuarantineE2ETests(unittest.TestCase):
    """#23: a sustained outage quarantines dead games into
    quarantined_scores while alive games keep flowing into scores; recovery
    clears it. Uses a small DEAD_QUARANTINE_WINDOW so the test stays fast."""

    def test_quarantine_diverts_dead_games_and_clears_on_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            outage = "\n".join(
                f"ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 "
                f"INSTADEATH_RECORD_RAW=0 INSTADEATH_RECORD_TURNS=0 "
                f"update_rolling_scores 0 'dead{i}.jsonl'\n"
                f"INSTADEATH_RECORD_RAW=0 INSTADEATH_RECORD_TURNS=0 "
                f"_update_current_strategy_run h1 0 'dead{i}.jsonl'"
                for i in range(5)
            )
            more_dead = "\n".join(
                f"ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 "
                f"INSTADEATH_RECORD_RAW=0 INSTADEATH_RECORD_TURNS=0 "
                f"update_rolling_scores 0 'stilldead{i}.jsonl'\n"
                f"INSTADEATH_RECORD_RAW=0 INSTADEATH_RECORD_TURNS=0 "
                f"_update_current_strategy_run h1 0 'stilldead{i}.jsonl'"
                for i in range(3)
            )
            recovery = "\n".join(
                f"ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 "
                f"INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 "
                f"update_rolling_scores 9000 'alive{i}.jsonl'\n"
                f"INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 "
                f"_update_current_strategy_run h1 9000 'alive{i}.jsonl'"
                for i in range(5)
            )
            script = _preamble(
                td,
                "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_ENABLED=1\n"
                "DEAD_QUARANTINE_WINDOW=5\nDEAD_QUARANTINE_CLEAR_WINDOW=5\n"
                "DEAD_QUARANTINE_CLEAR_RATE=0.05\nDEAD_HARD_RATIO=0.5\n"
            ) + outage + "\n" + more_dead + "\n" + recovery + textwrap.dedent(f"""

                python3 - <<'PY'
import json
m = json.load(open('{td}/instadeath_monitor.json'))
assert m["quarantine"]["active"] is False, m["quarantine"]
assert m["quarantine"]["cleared_at"] is not None
rs = json.load(open('{td}/rolling_scores.json'))['h1']
run = json.load(open('{td}/current_strategy_run.json'))
# the first 4 dead games happened before quarantine could evaluate
# (n<5==window), so they landed in scores. The 5th dead game (dead4) is the
# one whose observation makes the window reach n=5 -- it turns evaluated=True
# and verdict=HARNESS on the SAME call, and that call's own divert decision
# reads the just-updated quarantine.active, so dead4 itself is diverted too
# (not just the ones strictly after it). Plus the 3 "still dead" games after
# = 4 diverted total.
for label, entry in (("rolling", rs), ("current_run", run)):
    qs = entry.get("quarantined_scores") or []
    assert len(qs) == 4, (label, qs)
    assert all(x == 0 for x in qs), (label, qs)
    assert entry["scores"][-5:] == [9000] * 5, (label, entry["scores"])
    # accounting: total games seen = 4 landed-dead + 4 diverted + 5 alive = 13
    assert entry["games_total"] == 13, (label, entry["games_total"])
PY
                """)
            result = _run(script, timeout=60)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitNonLiveCallerTests(unittest.TestCase):
    """#27: repair_current_run_from_history.sh-style callers must not leak
    LAST_RAW_SCORE/LAST_TURNS from an unrelated live game into a replayed
    archive's progress record."""

    def test_unset_last_raw_score_yields_null_raw(self):
        with tempfile.TemporaryDirectory() as td:
            script = _preamble(td, "INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=5") + \
                textwrap.dedent(f"""\
                export LAST_RAW_SCORE=9999
                export LAST_TURNS=999
                (
                    unset LAST_RAW_SCORE LAST_TURNS
                    export INSTADEATH_MONITOR_UPDATE=0
                    ROLLING_SCORE_STRATEGY_HASH=h1 update_rolling_scores 9000 'replayed.jsonl'
                )
                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))['h1']
assert rs["progress"][0]["raw"] is None, rs["progress"][0]
assert rs["progress"][0]["turns"] is None, rs["progress"][0]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class InstadeathSplitMonitorFailureIsolationTests(unittest.TestCase):
    """#28: a broken monitor (e.g. unwritable DEAD_MONITOR_FILE directory)
    must never stop rolling_scores.json from being written."""

    def test_unwritable_monitor_path_does_not_block_rolling_update(self):
        with tempfile.TemporaryDirectory() as td:
            bad_dir = Path(td) / "no_such_parent" / "deeply" / "nested"
            # Point DEAD_MONITOR_FILE at a path whose parent can't be created
            # by making an intermediate component a regular file.
            blocker = Path(td) / "no_such_parent"
            blocker.write_text("blocked")
            script = _preamble(
                td, f"INSTADEATH_SPLIT_ENABLED=1\nDEAD_QUARANTINE_WINDOW=5\n"
                    f"DEAD_MONITOR_FILE='{bad_dir}/instadeath_monitor.json'"
            ) + textwrap.dedent(f"""\
                ROLLING_SCORE_STRATEGY_HASH=h1 INSTADEATH_MONITOR_UPDATE=1 \\
                    INSTADEATH_RECORD_RAW=900 INSTADEATH_RECORD_TURNS=150 \\
                    update_rolling_scores 9000 'g0.jsonl' 2>&1 | grep -q 'updated'
                python3 - <<'PY'
import json
rs = json.load(open('{td}/rolling_scores.json'))
assert rs["h1"]["scores"] == [9000]
PY
                """)
            result = _run(script)
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


if __name__ == "__main__":
    unittest.main()
