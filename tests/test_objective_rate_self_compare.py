"""目的 rate ゲート (lost_russia_rate / lost_soviet_rate) の自己比較ガード。

2026-11 の本番実測: 粛清 (regression rollback) の直後に目的 rate ゲートが発火して
同じ戦略を再び粛清し、regression_streak が 34 まで伸びた。current strategy と
anchor が同一 hash のときは「同一戦略の自己比較」なので、目的 rate ゲートを
発火させない (固定ログ RATEGATE_SKIP → bash 側で [REGRESSION] objective rate gate
skipped として記録)。異なる hash では従来どおり rate 後退を粛清根拠にする。

check_regression の python heredoc を抽出して fixture 上で直接実行する
(tests/test_stage_gate_stat.sh と同じ手法。VM 状態に依存しない)。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ANCHOR_HASH = "aaa000000000"
CURRENT_HASH = "bbb000000000"


def _extract_check_regression_python(dest: Path) -> Path:
    """strategy/regression.sh の check_regression 内 heredoc を抜き出す。"""
    text = (ROOT / "strategy/regression.sh").read_text(encoding="utf-8")
    start = text.index("check_regression() {")
    marker = "<<'PY'\n"
    body_start = text.index(marker, start) + len(marker)
    body_end = text.index("\nPY\n", body_start)
    dest.write_text(text[body_start:body_end] + "\n", encoding="utf-8")
    return dest


def _metrics(scores):
    xs = [int(x) for x in scores]
    n = len(xs)
    mean = sum(xs) / n
    ordered = sorted(xs)

    def q(p):
        if n == 1:
            return float(ordered[0])
        pos = (n - 1) * p
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

    p25, p50 = q(0.25), q(0.5)
    if n > 1:
        import math

        std = math.sqrt(sum((x - mean) ** 2 for x in xs) / n)
    else:
        std = 0.0
    lcb = mean - 1.28 * (std / (n ** 0.5))
    return {
        "comp": 0.55 * p50 + 0.30 * p25 + 0.15 * lcb,
        "p50": p50,
        "p25": p25,
        "lcb": lcb,
        "n": n,
    }


def _build_fixture(root: Path, current_hash: str, same_hash: bool) -> None:
    """anchor はロシア 10/100、current はロシア 0/100 の窓にする。

    score は両者同一 (comp 差 0) にして、rate ゲート以外の粛清根拠を消す。
    """
    scores = [10000 + (i * 37) % 400 for i in range(100)]
    anchor_scores = list(scores)
    anchor_entry = {
        "scores": anchor_scores,
        "max_types": [15] * 10 + [13] * 90,
        "russia_count": 10,
        "soviet_count": 0,
        "best_max_type": 15,
        "games_total": len(anchor_scores),
    }
    run = {
        "hash": current_hash,
        "scores": list(scores),
        "max_types": [13] * 100,
        "russia_count": 0,
        "soviet_count": 0,
        "best_max_type": 13,
        "games_total": len(scores),
    }
    rolling = {ANCHOR_HASH: anchor_entry}
    if same_hash:
        rolling[current_hash] = anchor_entry
    (root / "tmp/state").mkdir(parents=True, exist_ok=True)
    for sub in ("strategy_versions/by_hash", "strategy_versions_archive/by_hash"):
        (root / sub).mkdir(parents=True, exist_ok=True)
        (root / sub / f"{ANCHOR_HASH}.py").write_text("", encoding="utf-8")
    (root / "tmp/state/rolling_scores.json").write_text(
        json.dumps(rolling), encoding="utf-8")
    (root / "tmp/state/current_strategy_run.json").write_text(
        json.dumps(run), encoding="utf-8")
    anchor_metrics = _metrics(anchor_entry["scores"])
    (root / "tmp/state/best_strategy_anchor.json").write_text(
        json.dumps({"hash": ANCHOR_HASH, **anchor_metrics,
                    "best_max_type": 15, "russia_count": 10,
                    "soviet_count": 0}), encoding="utf-8")
    if same_hash:
        # same hash の経路は branch_active (head_hash == current hash) でのみ
        # 早期 OK 分岐を越えて目的ゲートまで到達する。
        (root / "tmp/state/active_branch.json").write_text(
            json.dumps({
                "head_hash": current_hash,
                "anchor_hash": current_hash,
                "anchor": anchor_metrics,
                "depth": 0,
                "closed_games": 0,
                "patience": 0,
            }), encoding="utf-8")
    else:
        (root / "tmp/state/active_branch.json").write_text("{}", encoding="utf-8")


def _run_check(root: Path, pyfile: Path, current_hash: str) -> subprocess.CompletedProcess:
    argv = [
        "tmp/state/rolling_scores.json",
        "tmp/state/current_strategy_run.json",
        "tmp/state/active_branch.json",
        "tmp/state/best_strategy_anchor.json",
        current_hash,
        "12", "strategy_versions/by_hash", "1000", "800", "1600", "2",
        "4", "400", "3", "2800", "2200", "3300", "2",
        "tmp/state/stagnation_counter.json",
        "tmp/state/wildcard_origin.json",
        "tmp/state/wildcard_attempt_state.json",
        "tmp/state/wildcard_outcomes.jsonl",
        "tmp/state/annealing_candidates.jsonl", "1", "1800", "0.85",
        "0", "4", "15", "1", "4", "7", "eval_score_history.txt",
        "1", "50", "50", "0",
        "tmp/state/archive_restart_cooldown.json", "1",
        # early_comp_top_gap / stage_achievement は無効化し、目的 rate ゲートだけを見る
        "0", "12", "0.85", "0", "4", "0.80", "12,13,14,15",
        "strategy_versions_archive/by_hash",
    ]
    env = dict(os.environ)
    env["ELOOP_LIB_DIR"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        ["python3", "-W", "ignore", str(pyfile), *argv],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class ObjectiveRateSelfCompareTests(unittest.TestCase):
    def _run_case(self, current_hash: str, same_hash: bool):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pyfile = _extract_check_regression_python(root / "check_regression.py")
            subprocess.run(
                ["python3", "-W", "ignore", "-m", "py_compile", str(pyfile)],
                check=True, capture_output=True, text=True)
            _build_fixture(root, current_hash, same_hash)
            result = _run_check(root, pyfile, current_hash)
            self.assertEqual(result.returncode, 0, msg=(
                f"stdout={result.stdout}\nstderr={result.stderr}"))
            return result.stdout

    def test_same_hash_does_not_fire_objective_rate_gate(self):
        """current == anchor の同一戦略を rate 差だけで粛清しない。"""
        out = self._run_case(ANCHOR_HASH, same_hash=True)
        self.assertNotIn("REGRESSION:", out, msg=out)
        # 自己比較で抑制されたことを固定ログ用マーカーで残す
        self.assertIn("RATEGATE_SKIP:%s:lost_russia_rate" % ANCHOR_HASH, out, msg=out)
        # 異なる hash なら出るはずの rate 根拠は判定行には出さない
        verdict_lines = "\n".join(
            line for line in out.splitlines() if not line.startswith("RATEGATE_SKIP:"))
        self.assertNotIn("lost_russia_rate", verdict_lines, msg=out)

    def test_different_hash_still_fires_objective_rate_gate(self):
        """hash が異なれば従来どおりロシア率後退を粛清根拠にする。"""
        out = self._run_case(CURRENT_HASH, same_hash=False)
        self.assertIn("REGRESSION:mode=objective_regression", out, msg=out)
        self.assertIn("lost_russia_rate", out, msg=out)
        self.assertIn("rategate=type15/cur0of100/anc10of100", out, msg=out)
        self.assertNotIn("RATEGATE_SKIP:", out, msg=out)

    def test_bash_side_emits_fixed_log_and_strips_marker(self):
        """RATEGATE_SKIP は固定ログとして出し、$result からは除去する。"""
        text = (ROOT / "strategy/regression.sh").read_text(encoding="utf-8")
        start = text.index("\tlocal _rategate_skip_line")
        end = text.index("\n\tfi\n", start) + len("\n\tfi\n")
        with tempfile.TemporaryDirectory() as td:
            strip = Path(td) / "strip.sh"
            strip.write_text(text[start:end], encoding="utf-8")
            script = textwrap.dedent(
                """\
                log() { printf 'LOG:%s\\n' "$*"; }
                f() {
                  local result
                  result=$(printf 'RATEGATE_SKIP:abc123:lost_russia_rate:rategate=type15/cur0of100/anc10of100\\nPROMOTE:anchor_hash=a,current_hash=b\\n')
                  source "$1"
                  printf 'RESULT:%s\\n' "$result"
                }
                """
            ) + f'\nf "{strip}"\n'
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "LOG:[REGRESSION] objective rate gate skipped: "
                "current strategy is the anchor (hash=abc123)", result.stdout)
            self.assertIn("RESULT:PROMOTE:anchor_hash=a,current_hash=b",
                          result.stdout)
            self.assertNotIn("RESULT:RATEGATE_SKIP", result.stdout)

    def test_lint_baseline_diff_suppresses_inherited_defects(self):
        """--baseline: base の継承欠陥では rc=1 にせず、新規欠陥だけを拒否する。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "base.py"
            cand = root / "cand.py"
            src = textwrap.dedent(
                """\
                def decide(game_state, analysis):
                    value = ---1
                    return {"x": 0, "reason": str(value)}
                """
            )
            base.write_text(src, encoding="utf-8")
            tool = ROOT / "tools/strategy_semantic_lint.py"
            inherited = subprocess.run(
                ["python3", str(tool), "--baseline", str(base), str(base)],
                capture_output=True, text=True)
            self.assertEqual(inherited.returncode, 0, msg=inherited.stdout + inherited.stderr)
            self.assertEqual(inherited.stdout.strip(), "")
            cand.write_text(src + "\nbad = --1\n", encoding="utf-8")
            new_defect = subprocess.run(
                ["python3", str(tool), "--baseline", str(base), str(cand)],
                capture_output=True, text=True)
            self.assertEqual(new_defect.returncode, 1, msg=new_defect.stdout + new_defect.stderr)
            self.assertIn("consecutive_unary_minus", new_defect.stdout)
            # baseline 無しの既定経路は従来どおり (継承欠陥でも rc=1)
            legacy = subprocess.run(
                ["python3", str(tool), str(cand)], capture_output=True, text=True)
            self.assertEqual(legacy.returncode, 1)
            # baseline 読み取り不能は環境要因 = rc=2
            missing = subprocess.run(
                ["python3", str(tool), "--baseline", str(root / "nope.py"), str(cand)],
                capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2, msg=missing.stderr)


if __name__ == "__main__":
    unittest.main()
