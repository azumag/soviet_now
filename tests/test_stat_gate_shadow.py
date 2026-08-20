"""End-to-end shadow-mode test for Phase 2 of soren-stat-gate-design.md
(section A / C-1): with STAT_GATE_MODE=shadow, check_regression() must emit a
[STATGATE] observation line AND leave the legacy verdict/rollback behavior
byte-identical to STAT_GATE_MODE=off.

This actually invokes check_regression() as a real bash function (via
subprocess, like the live loop does). All file/toggle env vars are redirected
into a tmpdir so the test never touches the repo's tmp/state, and the
side-effecting sub-gates (annealing, early-objective, stage-achievement,
wildcard, rollback-trend-grace, tabu) are disabled so the legacy verdict
reduces to the plain comp-based regression check -- which is what we assert is
unchanged.
"""

import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The real repo strategy.py's decide() hash, so extract_decide_hash.py
# resolves a real current_hash (matching the synthetic rolling entry below).
CURRENT_HASH = "0890dbefd73e"
ANCHOR_HASH = "anchor00000000000000000000000000000000"


def _preamble(td, mode):
    overrides = "\n".join([
        f"ROLLING_SCORES_FILE='{td}/rolling_scores.json'",
        f"CURRENT_STRATEGY_RUN_FILE='{td}/current_strategy_run.json'",
        f"ACTIVE_BRANCH_FILE='{td}/active_branch.json'",
        f"BEST_STRATEGY_ANCHOR_FILE='{td}/best_strategy_anchor.json'",
        f"STRATEGY_HASH_ARCHIVE_DIR='{td}/by_hash'",
        f"STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{td}/by_hash_perm'",
        f"TMP_STATE_DIR='{td}/tmp_state'",
        f"STAGNATION_COUNTER_FILE='{td}/stagnation.json'",
        f"WILDCARD_ORIGIN_FILE='{td}/wildcard_origin.json'",
        f"WILDCARD_ATTEMPT_STATE_FILE='{td}/wildcard_attempt_state.json'",
        f"WILDCARD_OUTCOME_FILE='{td}/wildcard_outcomes.jsonl'",
        f"ANNEALING_OBSERVE_FILE='{td}/annealing_candidates.jsonl'",
        f"ANNEALING_OBSERVE_ENABLED=0",
        f"EVAL_SCORE_HISTORY_FILE='{td}/eval_score_history.txt'",
        f"ARCHIVE_RESTART_COOLDOWN_FILE='{td}/archive_restart_cooldown.json'",
        f"REJECTED_HASHES_FILE='{td}/rejected_hashes.txt'",
        f"COMMANDS='{td}/commands'",
        f"ROLLING_SCORE_KEEP=200",
        f"CURRENT_RUN_SCORE_KEEP=200",
        f"STAT_GATE_MODE={mode}",
        f"INSTADEATH_SPLIT_ENABLED=0",
        # Disable the side-effecting sub-gates so legacy verdict is the plain
        # comp-based regression check (predictable + what we assert unchanged).
        f"ANNEALING_OBSERVE_ENABLED=0",
        f"EARLY_OBJECTIVE_REGRESSION_ENABLED=0",
        f"EARLY_COMP_TOP_GAP_ENABLED=0",
        f"STAGE_ACHIEVEMENT_REGRESSION_ENABLED=0",
        f"SAME_HASH_BACKSLIDE_RESET_ENABLED=0",
        f"ROLLBACK_TREND_GRACE_ENABLED=0",
        f"TABU_ENABLED=0",
        f"REGRESSION_DISABLED=0",
        # Point at the real strategy.py so current_hash resolves correctly.
        f"STRATEGY_FILE='{REPO_ROOT}/strategy.py'",
    ])
    return textwrap.dedent(f"""\
        set +e
        source ./core/config.sh
        mkdir -p '{td}/tmp_state' '{td}/by_hash' '{td}/by_hash_perm'
        {overrides}
        source ./core/helpers.sh
        source ./strategy/regression.sh
        source ./strategy/improve.sh
        """)


def _seed_data(td):
    # Pass TD via env (the seed python uses a quoted heredoc, so $td would not
    # expand there).
    script = textwrap.dedent(f"""\
        TD='{td}' python3 - <<'PY'
import json, os, statistics
td = os.environ['TD']
H="{CURRENT_HASH}"
A="{ANCHOR_HASH}"
scores = [10500 + i for i in range(100)]
def prog(s):
    return [{{"s": v, "raw": 900, "turns": 150, "d": 0, "ts": 1000000 + i,
              "t": None, "r": 0, "v": 0}} for i, v in enumerate(s)]
base = {{"max_types": [], "russia_count": 0, "soviet_count": 0,
         "best_max_type": 0, "frontier_hints": [], "peak_high_type_counts": [],
         "deadline_guard_counts": [], "deadline_guard_reason_tops": [],
         "_recent_archives": []}}
roll = {{
    H: dict(scores=scores, games_total=100, progress=prog(scores), **base),
    A: dict(scores=scores, games_total=200, progress=prog(scores), **base),
}}
json.dump(roll, open(td + '/rolling_scores.json', 'w'))
run = dict(hash=H, scores=scores, progress=prog(scores), games_total=100, **base)
json.dump(run, open(td + '/current_strategy_run.json', 'w'))
n = len(scores)
mean = sum(scores) / n
p50 = sorted(scores)[n // 2]
p25 = sorted(scores)[n // 4]
lcb = mean - 1.28 * (statistics.pstdev(scores) / n ** 0.5)
json.dump({{"hash": A, "comp": 0.55 * p50 + 0.30 * p25 + 0.15 * lcb,
           "p50": p50, "p25": p25, "lcb": lcb, "n": n}},
          open(td + '/best_strategy_anchor.json', 'w'))
json.dump({{"head_hash": H, "anchor_hash": A}}, open(td + '/active_branch.json', 'w'))
print("seeded")
PY
        """)
    return script


def _run_check_regression(td, mode, timeout=60):
    script = (_preamble(td, mode) + _seed_data(td) + textwrap.dedent("""
        out=$(check_regression 2>/dev/null) || true
        echo "OUT_START"
        echo "$out"
        echo "OUT_END"
        """))
    res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise AssertionError(
            f"check_regression failed (mode={mode})\nstdout={res.stdout}\nstderr={res.stderr}")
    text = res.stdout
    text = text.split("OUT_START", 1)[1].rsplit("OUT_END", 1)[0]
    # Strip leading [HH:MM:SS] timestamps so the comparison is content-only.
    lines = [re.sub(r"^\[\d{2}:\d{2}:\d{2}\] ", "", ln) for ln in text.splitlines()]
    return lines


def _strip_statgate(lines):
    return [ln for ln in lines if not ln.startswith("[STATGATE]")]


class StatGateShadowTests(unittest.TestCase):
    def test_shadow_is_non_disruptive_vs_off(self):
        with tempfile.TemporaryDirectory() as td:
            off = _run_check_regression(td, "off")
            shadow = _run_check_regression(td, "shadow")
            # Only shadow may contain a STATGATE line.
            self.assertFalse(any(ln.startswith("[STATGATE]") for ln in off),
                             msg=f"off-mode unexpectedly emitted STATGATE: {off}")
            statgate = [ln for ln in shadow if ln.startswith("[STATGATE]")]
            self.assertEqual(len(statgate), 1,
                             msg=f"shadow should emit exactly one STATGATE: {shadow}")
            # The legacy (non-STATGATE) output must be byte-identical.
            self.assertEqual(_strip_statgate(off), _strip_statgate(shadow))

    def test_statgate_payload_shape(self):
        with tempfile.TemporaryDirectory() as td:
            shadow = _run_check_regression(td, "shadow")
            statgate = [ln for ln in shadow if ln.startswith("[STATGATE]")]
            self.assertEqual(len(statgate), 1)
            # The STATGATE line is key=value pairs (matches the design's
            # aggregation one-liner), e.g. "legacy=OK stat=NONINFERIOR ...".
            payload = statgate[0][len("[STATGATE] "):]
            kv = dict(tok.split("=", 1) for tok in payload.split() if "=" in tok)
            # legacy must be a known verdict label; agree must be YES/NO; stat
            # must be a decide() verdict (or UNTESTED if something unready).
            self.assertIn(kv["legacy"], ("OK", "REGRESSION", "PROMOTE", "RESET"))
            self.assertIn(kv["agree"], ("YES", "NO"))
            self.assertIn(
                kv["stat"],
                ("REGRESSION_HARD", "REGRESSION_SOFT", "PROMOTE", "NONINFERIOR",
                 "INCONCLUSIVE", "INSUFFICIENT_REFERENCE", "INSUFFICIENT_CURRENT",
                 "NOT_A_LOOK", "UNTESTED"))
            # With identical healthy current vs anchor, expect non-regression
            # and agreement.
            self.assertNotIn(kv["stat"], ("REGRESSION_HARD", "REGRESSION_SOFT"))
            self.assertEqual(kv["agree"], "YES")


if __name__ == "__main__":
    unittest.main()
