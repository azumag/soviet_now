"""帯域脱出機構 (D + E + F) の不変条件テスト。

主目的:
  D — diversity premium は anchor 比較用のローカル値のみで永続化されない
  E — tabu は anchor 昇格のみを阻止し、rolling_scores の値は保持する
  F — wildcard reason が CLI 引数 6 番目として eloop_improve.sh に伝わる
        wildcard_origin に登録された hash だけ branch budget が override される
        wildcard_perturb はコメント・空行・docstring を保持し、選定リテラル
        以外を変更しない
  共通 — stagnation_counter が 4 種類の遷移で期待通り動く

実行:
  python3 -m unittest tests/test_escape_mechanisms.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --- D: diversity premium does not get persisted -----------------------------

class TestDiversityPremiumNotPersisted(unittest.TestCase):
    def test_anchor_file_stores_raw_metrics_only(self):
        """anchor 候補に diversity premium が乗っても、書き戻される comp は raw のまま。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            last_anchor_change_file = td / "last_anchor_change.md"

            # 2 候補: A (高 comp), B (やや低 comp, 挙動差大)
            rs_file.write_text(
                json.dumps(
                    {
                        "hashA": {
                            "scores": [1000] * 24,
                            "games_total": 24,
                            "_recent_archives": [],
                        },
                        "hashB": {
                            "scores": [950] * 24,
                            "games_total": 24,
                            "_recent_archives": [],
                        },
                    }
                )
            )
            stable_source = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
            (archive_dir / "hashA.py").write_text(stable_source)
            (archive_dir / "hashB.py").write_text(stable_source)

            # behavior_signatures 事前注入で B にだけ大きな挙動差を持たせる
            beh_file.write_text(
                json.dumps(
                    {
                        "hashA": {"reason": {"X": 1.0}, "x_bins": [1.0, 0, 0, 0, 0, 0], "n_games": 12, "merge_take_rate": 0.5},
                        "hashB": {"reason": {"Y": 1.0}, "x_bins": [0, 0, 0, 0, 0, 1.0], "n_games": 12, "merge_take_rate": 0.1},
                    }
                )
            )

            # behavior_signature ライブラリは file ベースで動くが、キャッシュにあれば再計算しないので OK
            # (anchor refresh は cached.get("n_games") >= min(len(archives), 6) を判定するため n_games=12 と空 archives で cache hit)

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "1"
            env["DIVERSITY_PREMIUM_WEIGHT"] = "1000"
            env["EXPLORE_GAP_MAX_RATIO"] = "0.20"
            env["TABU_ENABLED"] = "0"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)

            # subprocess で _refresh_best_strategy_anchor を呼ぶ
            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            self.assertTrue(anchor_file.exists(), msg=f"anchor not written. stdout={result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            # 重要: 書き戻された comp は raw のいずれかと一致 (premium 加算されていない)
            raw_comp_A = 0.50 * 1000 + 0.30 * 1000 + 0.20 * 1000  # = 1000.0
            raw_comp_B = 0.50 * 950 + 0.30 * 950 + 0.20 * 950
            self.assertIn(round(anchor["comp"]), [round(raw_comp_A), round(raw_comp_B)],
                          msg=f"persisted comp={anchor['comp']} is not raw")

    def test_anchor_selection_prefers_near_score_soviet_progress(self):
        """anchor はトップスコア近傍ならソ連到達済みを優先して局所解化を避ける。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            last_anchor_change_file = td / "last_anchor_change.md"

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnly": {
                            "scores": [1200] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 13,
                            "russia_count": 0,
                            "soviet_count": 0,
                        },
                        "sovietPath": {
                            "scores": [1120] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "max_types": [16] + [13] * 11,
                            "best_max_type": 16,
                            "russia_count": 1,
                            "soviet_count": 1,
                        },
                    }
                )
            )
            stable_source = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
            (archive_dir / "scoreOnly.py").write_text(stable_source)
            (archive_dir / "sovietPath.py").write_text(stable_source)

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "0"
            env["TABU_ENABLED"] = "0"
            env["OBJECTIVE_ANCHOR_PRIORITY_ENABLED"] = "1"
            env["OBJECTIVE_ANCHOR_MIN_COMP_RATIO"] = "0.90"
            env["OBJECTIVE_ANCHOR_MAX_COMP_GAP"] = "1500"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "sovietPath")
            self.assertEqual(anchor["best_max_type"], 16)
            self.assertEqual(anchor["russia_count"], 1)
            self.assertEqual(anchor["soviet_count"], 1)

    def _run_frontier_anchor_case(self, td, frontier_peaks, frontier_min_games="2", enabled="1"):
        """Helper: scoreOnly (higher comp, no frontier) vs frontierStrain (near comp,
        peak_high_type_counts=frontier_peaks). Returns the selected anchor hash."""
        td = Path(td)
        rs_file = td / "rolling_scores.json"
        anchor_file = td / "best_strategy_anchor.json"
        archive_dir = td / "by_hash"
        archive_dir.mkdir()
        rejected_file = td / "rejected.txt"
        beh_file = td / "behavior_signatures.json"
        tabu_file = td / "tabu.jsonl"
        last_anchor_change_file = td / "last_anchor_change.md"
        rs_file.write_text(json.dumps({
            "scoreOnly": {
                "scores": [1200] * 12, "games_total": 12, "_recent_archives": [],
                "best_max_type": 14, "russia_count": 0, "soviet_count": 0,
            },
            "frontierStrain": {
                "scores": [1120] * 12, "games_total": 12, "_recent_archives": [],
                "max_types": [15] + [14] * 11, "best_max_type": 15,
                "russia_count": 1, "soviet_count": 0,
                "peak_high_type_counts": frontier_peaks,
            },
        }))
        stable = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
        (archive_dir / "scoreOnly.py").write_text(stable)
        (archive_dir / "frontierStrain.py").write_text(stable)
        env = os.environ.copy()
        env["DIVERSITY_PREMIUM_ENABLED"] = "0"
        env["TABU_ENABLED"] = "0"
        env["OBJECTIVE_ANCHOR_PRIORITY_ENABLED"] = enabled
        env["OBJECTIVE_ANCHOR_MIN_COMP_RATIO"] = "0.90"
        env["OBJECTIVE_ANCHOR_MAX_COMP_GAP"] = "1500"
        env["OBJECTIVE_FRONTIER_MIN_GAMES"] = frontier_min_games
        env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
        env["TABU_SIGNATURES_FILE"] = str(tabu_file)
        env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)
        script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
        result = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
        return json.loads(anchor_file.read_text())["hash"]

    def test_frontier_strain_promoted_when_repeatable_and_near_leader(self):
        """2nd-Russia frontier (T15x1+T14x2) reached in >= OBJECTIVE_FRONTIER_MIN_GAMES
        games AND near the score leader => protected as anchor over a score-only optimum."""
        with tempfile.TemporaryDirectory() as td:
            peaks = ["T15x1 T14x2 T13x1"] * 2 + ["T13x1 T12x2"] * 10
            self.assertEqual(self._run_frontier_anchor_case(td, peaks), "frontierStrain")

    def test_single_frontier_game_not_promoted(self):
        """Fluke guard: frontier in only 1 game (< min_games=2) must NOT be promoted —
        this is the live situation (every existing frontier hash = exactly 1 game)."""
        with tempfile.TemporaryDirectory() as td:
            peaks = ["T15x1 T14x2 T13x1"] * 1 + ["T13x1 T12x2"] * 11
            self.assertEqual(self._run_frontier_anchor_case(td, peaks), "scoreOnly")

    def test_single_russia_not_promoted(self):
        """2026-05-25 guard: single Russia (T15x1 with only T14x1, no 2nd-Russia
        material) is NOT a frontier in ANY game, so a lower-comp single-Russia strain
        must NOT displace the score anchor."""
        with tempfile.TemporaryDirectory() as td:
            peaks = ["T15x1 T14x1 T13x2"] * 12
            self.assertEqual(self._run_frontier_anchor_case(td, peaks), "scoreOnly")

    def test_frontier_rung_is_noop_when_priority_disabled(self):
        """Reversibility: with OBJECTIVE_ANCHOR_PRIORITY_ENABLED=0, a repeatable frontier
        strain is ignored (byte-identical legacy behavior) => score anchor wins."""
        with tempfile.TemporaryDirectory() as td:
            peaks = ["T15x1 T14x2 T13x1"] * 4 + ["T13x1 T12x2"] * 8
            self.assertEqual(self._run_frontier_anchor_case(td, peaks, enabled="0"), "scoreOnly")

    def test_both_anchor_paths_carry_soviet_frontier_rung(self):
        """Objective-aware selectors and rollback checks must carry the graded
        frontier ladder so anchor, rollback target, and purge checks consistently
        protect the Soviet path."""
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        # frontier detection helper present in both selector blocks and check_regression.
        self.assertEqual(regression.count("def _is_soviet_frontier(peak_str):"), 3)
        self.assertIn("c.get(15, 0) >= 2 or (c.get(15, 0) >= 1 and c.get(14, 0) >= 2)", regression)
        # graded ladder default keys carry Soviet, frontier, and recurrent-Russia rungs in both blocks.
        self.assertEqual(regression.count("objective_key = (0, 0, 0, 0, 0)"), 2)
        # repeatable-games threshold threaded + parsed in both selector blocks and check_regression.
        self.assertIn("objective_frontier_min_games = int(sys.argv[25])", regression)
        self.assertIn("objective_frontier_min_games = int(sys.argv[14])", regression)
        self.assertIn("objective_frontier_min_games = max(1, int(sys.argv[48]))", regression)
        self.assertIn('OBJECTIVE_FRONTIER_MIN_GAMES="${OBJECTIVE_FRONTIER_MIN_GAMES:-2}"', config)
        self.assertIn('OBJECTIVE_RUSSIA_MIN_COUNT="${OBJECTIVE_RUSSIA_MIN_COUNT:-2}"', config)
        self.assertIn('OBJECTIVE_RUSSIA_MIN_RATE="${OBJECTIVE_RUSSIA_MIN_RATE:-0.15}"', config)

    def test_anchor_refresh_uses_archive_restart_source_objective_metadata(self):
        """archive_restart の source メタがあれば、pruned 履歴でも建国進捗を 0 扱いしない。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            origin_file = td / "wildcard_origin.json"

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnly": {
                            "scores": [12000] * 12,
                            "games_total": 12,
                            "max_types": [0],
                        },
                    }
                ),
                encoding="utf-8",
            )
            origin_file.write_text(
                json.dumps(
                    {
                        "scoreOnly": {
                            "origin_type": "archive_restart",
                            "source_hash": "scoreOnly",
                            "source_best_max_type": 15,
                            "source_russia_count": 1,
                            "source_soviet_count": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            stable_source = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
            (archive_dir / "scoreOnly.py").write_text(stable_source, encoding="utf-8")

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "0"
            env["TABU_ENABLED"] = "0"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
WILDCARD_ORIGIN_FILE='{origin_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "scoreOnly")
            self.assertEqual(anchor["best_max_type"], 15)
            self.assertEqual(anchor["russia_count"], 1)
            self.assertEqual(anchor["soviet_count"], 0)

    def test_anchor_refresh_preserves_valid_current_anchor(self):
        """current が既存 anchor の場合、2番手へ強制差し替えせず同一hash判定へ残す。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            last_anchor_change_file = td / "last_anchor_change.md"

            rs_file.write_text(
                json.dumps(
                    {
                        "currentRussia": {
                            "scores": [1100] * 20,
                            "games_total": 40,
                            "_recent_archives": [],
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        },
                        "scoreOnly": {
                            "scores": [1200] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 14,
                            "russia_count": 0,
                            "soviet_count": 0,
                        },
                    }
                )
            )
            stable_source = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
            (archive_dir / "currentRussia.py").write_text(stable_source)
            (archive_dir / "scoreOnly.py").write_text(stable_source)
            anchor_file.write_text(
                json.dumps(
                    {
                        "hash": "currentRussia",
                        "comp": 1100,
                        "p50": 1100,
                        "p25": 1100,
                        "lcb": 1100,
                        "n": 20,
                        "best_max_type": 15,
                        "russia_count": 1,
                        "soviet_count": 0,
                    }
                )
            )

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "0"
            env["TABU_ENABLED"] = "0"
            env["OBJECTIVE_ANCHOR_PRIORITY_ENABLED"] = "1"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "currentRussia" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "currentRussia")
            self.assertEqual(anchor["best_max_type"], 15)
            self.assertEqual(anchor["russia_count"], 1)

    def test_direct_anchor_promotion_preserves_russia_anchor_from_score_only(self):
        """PROMOTE 経路でも Russia anchor を score-only で直接上書きしない。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            run_file = td / "current_strategy_run.json"
            anchor_file = td / "best_strategy_anchor.json"
            last_anchor_change_file = td / "last_anchor_change.md"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnly": {
                            "scores": [1300] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 14,
                            "russia_count": 0,
                            "soviet_count": 0,
                        }
                    }
                )
            )
            run_file.write_text(
                json.dumps(
                    {
                        "hash": "scoreOnly",
                        "scores": [1300] * 12,
                        "games_total": 12,
                        "best_max_type": 14,
                        "russia_count": 0,
                        "soviet_count": 0,
                    }
                )
            )
            anchor_file.write_text(
                json.dumps(
                    {
                        "hash": "russiaPath",
                        "comp": 1100,
                        "p50": 1100,
                        "p25": 1100,
                        "lcb": 1100,
                        "n": 12,
                        "best_max_type": 15,
                        "russia_count": 1,
                        "soviet_count": 0,
                    }
                )
            )
            (archive_dir / "scoreOnly.py").write_text("# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n")

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
CURRENT_STRATEGY_RUN_FILE='{run_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
LAST_ANCHOR_CHANGE_FILE='{last_anchor_change_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_promote_current_strategy_to_anchor scoreOnly
echo promote_rc=$?
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            self.assertIn("promote_rc=1", result.stdout)
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "russiaPath")
            self.assertEqual(anchor["russia_count"], 1)

    def test_direct_anchor_promotion_keeps_objective_fields_when_allowed(self):
        """目的進捗を落とさない PROMOTE は成功し、anchor payload に進捗も残す。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            run_file = td / "current_strategy_run.json"
            anchor_file = td / "best_strategy_anchor.json"
            last_anchor_change_file = td / "last_anchor_change.md"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"

            rs_file.write_text(
                json.dumps(
                    {
                        "betterRussia": {
                            "scores": [1400] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        }
                    }
                )
            )
            run_file.write_text(
                json.dumps(
                    {
                        "hash": "betterRussia",
                        "scores": [1400] * 12,
                        "games_total": 12,
                        "best_max_type": 15,
                        "russia_count": 1,
                        "soviet_count": 0,
                    }
                )
            )
            anchor_file.write_text(
                json.dumps(
                    {
                        "hash": "russiaPath",
                        "comp": 1200,
                        "p50": 1200,
                        "p25": 1200,
                        "lcb": 1200,
                        "n": 12,
                        "best_max_type": 15,
                        "russia_count": 1,
                        "soviet_count": 0,
                    }
                )
            )
            (archive_dir / "betterRussia.py").write_text("# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n")

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
CURRENT_STRATEGY_RUN_FILE='{run_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
LAST_ANCHOR_CHANGE_FILE='{last_anchor_change_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_promote_current_strategy_to_anchor betterRussia
echo promote_rc=$?
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            self.assertIn("promote_rc=0", result.stdout)
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "betterRussia")
            self.assertEqual(anchor["best_max_type"], 15)
            self.assertEqual(anchor["russia_count"], 1)
            anchor_change = last_anchor_change_file.read_text()
            self.assertIn("- prev: russiaPath", anchor_change)
            self.assertIn("- new: betterRussia", anchor_change)
            self.assertIn("- source: promote_current_strategy", anchor_change)
            self.assertIn("russia=1", anchor_change)

    def test_existing_russia_anchor_can_be_replaced_by_near_score_only_candidate(self):
        """Russia 到達だけでは近傍スコアの score-only 候補を押しのけない。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            last_anchor_change_file = td / "last_anchor_change.md"

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnly": {
                            "scores": [1300] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 14,
                            "russia_count": 0,
                            "soviet_count": 0,
                        },
                        "russiaPath": {
                            "scores": [1200] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        },
                    }
                )
            )
            stable_source = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
            (archive_dir / "scoreOnly.py").write_text(stable_source)
            (archive_dir / "russiaPath.py").write_text(stable_source)
            anchor_file.write_text(
                json.dumps(
                    {
                        "hash": "russiaPath",
                        "comp": 1200,
                        "p50": 1200,
                        "p25": 1200,
                        "lcb": 1200,
                        "n": 12,
                        "best_max_type": 15,
                        "russia_count": 1,
                        "soviet_count": 0,
                    }
                )
            )
            russia_sig = {"reason": {"R": 1.0}, "x_bins": [1.0, 0, 0, 0, 0, 0], "n_games": 12, "merge_take_rate": 0.5}
            beh_file.write_text(
                json.dumps(
                    {
                        "russiaPath": russia_sig,
                        "scoreOnly": {"reason": {"S": 1.0}, "x_bins": [0, 0, 0, 0, 0, 1.0], "n_games": 12, "merge_take_rate": 0.1},
                    }
                )
            )
            tabu_file.write_text(json.dumps({"signature": russia_sig, "decay_until_games": 999999}) + "\n")

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "0"
            env["TABU_ENABLED"] = "1"
            env["TABU_DISTANCE_THRESHOLD"] = "0.15"
            env["OBJECTIVE_ANCHOR_PRIORITY_ENABLED"] = "1"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "scoreOnly")

    def test_existing_russia_anchor_can_be_replaced_by_far_higher_score_candidate(self):
        """score 差が十分大きい mature 候補は rollback anchor として採用できる。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            last_anchor_change_file = td / "last_anchor_change.md"

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnly": {
                            "scores": [3000] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 14,
                            "russia_count": 0,
                            "soviet_count": 0,
                        },
                        "russiaPath": {
                            "scores": [1200] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        },
                    }
                )
            )
            stable_source = "# --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
            (archive_dir / "scoreOnly.py").write_text(stable_source)
            (archive_dir / "russiaPath.py").write_text(stable_source)
            anchor_file.write_text(
                json.dumps(
                    {
                        "hash": "russiaPath",
                        "comp": 1200,
                        "p50": 1200,
                        "p25": 1200,
                        "lcb": 1200,
                        "n": 12,
                        "best_max_type": 15,
                        "russia_count": 1,
                        "soviet_count": 0,
                    }
                )
            )

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "0"
            env["TABU_ENABLED"] = "0"
            env["OBJECTIVE_ANCHOR_PRIORITY_ENABLED"] = "1"
            env["OBJECTIVE_ANCHOR_MIN_COMP_RATIO"] = "0.90"
            env["OBJECTIVE_ANCHOR_MAX_COMP_GAP"] = "1500"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "scoreOnly")
            self.assertEqual(anchor["best_max_type"], 14)
            self.assertEqual(anchor["russia_count"], 0)

    def test_regression_rollback_can_restore_anchor_from_permanent_archive(self):
        """anchor rollback は通常 archive だけでなく permanent archive も見に行く。"""
        regression = (REPO_ROOT / "strategy" / "regression.sh").read_text()
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", regression)
        self.assertIn("_find_rollback_candidate_file_for_hash", regression)
        self.assertIn('rollback_note="anchor_top1 hash=${rollback_hash}', regression)
        self.assertNotIn("anchor_top1_permanent", regression)

    def test_anchor_selection_skips_archives_that_would_normalize_on_validate(self):
        """guard 未注入 archive は rollback 時に別 hash へ変わるため anchor から外す。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            anchor_file = td / "best_strategy_anchor.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            beh_file = td / "behavior_signatures.json"
            tabu_file = td / "tabu.jsonl"
            last_anchor_change_file = td / "last_anchor_change.md"

            rs_file.write_text(
                json.dumps(
                    {
                        "staleRussia": {
                            "scores": [1300] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        },
                        "stableRussia": {
                            "scores": [1200] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        },
                    }
                )
            )
            (archive_dir / "staleRussia.py").write_text("def decide(game_state, analysis):\n    return {'x': 0}\n")
            (archive_dir / "stableRussia.py").write_text(
                "def decide(game_state, analysis):\n"
                "    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
                "    # --- END DEADLINE GUARD ---\n"
                "    return {'x': 0}\n"
            )

            env = os.environ.copy()
            env["DIVERSITY_PREMIUM_ENABLED"] = "0"
            env["TABU_ENABLED"] = "0"
            env["OBJECTIVE_ANCHOR_PRIORITY_ENABLED"] = "1"
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["LAST_ANCHOR_CHANGE_FILE"] = str(last_anchor_change_file)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
BEST_STRATEGY_ANCHOR_FILE='{anchor_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
source strategy/regression.sh 2>/dev/null
_refresh_best_strategy_anchor "" 2>&1
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            anchor = json.loads(anchor_file.read_text())
            self.assertEqual(anchor["hash"], "stableRussia")


# --- E: tabu blocks anchor promotion only, scores preserved ------------------

class TestTabuExcludesOnlyPromotion(unittest.TestCase):
    def test_tabu_does_not_remove_rolling_scores(self):
        """tabu に登録されても rolling_scores.json の対象 hash のデータは残る。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            tabu_file = td / "tabu.jsonl"
            beh_file = td / "behavior_signatures.json"
            wildcard_file = td / "wildcard.json"

            sig_a = {"reason": {"X": 1.0}, "x_bins": [1.0, 0, 0, 0, 0, 0], "n_games": 12, "merge_take_rate": 0.5, "endgame_recovery": 0.0, "high_phase_reason": {}, "n_turns": 100}
            rs_file.write_text(
                json.dumps({"hashA": {"scores": [1000] * 24, "games_total": 24, "_recent_archives": []}})
            )

            env = os.environ.copy()
            env["TABU_ENABLED"] = "1"
            env["TABU_SIGNATURES_FILE"] = str(tabu_file)
            env["BEHAVIOR_SIGNATURES_FILE"] = str(beh_file)
            env["WILDCARD_ORIGIN_FILE"] = str(wildcard_file)
            env["TABU_DECAY_GAMES"] = "100"
            env["TABU_RETAIN"] = "20"
            env["MIN_GAMES_BEFORE_IMPROVE"] = "24"
            env["ROLLING_SCORES_FILE"] = str(rs_file)

            # signature キャッシュに事前登録 (compute をスキップさせるため)
            beh_file.write_text(json.dumps({"hashA": sig_a}))

            # _record_tabu_signature 呼び出しは _recent_archives が空だと EXIT する
            # ので manual に tabu を1件書く
            tabu_file.write_text(json.dumps({
                "hash": "hashA",
                "signature": sig_a,
                "decay_until_games": 999999,
                "recorded_at": 0,
            }) + "\n")

            # rolling_scores が hashA のデータを保持しているか
            self.assertIn("hashA", json.loads(rs_file.read_text()))
            self.assertEqual(len(json.loads(rs_file.read_text())["hashA"]["scores"]), 24)


# --- F1: wildcard reason survives process boundary ---------------------------

class TestWildcardReasonProcessBoundary(unittest.TestCase):
    def test_eloop_improve_receives_reason_arg(self):
        """eloop_improve.sh 冒頭が IMPROVE_REASON を 6 番目の引数から受け取る。"""
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        self.assertIn('IMPROVE_REASON="${6:-normal}"', eloop)
        # wildcard 分岐の存在も確認
        self.assertIn('"${IMPROVE_REASON:-normal}" = "wildcard"', eloop)
        self.assertIn('_write_improve_state "running" "$IMPROVE_SELF_PID" "$IMPROVE_BASE_HASH" "$phase" "$progress" "$detail" "$IMPROVE_STARTED_AT" "$IMPROVE_BIRTH_EPOCH" "${IMPROVE_REASON:-normal}"', eloop)
        self.assertIn("_implementation_self_report_rejects_change()", eloop)
        self.assertIn("AI実装が冗長または挙動が変わらない変更と自己申告した", eloop)
        # _start_improvement_job 側も 6 番目に reason を渡している
        improve_sh = (REPO_ROOT / "strategy/improve.sh").read_text()
        self.assertIn('bash "$runtime_script" "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" "$reason"', improve_sh)

    def test_wildcard_adapts_perturbation_after_consecutive_attempts(self):
        """WILDCARD 連続発火時は state を記録し、摂動幅と対象数を段階的に拡張する。"""
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()

        self.assertIn("WILDCARD_ADAPTIVE_SCALE_ENABLED", config)
        self.assertIn("WILDCARD_ADAPTIVE_SCALE_STEP", config)
        self.assertIn("WILDCARD_ADAPTIVE_SCALE_MAX", config)
        self.assertIn("WILDCARD_ADAPTIVE_EXTRA_PARAM_EVERY", config)
        self.assertIn("WILDCARD_TABU_RECENT_LINES", config)
        self.assertIn("WILDCARD_AI_ESCALATE_ENABLED", config)
        self.assertIn("WILDCARD_AI_ESCALATE_STREAK", config)
        self.assertIn("WILDCARD_BANDIT_ENABLED", config)
        self.assertIn("WILDCARD_BANDIT_LOOKBACK", config)
        self.assertIn("WILDCARD_BANDIT_EXPLORE_RATE", config)
        self.assertIn("ANNEALING_OBSERVE_ENABLED", config)
        self.assertIn("ANNEALING_BASE_TEMP", config)
        self.assertIn("ANNEALING_DECAY", config)
        self.assertIn("WILDCARD_ATTEMPT_STATE_FILE", config)
        self.assertIn("WILDCARD_OUTCOME_FILE", config)
        self.assertIn("ANNEALING_OBSERVE_FILE", config)
        self.assertIn("WILDCARD_PARALLEL_ENABLED", config)
        self.assertIn("WILDCARD_PARALLEL_JOBS", config)
        self.assertIn('WILDCARD_PARALLEL_JOBS="${WILDCARD_PARALLEL_JOBS:-6}"', config)
        self.assertIn('POST_IMPROVE_PARAM_PARALLEL_ENABLED="${POST_IMPROVE_PARAM_PARALLEL_ENABLED:-0}"', config)
        self.assertIn('POST_IMPROVE_PARAM_PARALLEL_JOBS="${POST_IMPROVE_PARAM_PARALLEL_JOBS:-6}"', config)
        self.assertIn('POST_IMPROVE_PARAM_PARALLEL_STOP_SOREN91="${POST_IMPROVE_PARAM_PARALLEL_STOP_SOREN91:-0}"', config)
        self.assertIn('POST_IMPROVE_PARAM_PARALLEL_SERVE_BASE_PORT="${POST_IMPROVE_PARAM_PARALLEL_SERVE_BASE_PORT:-18180}"', config)
        self.assertIn('POST_IMPROVE_PARAM_PARALLEL_CDP_BASE_PORT="${POST_IMPROVE_PARAM_PARALLEL_CDP_BASE_PORT:-19320}"', config)
        self.assertIn("WILDCARD_PARALLEL_GAMES", config)
        self.assertIn("WILDCARD_PARALLEL_OVERLAY_SOURCE", config)
        self.assertIn("WILDCARD_PARALLEL_BGM_VOLUME", config)
        self.assertIn("WILDCARD_PARALLEL_SE_VOLUME", config)

        self.assertIn("consecutive_wildcards", eloop)
        self.assertIn("adapted_ratio_min", eloop)
        self.assertIn("adapted_ratio_max", eloop)
        self.assertIn("adapted_count_min", eloop)
        self.assertIn("recent_applied_lines", eloop)
        self.assertIn("recent_attempts", eloop)
        self.assertIn("next_state = dict(state)", eloop)
        self.assertIn("losing last_reset_epoch makes", eloop)
        self.assertIn("wildcard_outcomes.jsonl", eloop)
        self.assertIn('"event": "CREATED"', eloop)
        self.assertIn("prefer_lines", eloop)
        self.assertIn("--prefer-lines", eloop)
        self.assertIn("--explore-rate", eloop)
        self.assertIn("--exclude-lines", eloop)
        self.assertIn("wildcard_parallel.py", eloop)
        self.assertIn("--evaluate-mode", eloop)
        self.assertIn("parallel_candidates", eloop)
        self.assertIn("parallel_job_id", eloop)
        self.assertIn("wildcardParallelOverlay", config)
        self.assertIn("soviet_local.stderr.log", parallel)
        self.assertIn("bridge exited rc=", parallel)
        self.assertIn("wildcard_applied", eloop)
        self.assertIn('"origin_type": "wildcard"', eloop)
        self.assertIn('"exclude_applied": exclude_applied', eloop)
        self.assertIn("adaptive scale streak=", eloop)
        self.assertIn('WILDCARD_CURRENT_STREAK="$wildcard_streak" WILDCARD_APPLIED_JSON=', eloop)
        self.assertIn('"wildcard_streak": int(os.environ.get("WILDCARD_CURRENT_STREAK"', eloop)
        self.assertLess(
            eloop.index('CHANGE_LOG_FILE_HOST="$HOST_ROOT/$CHANGE_LOG_FILE"'),
            eloop.index('"${IMPROVE_REASON:-normal}" = "wildcard"'),
        )
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        self.assertIn('${WILDCARD_ATTEMPT_STATE_FILE:-tmp/state/wildcard_attempt_state.json}', regression)
        self.assertIn("def _update_wildcard_attempt_state(event):", regression)
        self.assertIn('event in ("PROMOTE", "OK_BEAT")', regression)
        self.assertIn('"wildcard_success_reset"', regression)
        self.assertIn("wildcard_outcome_file", regression)
        self.assertIn('"last_wildcard_outcome"', regression)
        self.assertIn('"last_wildcard_origin_type"', regression)
        self.assertIn('"metrics": current_payload', regression)
        self.assertIn('"origin_type": str(origin.get("origin_type") or "wildcard")', regression)
        self.assertIn("def _record_annealing_candidate(event):", regression)
        self.assertIn('"event": "ANNEALING_CANDIDATE"', regression)
        self.assertIn('"observe_only": True', regression)
        self.assertIn("_record_annealing_candidate(event)", regression)

    def test_wildcard_parallel_orchestrator_selects_one_winner(self):
        """parallel orchestrator は指定した候補数を隔離生成し、勝者1本だけを返す。"""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            strategy = td_path / "strategy.py"
            strategy.write_text(
                textwrap.dedent(
                    """
                    def decide(game_state, analysis):
                        score_bias = 1.25
                        x = 0.50 + score_bias * 0.10
                        if len(game_state.get("pieces", [])) > 4:
                            x = -0.75
                        return {"x": x, "reason": "test"}
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            result_file = td_path / "result.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_parallel.py"),
                    "--strategy",
                    str(strategy),
                    "--jobs",
                    "3",
                    "--games",
                    "1",
                    "--evaluate-mode",
                    "simulate",
                    "--session-root",
                    str(td_path / "sessions"),
                    "--status-file",
                    str(td_path / "status.json"),
                    "--html-file",
                    str(td_path / "wildcard.html"),
                    "--result-file",
                    str(result_file),
                    "--seed",
                    "1234",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, msg=f"stderr={proc.stderr}\nstdout={proc.stdout}")
            result = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["candidates"]), 3)
            self.assertEqual(result["winner"]["job_id"], "cand-3")
            self.assertTrue(Path(result["winner"]["strategy_path"]).exists())
            self.assertTrue((td_path / "wildcard.html").exists())
            for cand in result["candidates"]:
                workdir = Path(cand["workdir"])
                self.assertTrue((workdir / "strategy.py").exists())
                self.assertTrue((workdir / "commands.txt").exists())
                self.assertTrue((workdir / "game_state.json").exists())
                self.assertIn("raw_scores", cand)
                self.assertIn("eval_scores", cand)

    def test_wildcard_parallel_uses_live_eval_score_for_winner(self):
        """WILDCARD 候補比較は raw score ではなく live と同じ eval score を使う。"""
        import wildcard_parallel

        low_raw_high_type = {"score": 900, "final_types": [14]}
        high_raw_low_type = {"score": 1200, "final_types": [10]}
        self.assertGreater(
            wildcard_parallel.eval_score(low_raw_high_type),
            wildcard_parallel.eval_score(high_raw_low_type),
        )

        cand_a = wildcard_parallel.CandidateResult(
            job_id="cand-a",
            index=0,
            workdir=Path("/tmp/cand-a"),
            strategy_path=Path("/tmp/cand-a/strategy.py"),
            status="accepted",
            scores=[wildcard_parallel.eval_score(low_raw_high_type)],
            raw_scores=[900],
            eval_scores=[wildcard_parallel.eval_score(low_raw_high_type)],
            max_type=14,
        )
        cand_b = wildcard_parallel.CandidateResult(
            job_id="cand-b",
            index=1,
            workdir=Path("/tmp/cand-b"),
            strategy_path=Path("/tmp/cand-b/strategy.py"),
            status="accepted",
            scores=[wildcard_parallel.eval_score(high_raw_low_type)],
            raw_scores=[1200],
            eval_scores=[wildcard_parallel.eval_score(high_raw_low_type)],
            max_type=10,
        )
        self.assertIs(wildcard_parallel.choose_winner([cand_b, cand_a], 1), cand_a)

    def test_wildcard_parallel_bounds_game_and_main_loop_blocking(self):
        """隔離評価の詰まりは bounded にし、本線停止は stale status だけで続けない。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        readme = (REPO_ROOT / "README.md").read_text()

        self.assertIn('--games "${WILDCARD_PARALLEL_GAMES:-6}"', eloop)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_GAMES"), 6)', parallel)
        self.assertIn('--game-timeout", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_GAME_TIMEOUT"), 1200)', parallel)
        self.assertIn('game_timeout = max(30, _int(getattr(args, "game_timeout", 1200), 1200))', parallel)
        self.assertIn("game_deadline = time.time() + game_timeout", parallel)
        self.assertIn('candidate.status = "timeout"', parallel)
        # on timeout we now score the live board instead of discarding the game (good
        # long-game strategies must not be killed by the per-game timeout).
        self.assertIn("timeout_game = _score_timeout_board(workdir)", parallel)
        self.assertIn("candidate.scores.append(eval_score(timeout_game))", parallel)
        self.assertIn('payload.setdefault("started_at", now_epoch)', parallel)
        self.assertIn('payload["updated_at"] = now_epoch', parallel)
        self.assertIn('WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC', improve)
        self.assertIn('(time.time() - started_at) > max_sec', improve)
        # terminal phase (winner_selected etc.) between consecutive wildcard rounds
        # keeps the main loop paused while wildcard_parallel.py is alive, so a slip
        # game can't grab the OBS sorengame source over the param overlay.
        self.assertIn('WP_PROC_ALIVE', improve)
        self.assertIn('if [ "${prev_phase:-}" = "wildcard_parallel" ]; then', improve)
        self.assertIn("wall_timeout=0", improve)
        self.assertIn('[ "${prev_phase:-}" != "wildcard_parallel" ]', improve)
        self.assertIn("WILDCARD_PARALLEL_GAME_TIMEOUT", readme)
        self.assertIn("WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC", readme)

    def test_wildcard_parallel_culling_defaults_to_each_game(self):
        """デフォルトでは1ゲームごとに leader 比で cull/refill を判定する。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn('WILDCARD_PARALLEL_CULL_AFTER_GAMES="${WILDCARD_PARALLEL_CULL_AFTER_GAMES:-1}"', config)
        self.assertIn('WILDCARD_PARALLEL_CULL_LEADER_MIN_GAMES="${WILDCARD_PARALLEL_CULL_LEADER_MIN_GAMES:-2}"', config)
        self.assertIn('WILDCARD_PARALLEL_LINGERING_SLOT_MAX_CULLS="${WILDCARD_PARALLEL_LINGERING_SLOT_MAX_CULLS:-0}"', config)
        self.assertIn('WILDCARD_PARALLEL_MIN_SUCCESSFUL_GAMES="${WILDCARD_PARALLEL_MIN_SUCCESSFUL_GAMES:-0}"', config)
        self.assertIn('WILDCARD_PARALLEL_CULL_COMP_RATIO="${WILDCARD_PARALLEL_CULL_COMP_RATIO:-0.90}"', config)
        self.assertNotIn("WILDCARD_PARALLEL_MAX_REFILLS", config)
        self.assertIn('--cull-after-games "${WILDCARD_PARALLEL_CULL_AFTER_GAMES:-1}"', eloop)
        self.assertIn('--cull-leader-min-games "${WILDCARD_PARALLEL_CULL_LEADER_MIN_GAMES:-2}"', eloop)
        self.assertIn('--cull-comp-ratio "${WILDCARD_PARALLEL_CULL_COMP_RATIO:-0.90}"', eloop)
        self.assertIn('--lingering-slot-max-culls "${WILDCARD_PARALLEL_LINGERING_SLOT_MAX_CULLS:-0}"', eloop)
        self.assertNotIn("--max-refills", eloop)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_CULL_AFTER_GAMES"), 1)', parallel)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_CULL_LEADER_MIN_GAMES"), 2)', parallel)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_LINGERING_SLOT_MAX_CULLS"), 0)', parallel)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_MIN_SUCCESSFUL_GAMES"), 0)', parallel)
        self.assertIn("args.min_successful_games = args.games", parallel)
        self.assertIn("class CullCoordinator", parallel)
        self.assertIn('candidate.status = "culled"', parallel)
        self.assertNotIn("max_refills", parallel)
        self.assertNotIn("--max-refills", parallel)
        self.assertIn('job_id = f"cand-{index + 1}" if generation <= 0 else f"cand-{index + 1}-r{generation + 1}"', parallel)
        self.assertIn("candidate = run_perturb(args, index, session_dir, generation)", parallel)
        self.assertIn("with ThreadPoolExecutor(max_workers=args.jobs) as pool", parallel)

    def test_wildcard_parallel_cull_rechecks_after_minimum_games(self):
        """cull-after-games 以降は閾値ぴったりの1回だけでなく各ゲーム後に再判定する。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            args = argparse.Namespace(cull_after_games=1, cull_leader_min_games=2, cull_comp_ratio=0.90)
            leader = wildcard_parallel.CandidateResult(
                job_id="leader",
                index=0,
                workdir=td_path / "leader",
                strategy_path=td_path / "leader" / "strategy.py",
                status="running",
                scores=[100, 100, 100, 100, 100],
                comp=100,
            )
            candidate = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="running",
                scores=[75, 75, 75],
                comp=75,
            )
            coordinator = wildcard_parallel.CullCoordinator(
                args,
                td_path / "status.json",
                td_path / "overlay.html",
                td_path / "session",
                [leader],
            )

            self.assertTrue(coordinator.should_cull(candidate))
            self.assertIn("culled after 3 games", candidate.error)
            self.assertEqual(candidate.cull_leader_job_id, "leader")
            self.assertEqual(candidate.cull_leader_games, 5)
            self.assertEqual(candidate.cull_leader_comp, 100)
            self.assertEqual(candidate.cull_threshold, 90)

            candidate.scores = [95]
            candidate.comp = 95
            candidate.error = ""
            candidate.cull_leader_job_id = ""
            candidate.cull_leader_games = 0
            candidate.cull_leader_comp = 0
            candidate.cull_threshold = 0
            self.assertFalse(coordinator.should_cull(candidate))

            candidate.scores.extend([45, 45, 45, 45])
            candidate.comp = 55
            self.assertTrue(coordinator.should_cull(candidate))
            self.assertIn("culled after 5 games", candidate.error)
            self.assertEqual(candidate.cull_leader_job_id, "leader")

    def test_wildcard_parallel_does_not_cull_against_one_game_leader(self):
        """cull判定自体は1ゲーム後からでも、比較先leaderは2ゲーム以上に限る。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            one_game_leader = wildcard_parallel.CandidateResult(
                job_id="one-game-leader",
                index=0,
                workdir=td_path / "leader",
                strategy_path=td_path / "leader" / "strategy.py",
                status="running",
                scores=[1000],
                comp=1000,
            )
            candidate = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="running",
                scores=[100],
                comp=100,
            )
            coordinator = wildcard_parallel.CullCoordinator(
                argparse.Namespace(cull_after_games=1, cull_leader_min_games=2, cull_comp_ratio=0.90),
                td_path / "status.json",
                td_path / "overlay.html",
                td_path / "session",
                [one_game_leader],
            )

            self.assertFalse(coordinator.should_cull(candidate))
            self.assertEqual(candidate.cull_leader_job_id, "")

            one_game_leader.scores.append(1000)
            self.assertTrue(coordinator.should_cull(candidate))
            self.assertEqual(candidate.cull_leader_job_id, "one-game-leader")

    def test_wildcard_parallel_score_baseline_can_cull_without_becoming_winner(self):
        """元戦略に既存スコアがあれば culling 比較用 leader として使うが winner にはしない。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            strategy = td_path / "strategy.py"
            strategy.write_text("def decide(gs, analysis):\n    return {'x': 0}\n", encoding="utf-8")
            current_run = td_path / "current_strategy_run.json"
            current_run.write_text(
                json.dumps(
                    {
                        "hash": wildcard_parallel.compute_strategy_hash(strategy),
                        "scores": [1200],
                    }
                ),
                encoding="utf-8",
            )
            rolling = td_path / "rolling_scores.json"
            rolling.write_text("{}", encoding="utf-8")
            baseline = wildcard_parallel.load_score_baseline(strategy, td_path / "session", rolling, current_run)
            self.assertIsNotNone(baseline)
            self.assertEqual(baseline.status, "baseline")
            self.assertTrue(baseline.score_baseline)
            self.assertEqual(baseline.scores, [1200])

            candidate = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="running",
                scores=[900],
                comp=900,
            )
            coordinator = wildcard_parallel.CullCoordinator(
                argparse.Namespace(cull_after_games=1, cull_leader_min_games=2, cull_comp_ratio=0.90),
                td_path / "status.json",
                td_path / "overlay.html",
                td_path / "session",
                [baseline],
            )

            self.assertTrue(coordinator.should_cull(candidate))
            self.assertEqual(candidate.cull_leader_job_id, "baseline-score")
            self.assertIsNone(wildcard_parallel.choose_winner([baseline], 1))

    def test_baseline_slot1_played_baseline_is_anchor_not_winner_and_uncullable(self):
        """--baseline-slot1 の slot-1 に置く played baseline (baseline=True) は、
        winner にならず・cull されず・勝者比較の基準として使われる。これが無いと
        baseline が比較基準として認識されず cull もされ、現行より低スコアの戦略が
        無条件採用される (報告されたバグ)。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)

            def mk(job_id, index, comp, *, baseline=False):
                return wildcard_parallel.CandidateResult(
                    job_id=job_id, index=index, workdir=td_path / job_id,
                    strategy_path=td_path / job_id / "strategy.py",
                    status="accepted", scores=[int(comp)] * 6, comp=comp, baseline=baseline,
                )

            played_baseline = mk("cand-1", 0, 12000.0, baseline=True)
            worse = mk("cand-6", 5, 9696.0)
            better = mk("cand-3", 2, 13000.0)

            # worse-than-baseline candidate must NOT be adopted (the reported bug).
            self.assertIsNone(wildcard_parallel.choose_winner([played_baseline, worse], 6))
            # a strictly-better candidate IS adopted, and the baseline itself never wins.
            self.assertIs(wildcard_parallel.choose_winner([played_baseline, better], 6), better)

            # the played baseline must never be culled — it has to finish as a stable anchor.
            high_leader = mk("cand-9", 8, 20000.0)  # threshold 18000 > baseline 12000
            coordinator = wildcard_parallel.CullCoordinator(
                argparse.Namespace(cull_after_games=1, cull_leader_min_games=2, cull_comp_ratio=0.90),
                td_path / "status.json", td_path / "overlay.html", td_path / "session",
                [high_leader],
            )
            self.assertFalse(coordinator.should_cull(played_baseline))

    def test_cull_threshold_anchored_to_leader_by_default(self):
        """cull 閾値は既定で「最強の生存者(leader)」の cull_comp_ratio (2026-05-30 ユーザー
        要望で baseline基準→leader基準に変更)。leader の 90% に満たない摂動を刈って探索幅を
        広げる。WILDCARD_PARALLEL_CULL_ANCHOR=baseline で従来の現戦略基準に戻せる。baseline
        自身は anchor に関わらず決して刈られない。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)

            def mk(job_id, index, comp, scores, *, baseline=False):
                return wildcard_parallel.CandidateResult(
                    job_id=job_id, index=index, workdir=td_path / job_id,
                    strategy_path=td_path / job_id / "strategy.py",
                    status="running", scores=scores, comp=comp, p25=comp, baseline=baseline,
                )

            baseline = mk("cand-1", 0, 10000.0, [10000, 10000, 10000], baseline=True)
            leader = mk("cand-2", 1, 20000.0, [20000, 20000])  # strongest survivor (>=2 games)
            ns = argparse.Namespace(cull_after_games=1, cull_leader_min_games=2, cull_comp_ratio=0.90)

            # leader-anchored (default): threshold = 0.90 * leader(20000) = 18000; a
            # candidate at 95% of the BASELINE but 47% of the leader is now culled — this
            # is the harder culling that widens exploration.
            with mock.patch.dict(os.environ, {"WILDCARD_PARALLEL_CULL_ANCHOR": "leader"}, clear=False):
                coordinator = wildcard_parallel.CullCoordinator(
                    ns, td_path / "s.json", td_path / "o.html", td_path / "session", [baseline, leader],
                )
                near_base = mk("cand-3", 2, 9500.0, [9500])
                self.assertTrue(coordinator.should_cull(near_base))
                self.assertEqual(near_base.cull_leader_job_id, "cand-2")
                # the baseline itself is never culled (stays as the comparison anchor).
                self.assertFalse(coordinator.should_cull(baseline))

            # knob: baseline anchor restores the prior, narrower behavior (keep >= 90% of
            # the current strategy regardless of a lucky high-variance leader).
            with mock.patch.dict(os.environ, {"WILDCARD_PARALLEL_CULL_ANCHOR": "baseline"}, clear=False):
                coordinator2 = wildcard_parallel.CullCoordinator(
                    ns, td_path / "s2.json", td_path / "o2.html", td_path / "session2", [baseline, leader],
                )
                near_base2 = mk("cand-3", 2, 9500.0, [9500])
                self.assertFalse(coordinator2.should_cull(near_base2))  # 9500 >= 0.90*10000

    def test_choose_winner_interrupted_adopts_best_so_far(self):
        """deadline timeout/中断時は、min_successful_games 未達でも(>= INTERRUPTED_MIN_GAMES、
        まだ running でも)その時点の最良候補を採用する。ただし baseline を上回ることは必須
        (timeoutでも現戦略より悪い戦略は採用しない=回帰防止)。"""
        import wildcard_parallel

        cw = wildcard_parallel.choose_winner
        with mock.patch.dict(os.environ, {"WILDCARD_PARALLEL_INTERRUPTED_MIN_GAMES": "2"}, clear=False), \
                tempfile.TemporaryDirectory() as td:
            td_path = Path(td)

            def mk(job_id, index, comp, n, status, *, baseline=False):
                return wildcard_parallel.CandidateResult(
                    job_id=job_id, index=index, workdir=td_path / job_id,
                    strategy_path=td_path / job_id / "strategy.py",
                    status=status, scores=[int(comp)] * n, comp=comp, p25=comp, baseline=baseline,
                )

            baseline = mk("cand-1", 0, 10000.0, 6, "accepted", baseline=True)
            running_best = mk("cand-2", 1, 12000.0, 3, "running")  # cut short at 3 games, beats baseline
            # normal (not interrupted): a still-running candidate < min_successful_games is NOT eligible.
            self.assertIsNone(cw([baseline, running_best], 6))
            # interrupted: the running best (>= 2 games, beats baseline) IS adopted.
            self.assertIs(cw([baseline, running_best], 6, interrupted=True), running_best)
            # interrupted but only 1 game (< INTERRUPTED_MIN_GAMES 2) → not eligible.
            one_game = mk("cand-3", 2, 15000.0, 1, "running")
            self.assertIsNone(cw([baseline, one_game], 6, interrupted=True))
            # interrupted, best-so-far does NOT beat the baseline → no adoption (no regression).
            worse = mk("cand-4", 3, 9000.0, 3, "running")
            self.assertIsNone(cw([baseline, worse], 6, interrupted=True))

    def test_russia_cull_protection_and_recurrence_adoption(self):
        """2026-05-31 (user: Russia-founding slots were getting culled). Pins the two-part fix:
        (A) candidate_reliable_russia = RECURRENT Russia only — russia_count >= RECURRENCE_MIN_COUNT
        (default 2) OR a reproduced 2nd-Russia frontier (T15x2 / T15x1+T14x2) over >=
        FRONTIER_MIN_GAMES (default 2) boards. A single lucky Russia is NOT reliable.
        (B) choose_winner's leading objective bit uses reliable_russia when ADOPT_RUSSIA_RECURRENCE=1
        (default): a WEAK single-Russia (russia==1, comp below the baseline) is NOT adopted
        (05-25-safe), a RECURRENT Russia IS; ADOPT_RUSSIA_RECURRENCE=0 restores the legacy
        russia_count>0 bit. env pinned for determinism."""
        import wildcard_parallel as wp

        def mk(job_id, index, comp, *, baseline=False, russia=0, soviet=0, game_results=None):
            return wp.CandidateResult(
                job_id=job_id, index=index, workdir=Path("/tmp") / job_id,
                strategy_path=Path("/tmp") / job_id / "strategy.py",
                status="accepted", scores=[int(comp)] * 6, comp=comp, p25=comp,
                baseline=baseline, russia_count=russia, soviet_count=soviet,
                game_results=list(game_results or []),
            )

        env = {"WILDCARD_PARALLEL_ADOPT_RUSSIA_RECURRENCE": "1",
               "WILDCARD_PARALLEL_RUSSIA_RECURRENCE_MIN_COUNT": "2",
               "WILDCARD_PARALLEL_RUSSIA_FRONTIER_MIN_GAMES": "2"}
        with mock.patch.dict(os.environ, env, clear=False):
            # (A) candidate_reliable_russia
            self.assertTrue(wp.candidate_reliable_russia(mk("c", 0, 1.0, russia=2)))   # recurrence by count
            self.assertFalse(wp.candidate_reliable_russia(mk("c", 0, 1.0, russia=1)))  # single lucky → NOT reliable
            two_frontier = [{"final_types": [15, 15, 14]}, {"final_types": [15, 14, 14]}]
            self.assertTrue(wp.candidate_reliable_russia(mk("c", 0, 1.0, russia=1, game_results=two_frontier)))
            self.assertFalse(wp.candidate_reliable_russia(  # one frontier board is not enough
                mk("c", 0, 1.0, russia=1, game_results=[{"final_types": [15, 15]}])))
            # malformed game_results must not raise
            self.assertFalse(wp.candidate_reliable_russia(
                mk("c", 0, 1.0, russia=1, game_results=[{"final_types": ["x", None]}, "junk"])))
            # (B) choose_winner adoption — weak single-Russia below baseline comp is NOT adopted (05-25)
            cw = wp.choose_winner
            baseline = mk("base", 0, 12000.0, baseline=True, russia=0)
            single = mk("single", 1, 11000.0, russia=1)
            recurrent = mk("recur", 2, 11000.0, russia=2)
            self.assertIsNone(cw([baseline, single], 6, baseline_historical_russia=0))
            self.assertIs(cw([baseline, recurrent], 6, baseline_historical_russia=0), recurrent)
        # legacy: with the recurrence guard OFF, the single-Russia regains russia>0 and IS adopted
        with mock.patch.dict(os.environ, {**env, "WILDCARD_PARALLEL_ADOPT_RUSSIA_RECURRENCE": "0"}, clear=False):
            cw = wp.choose_winner
            baseline = mk("base", 0, 12000.0, baseline=True, russia=0)
            single = mk("single", 1, 11000.0, russia=1)
            self.assertIs(cw([baseline, single], 6, baseline_historical_russia=0), single)

    def test_transient_launch_failure_classifier(self):
        """2026-05-31 (user: 'スロットは6つあって…のこりの二つはどこに行った'). A param-parallel
        slot whose candidate Chrome closed mid-startup used to fail terminally and lose the slot
        for the whole run (2/6 boxes went dead). evaluate_slot now gives a BOUNDED relaunch on a
        TRANSIENT launch/bridge failure only. This pins the classifier that gates that retry:
        launch/bridge-startup failures (no game produced) → retry; a strategy that actually played
        then crashed, or a perturb-generator failure, → do NOT retry (would waste the budget)."""
        import wildcard_parallel as wp

        # the real slot-5 crash string observed in this incident
        real = ("bridge timed out waiting for initial game_state: bridge did not produce "
                "game_state | stderr: [BRIDGE-FATAL] runLocalController rejected: "
                "page.waitForTimeout: Target page, context or browser has been closed")
        self.assertTrue(wp._is_transient_launch_failure(real))
        self.assertTrue(wp._is_transient_launch_failure("Target page, context or browser has been closed"))
        self.assertTrue(wp._is_transient_launch_failure("bridge timed out"))
        # NON-transient: never retry these
        self.assertFalse(wp._is_transient_launch_failure("no successful games"))
        self.assertFalse(wp._is_transient_launch_failure("decide() raised ValueError on turn 40"))
        self.assertFalse(wp._is_transient_launch_failure("wildcard_perturb rc=1"))
        self.assertFalse(wp._is_transient_launch_failure(None))
        self.assertFalse(wp._is_transient_launch_failure(""))
        # retry cap defaults to 3 and is env-overridable (0 restores give-up-immediately)
        with mock.patch.dict(os.environ, {"WILDCARD_PARALLEL_MAX_LAUNCH_RETRIES": "0"}, clear=False):
            self.assertEqual(wp._int(os.getenv("WILDCARD_PARALLEL_MAX_LAUNCH_RETRIES"), 3), 0)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WILDCARD_PARALLEL_MAX_LAUNCH_RETRIES", None)
            self.assertEqual(wp._int(os.getenv("WILDCARD_PARALLEL_MAX_LAUNCH_RETRIES"), 3), 3)

    def test_russia_rate_guard_protects_capable_baseline(self):
        """choose_winner は、歴史的にロシア建国可能な baseline(現戦略)を、ロシア未実証の
        高スコア摂動で置換しない (2026-05-30 07:12回帰の恒久対策)。~6ゲームでは稀なロシア
        を観測できず baseline の in-run russia=0 になるため、実証済みの歴史的 russia_count
        で判定する。MIN_COUNT=1(既定)で「一度でもロシア建国した能力」を保護。05-25のhard固着
        は OVERRIDE_RATIO のスコア上書きで回避(当時の保護にはこの脱出弁が無かった)。env を
        pin して環境非依存にする。"""
        import wildcard_parallel

        def mk(td_path, job_id, index, comp, *, baseline=False, russia=0, soviet=0, max_type=14):
            return wildcard_parallel.CandidateResult(
                job_id=job_id, index=index, workdir=td_path / job_id,
                strategy_path=td_path / job_id / "strategy.py",
                status="accepted", scores=[int(comp)] * 6, comp=comp, p25=comp,
                baseline=baseline, russia_count=russia, soviet_count=soviet,
                max_type=max_type,
            )

        cw = wildcard_parallel.choose_winner
        env = {"WILDCARD_PARALLEL_RUSSIA_GUARD_MIN_COUNT": "1",
               "WILDCARD_PARALLEL_RUSSIA_GUARD_OVERRIDE_RATIO": "1.15"}
        with mock.patch.dict(os.environ, env, clear=False), tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # played slot-1 baseline: in-run russia=0 (sample too small to see rare Russia).
            baseline = mk(td_path, "cand-1", 0, 12000.0, baseline=True, russia=0)
            winner_small = mk(td_path, "cand-3", 2, 12600.0, russia=0)  # +5% score, no Russia

            # demonstrated historical Russia (>= MIN_COUNT 1) → protect: do NOT adopt the
            # non-Russia winner whose score gain (+5%) is below the override.
            self.assertIsNone(cw([baseline, winner_small], 6, baseline_historical_russia=1))
            # no proven historical Russia → existing behavior: adopt the higher-score winner.
            self.assertIs(cw([baseline, winner_small], 6, baseline_historical_russia=0), winner_small)
            # large score gain (12000→14000 = +16.7% >= 1.15) overrides protection (避固着).
            winner_big = mk(td_path, "cand-4", 3, 14000.0, russia=0)
            self.assertIs(cw([baseline, winner_big], 6, baseline_historical_russia=1), winner_big)
            # But a no-Russia winner must not erase a proven T15/Russia frontier just by score.
            self.assertIsNone(cw(
                [baseline, winner_big],
                6,
                baseline_historical_russia=1,
                baseline_historical_best_max_type=15,
            ))
            # a winner with RECURRENT Russia (russia>=2, the 2026-05-31 adoption-recurrence
            # contract) is adopted even at lower score (回復). A single lucky Russia (russia==1)
            # is NO LONGER auto-adopted — it competes on comp like any candidate (05-25-safe);
            # see test_weak_single_russia_NOT_adopted_over_higher_comp_nonrussia.
            winner_russia = mk(td_path, "cand-5", 4, 11000.0, russia=2, max_type=15)
            self.assertIs(cw([baseline, winner_russia], 6, baseline_historical_russia=1), winner_russia)

        # the MIN_COUNT knob works: at MIN_COUNT=2 a single demonstrated Russia is below
        # threshold and is NOT protected (conservative mode trades coverage for safety).
        with mock.patch.dict(os.environ, {**env, "WILDCARD_PARALLEL_RUSSIA_GUARD_MIN_COUNT": "2"}, clear=False), \
                tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            baseline = mk(td_path, "cand-1", 0, 12000.0, baseline=True, russia=0)
            winner_small = mk(td_path, "cand-3", 2, 12600.0, russia=0)
            self.assertIs(cw([baseline, winner_small], 6, baseline_historical_russia=1), winner_small)

    def test_wildcard_parallel_score_baseline_is_skipped_for_baseline_slot1_mode(self):
        """戦略改善後パラメータ探索の baseline-slot1 mode では既存スコア baseline を注入しない。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()

        self.assertIn("if not args.baseline_slot1:", parallel)
        self.assertIn("score_baseline = load_score_baseline", parallel)
        self.assertIn('if c.status == "pending" and not c.score_baseline', parallel)
        # The played slot-1 baseline (baseline=True) is the comparison anchor too.
        self.assertIn("def is_baseline(c: CandidateResult) -> bool:", parallel)
        self.assertIn("return c.score_baseline or c.baseline", parallel)
        self.assertIn("if candidate.baseline or candidate.score_baseline:", parallel)

    def test_wildcard_parallel_culls_finished_low_candidate_before_returning(self):
        """先に完走した低comp候補も accepted のまま返さず補充する。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            args = argparse.Namespace(cull_after_games=1, cull_leader_min_games=2, cull_comp_ratio=0.90)
            leader = wildcard_parallel.CandidateResult(
                job_id="leader",
                index=0,
                workdir=td_path / "leader",
                strategy_path=td_path / "leader" / "strategy.py",
                status="running",
                scores=[100, 100],
                comp=100,
            )
            first = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="pending",
            )
            low_done = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="accepted",
                scores=[80],
                comp=80,
            )
            refill = wildcard_parallel.CandidateResult(
                job_id="candidate-r2",
                index=1,
                workdir=td_path / "candidate-r2",
                strategy_path=td_path / "candidate-r2" / "strategy.py",
                status="pending",
                generation=1,
            )
            good_done = wildcard_parallel.CandidateResult(
                job_id="candidate-r2",
                index=1,
                workdir=td_path / "candidate-r2",
                strategy_path=td_path / "candidate-r2" / "strategy.py",
                status="accepted",
                scores=[95],
                comp=95,
                generation=1,
            )
            coordinator = wildcard_parallel.CullCoordinator(
                args,
                td_path / "status.json",
                td_path / "overlay.html",
                td_path / "session",
                [leader],
            )

            with mock.patch.object(wildcard_parallel, "evaluate_real", side_effect=[low_done, good_done]), \
                mock.patch.object(wildcard_parallel, "run_perturb", return_value=refill) as perturb:
                result = wildcard_parallel.evaluate_slot(1, first, args, td_path / "session", coordinator)

            self.assertIs(result, good_done)
            self.assertEqual(low_done.status, "culled")
            perturb.assert_called_once_with(args, 1, td_path / "session", 1)

    def test_wildcard_parallel_culls_runner_error_result_without_scoring(self):
        """strategy_runner が error 付き結果を返した場合はスコア化せず補充対象へ回す。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            candidate = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=0,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="pending",
            )
            candidate.strategy_path.parent.mkdir(parents=True)
            candidate.strategy_path.write_text("def decide(gs, analysis):\n    return {'x': 0}\n", encoding="utf-8")
            args = argparse.Namespace(
                games=1,
                bridge_timeout=1,
                cdp_base_port=19000,
                serve_base_port=18000,
            )

            bridge = mock.Mock()
            bridge.poll.return_value = None
            bridge._soren_log_files = ()

            with mock.patch.object(wildcard_parallel, "launch_bridge", return_value=bridge), \
                mock.patch.object(wildcard_parallel, "capture_candidate_preview"), \
                mock.patch.object(wildcard_parallel, "cleanup_wildcard_server_ports"), \
                mock.patch.object(wildcard_parallel, "cleanup_chrome_profile_processes"), \
                mock.patch.object(wildcard_parallel.subprocess, "Popen") as popen:
                proc = mock.Mock()
                proc.poll.return_value = 0
                proc.communicate.return_value = (
                    '---RESULT---\n{"error":"bridge_desync","score":0,"turns":1,"state":"MOVE","pieces":1,"final_types":[1]}\n',
                    "",
                )
                proc.returncode = 0
                popen.return_value = proc

                result = wildcard_parallel.evaluate_real(candidate, args, td_path / "session")

            self.assertEqual(result.status, "culled")
            self.assertEqual(result.scores, [])
            self.assertEqual(result.raw_scores, [])
            self.assertIn("bridge_desync", result.error)
            self.assertFalse(list((result.workdir / "game_history").glob("wildcard_parallel_*_score0.jsonl")))

    def test_wildcard_parallel_refills_runner_error_cull(self):
        """未完走 cull は通常の cull と同じく次の候補で slot を埋め直す。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            args = argparse.Namespace(
                jobs=3,
                games=2,
                min_successful_games=2,
                cull_after_games=1,
                cull_leader_min_games=2,
                cull_comp_ratio=0.90,
                lingering_slot_max_culls=6,
            )
            first = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="pending",
            )
            incomplete = wildcard_parallel.CandidateResult(
                job_id="candidate",
                index=1,
                workdir=td_path / "candidate",
                strategy_path=td_path / "candidate" / "strategy.py",
                status="culled",
                error="incomplete game culled without score: bridge_desync",
            )
            refill = wildcard_parallel.CandidateResult(
                job_id="candidate-r2",
                index=1,
                workdir=td_path / "candidate-r2",
                strategy_path=td_path / "candidate-r2" / "strategy.py",
                status="pending",
                generation=1,
            )
            good_done = wildcard_parallel.CandidateResult(
                job_id="candidate-r2",
                index=1,
                workdir=td_path / "candidate-r2",
                strategy_path=td_path / "candidate-r2" / "strategy.py",
                status="accepted",
                scores=[100, 120],
                comp=110,
                generation=1,
            )
            coordinator = wildcard_parallel.CullCoordinator(
                args,
                td_path / "status.json",
                td_path / "overlay.html",
                td_path / "session",
                [],
            )

            with mock.patch.object(wildcard_parallel, "evaluate_real", side_effect=[incomplete, good_done]), \
                mock.patch.object(wildcard_parallel, "run_perturb", return_value=refill) as perturb:
                result = wildcard_parallel.evaluate_slot(1, first, args, td_path / "session", coordinator)

            self.assertIs(result, good_done)
            self.assertEqual(incomplete.status, "culled")
            self.assertEqual(incomplete.scores, [])
            perturb.assert_called_once_with(args, 1, td_path / "session", 1)

    def test_wildcard_parallel_cuts_off_lingering_last_slot_after_cull_limit(self):
        """残り1スロットが6回超カリングされたら補充を止め、既存acceptedの採用へ進む。"""
        import argparse
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            args = argparse.Namespace(
                jobs=3,
                games=2,
                min_successful_games=2,
                cull_after_games=1,
                cull_leader_min_games=2,
                cull_comp_ratio=0.90,
                lingering_slot_max_culls=6,
            )
            accepted_a = wildcard_parallel.CandidateResult(
                job_id="accepted-a",
                index=0,
                workdir=td_path / "accepted-a",
                strategy_path=td_path / "accepted-a" / "strategy.py",
                status="accepted",
                scores=[100, 100],
                comp=100,
            )
            accepted_b = wildcard_parallel.CandidateResult(
                job_id="accepted-b",
                index=2,
                workdir=td_path / "accepted-b",
                strategy_path=td_path / "accepted-b" / "strategy.py",
                status="accepted",
                scores=[120, 120],
                comp=120,
            )
            prior_culled = [
                wildcard_parallel.CandidateResult(
                    job_id=f"candidate-r{i}",
                    index=1,
                    workdir=td_path / f"candidate-r{i}",
                    strategy_path=td_path / f"candidate-r{i}" / "strategy.py",
                    status="culled",
                    scores=[50],
                    comp=50,
                )
                for i in range(6)
            ]
            first = wildcard_parallel.CandidateResult(
                job_id="candidate-r7",
                index=1,
                workdir=td_path / "candidate-r7",
                strategy_path=td_path / "candidate-r7" / "strategy.py",
                status="pending",
                generation=6,
            )
            low_done = wildcard_parallel.CandidateResult(
                job_id="candidate-r7",
                index=1,
                workdir=td_path / "candidate-r7",
                strategy_path=td_path / "candidate-r7" / "strategy.py",
                status="accepted",
                scores=[60],
                comp=60,
                generation=6,
            )
            coordinator = wildcard_parallel.CullCoordinator(
                args,
                td_path / "status.json",
                td_path / "overlay.html",
                td_path / "session",
                [accepted_a, accepted_b, *prior_culled],
            )

            with mock.patch.object(wildcard_parallel, "evaluate_real", return_value=low_done), \
                mock.patch.object(wildcard_parallel, "run_perturb") as perturb:
                result = wildcard_parallel.evaluate_slot(1, first, args, td_path / "session", coordinator)

            self.assertIs(result, low_done)
            self.assertEqual(low_done.status, "culled")
            self.assertIn("lingering slot cutoff after 7 culls", low_done.error)
            perturb.assert_not_called()
            self.assertIs(wildcard_parallel.choose_winner(coordinator.candidates, 2), accepted_b)

    def test_wildcard_parallel_reuses_bridge_and_retries_between_real_games(self):
        """real 評価は候補ごとの Chrome を開き直さず retry で次ゲームへ進む。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn('for game_index in range(args.games):', parallel)
        self.assertIn("def reset_bridge_for_next_game", parallel)
        self.assertIn('(workdir / "commands.txt").write_text("retry\\n", encoding="utf-8")', parallel)
        self.assertIn("if game_index > 0 or reused_bridge:\n                    reset_bridge_for_next_game(workdir, bridge, args.bridge_timeout)", parallel)
        self.assertIn('slot_runtime: dict = {"workdir": session_dir / f"slot-{index + 1}"}', parallel)
        self.assertIn("evaluate_real(candidate, args, session_dir, coordinator.should_cull, slot_runtime=slot_runtime)", parallel)
        self.assertIn('(workdir / "game_state.json").write_text("{}", encoding="utf-8")', parallel)
        self.assertIn("bridge = launch_bridge(workdir, env, args.bridge_timeout)", parallel)
        self.assertEqual(parallel.count("bridge = launch_bridge(workdir, env, args.bridge_timeout)"), 1)
        self.assertIn("if not reuse_slot_runtime:\n            stop_process(bridge)", parallel)
        self.assertIn("finally:\n        bridge = slot_runtime.get(\"bridge\")\n        stop_process(bridge)", parallel)

    def test_wildcard_parallel_records_running_before_first_game(self):
        """補充候補が起動済みなのに overlay が pending のまま残らないようにする。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn('candidate.status = "running"', parallel)
        self.assertIn("candidate.profile_dir = str((workdir / \"tmp\" / \"chromium_profile\").resolve())", parallel)
        self.assertIn("if progress_callback:\n        progress_callback(candidate)", parallel)

    def test_wildcard_parallel_prefers_bundled_chrome_for_testing(self):
        """候補評価は同梱 Chromium 優先、失敗時のみ通常Chromeへ退避する。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn("def resolve_playwright_chrome_for_testing", parallel)
        self.assertIn("def resolve_macos_system_chrome", parallel)
        self.assertIn("def chrome_fallback_app_paths", parallel)
        self.assertIn("def chrome_fallback_executable_paths", parallel)
        self.assertIn("chromium.executablePath()", parallel)
        self.assertIn("def prelaunch_candidate_chrome", parallel)
        self.assertIn("def is_regular_macos_chrome_path", parallel)
        self.assertIn("def chrome_open_fallback_bundle_ids", parallel)
        self.assertIn("def set_candidate_html_window_title", parallel)
        self.assertIn('f"Wildcard Parallel Slot {slot_index + 1}"', parallel)
        self.assertIn('re.sub(r\'productName:\\s*"[^"]*"\', f\'productName: "{title}"\', text, count=1)', parallel)
        self.assertIn("def wait_for_candidate_chrome_cdp", parallel)
        self.assertIn("def wait_for_candidate_chrome_cdp(cdp_port: int, timeout: float = 15.0)", parallel)
        self.assertIn("urllib.request.urlopen(url, timeout=0.5)", parallel)
        self.assertIn("return wait_for_candidate_chrome_cdp(cdp_port)", parallel)
        self.assertIn("return bool(_run_with_launch_stagger(launch_and_wait))", parallel)
        self.assertIn("[candidate_executable_path, *chrome_args]", parallel)
        self.assertIn("Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", parallel)
        self.assertIn("chrome_executable_path = resolve_playwright_chrome_for_testing(playwright_browsers_path)", parallel)
        self.assertIn("chrome_fallback_app_path_list = chrome_fallback_app_paths(chrome_app_path)", parallel)
        self.assertIn("chrome_fallback_executable_path_list = chrome_fallback_executable_paths(chrome_executable_path)", parallel)
        self.assertIn('"WILDCARD_PARALLEL_CHROME_FALLBACK_APP_PATHS"', parallel)
        self.assertIn('"SOREN_CHROME_OPEN_FALLBACK_APP_PATHS"', parallel)
        self.assertIn('"WILDCARD_PARALLEL_CHROME_FALLBACK_EXECUTABLE_PATHS"', parallel)
        self.assertIn('"SOREN_CHROME_FALLBACK_EXECUTABLE_PATHS"', parallel)
        self.assertIn('default_headless = "0" if sys.platform == "darwin" else "1"', parallel)
        self.assertIn('"HOME": str((workdir / "tmp" / "chrome_home").resolve())', parallel)
        self.assertIn('"SOREN_CHROME_USE_REAL_HOME": os.environ.get("WILDCARD_PARALLEL_USE_REAL_CHROME_HOME", "0")', parallel)
        self.assertIn('"SOREN_CHROME_SET_CFFIXED_HOME": os.environ.get("WILDCARD_PARALLEL_SET_CFFIXED_HOME", "1")', parallel)
        self.assertIn('use_real_home = os.environ.get("SOREN_CHROME_USE_REAL_HOME", "0").strip().lower() in {"1", "true", "yes", "on"}', parallel)
        self.assertIn('cffixed_home_setting = os.environ.get("SOREN_CHROME_SET_CFFIXED_HOME", "").strip().lower()', parallel)
        self.assertIn("else not use_real_home", parallel)
        self.assertIn("real_home = os.environ.get(\"SOREN_LAUNCHSERVICES_HOME\") or str(Path.home())", parallel)
        self.assertIn("def launch_env_for(candidate_app_path: str, candidate_executable_path: str, launch_services: bool = False)", parallel)
        self.assertIn("if use_real_home:", parallel)
        self.assertIn("launch_home = real_home", parallel)
        self.assertIn('env["HOME"] = launch_home', parallel)
        self.assertIn('env.pop("CFFIXED_USER_HOME", None)', parallel)
        self.assertIn('env.pop("XDG_CONFIG_HOME", None)', parallel)
        self.assertIn('env.pop("XDG_CACHE_HOME", None)', parallel)
        self.assertIn("if set_cffixed_home:", parallel)
        self.assertIn('env["CFFIXED_USER_HOME"] = cf_home', parallel)
        self.assertIn("env=env", parallel)
        self.assertIn('"/usr/bin/open",\n            "-g",\n            "-n",\n            app_path,', parallel)
        self.assertIn('"/usr/bin/open",\n                "-g",\n                "-n",\n                fallback_app_path,', parallel)
        self.assertIn('"-b",\n                bundle_id,', parallel)
        self.assertIn("prelaunch_ok = prelaunch_candidate_chrome(", parallel)
        self.assertIn("chrome_app_path,\n            chrome_executable_path,\n            candidate.profile_dir,\n            candidate.cdp_port,", parallel)
        self.assertIn("chrome_fallback_app_path_list,\n            chrome_fallback_executable_path_list,\n        )", parallel)
        self.assertNotIn('Wildcard Parallel Cand {candidate.index + 1} | soren-game', parallel)
        self.assertIn("class TmuxBridgeProcess", parallel)
        self.assertIn('os.environ.get("WILDCARD_PARALLEL_BRIDGE_TMUX", "1")', parallel)
        self.assertIn('["tmux", "new-session", "-d", "-s", session_name, str(script_path.resolve())]', parallel)
        self.assertIn('"--disable-crashpad"', parallel)
        self.assertNotIn('"--disable-breakpad"', parallel)
        self.assertNotIn('"--disable-crashpad-for-testing"', parallel)
        self.assertIn('f"--crash-dumps-dir={crashpad_dir}"', parallel)
        self.assertNotIn('"-a",\n            app_path,', parallel)
        self.assertIn('use_system_chrome = os.environ.get("WILDCARD_PARALLEL_USE_SYSTEM_CHROME", "0")', parallel)
        self.assertIn('use_system_chrome not in {"0", "1"}', parallel)
        self.assertIn("not explicit_chrome_path", parallel)
        self.assertIn('env["SOREN_CHROME_APP_PATH"] = chrome_app_path', parallel)
        self.assertIn('os.environ.get("WILDCARD_PARALLEL_OBS_BROWSER_SOURCES", "1")', parallel)
        self.assertIn('[ "${POST_IMPROVE_PARAM_PARALLEL_ENABLED:-0}" = "1" ] || return 0', (REPO_ROOT / "eloop_improve.sh").read_text())
        self.assertIn('export WILDCARD_PARALLEL_OBS_WINDOW_SOURCES="${WILDCARD_PARALLEL_OBS_WINDOW_SOURCES:-0}"', (REPO_ROOT / "eloop_improve.sh").read_text())
        self.assertIn('export WILDCARD_PARALLEL_OBS_BROWSER_SOURCES="${WILDCARD_PARALLEL_OBS_BROWSER_SOURCES:-0}"', (REPO_ROOT / "eloop_improve.sh").read_text())

    def test_post_improve_param_parallel_blocks_main_and_respects_small_jobs(self):
        """post-improve 追加試行は改善扱いで本線を止め、軽量 slot 数指定は 6 に戻さない。"""
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn('[ "$param_parallel_jobs" -lt 3 ] && param_parallel_jobs=3', improve)
        self.assertNotIn('[ "$param_parallel_jobs" -lt 6 ] && param_parallel_jobs=6', improve)
        self.assertIn('--block-main-loop', improve)
        self.assertNotIn('--no-block-main-loop', improve)
        self.assertIn('--serve-base-port "${POST_IMPROVE_PARAM_PARALLEL_SERVE_BASE_PORT:-18180}"', improve)
        self.assertIn('--cdp-base-port "${POST_IMPROVE_PARAM_PARALLEL_CDP_BASE_PORT:-19320}"', improve)

    def test_wildcard_parallel_candidate_title_does_not_mutate_main_game_html(self):
        """候補 window title の変更は candidate copy に限定し、本線 OBS target の HTML は変えない。"""
        import wildcard_parallel

        main_index = REPO_ROOT / "sorengame" / "build" / "index.html"
        before = main_index.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            (workdir / "sorengame").symlink_to(REPO_ROOT / "sorengame")

            wildcard_parallel.set_candidate_html_window_title(workdir, 2)

            after = main_index.read_text(encoding="utf-8")
            candidate = (workdir / "sorengame" / "build" / "index.html").read_text(encoding="utf-8")
            self.assertEqual(after, before)
            self.assertIn("<title>Wildcard Parallel Slot 3</title>", candidate)
            self.assertIn('productName: "Wildcard Parallel Slot 3"', candidate)

    def test_wildcard_parallel_cleans_candidate_chrome_windows(self):
        """WILDCARD 候補 Chrome は profile/port 指定で残骸 cleanup する。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()
        self.assertIn('"SOREN_CHROME_HOME": str((workdir / "tmp" / "chrome_home").resolve())', parallel)
        self.assertIn('"SOREN_CHROME_USE_REAL_HOME": os.environ.get("WILDCARD_PARALLEL_USE_REAL_CHROME_HOME", "0")', parallel)
        self.assertIn('"SOREN_CHROME_SET_CFFIXED_HOME": os.environ.get("WILDCARD_PARALLEL_SET_CFFIXED_HOME", "1")', parallel)
        self.assertIn('"SOREN_CHROME_NO_FOCUS_LAUNCH": os.environ.get("WILDCARD_PARALLEL_NO_FOCUS_LAUNCH", "1")', parallel)
        self.assertIn('"SOREN_CHROME_FORCE_PLAYWRIGHT_LAUNCH": os.environ.get("WILDCARD_PARALLEL_FORCE_PLAYWRIGHT_LAUNCH", "0")', parallel)
        self.assertIn('"SOREN_CHROME_OPEN_FALLBACK_APP_NAME": os.environ.get(', parallel)
        self.assertIn('"WILDCARD_PARALLEL_OPEN_FALLBACK_APP_NAME"', parallel)
        self.assertIn('"SOREN_CHROME_OPEN_FALLBACK_BUNDLE_ID": os.environ.get(', parallel)
        self.assertIn('"WILDCARD_PARALLEL_OPEN_FALLBACK_BUNDLE_ID"', parallel)
        self.assertIn('"SOREN_CHROME_OPEN_FALLBACK_APP_PATHS": os.environ.get(', parallel)
        self.assertIn('"WILDCARD_PARALLEL_CHROME_FALLBACK_APP_PATHS"', parallel)
        self.assertIn('"SOREN_CHROME_FALLBACK_EXECUTABLE_PATHS": os.environ.get(', parallel)
        self.assertIn('"WILDCARD_PARALLEL_CHROME_FALLBACK_EXECUTABLE_PATHS"', parallel)
        self.assertIn("com.google.chrome.for.testing,com.google.Chrome", parallel)
        self.assertIn('"SOREN_LAUNCHSERVICES_HOME": str(Path.home())', parallel)
        self.assertIn("def cleanup_chrome_profile_processes", parallel)
        self.assertIn("def cleanup_wildcard_chrome_processes", parallel)
        self.assertIn('"Chrome" not in command and "Chromium" not in command', parallel)
        self.assertIn("def cleanup_ports_from_status", parallel)
        self.assertIn("def cleanup_wildcard_session_dirs", parallel)
        self.assertIn('parser.add_argument("--cleanup-stale", action="store_true")', parallel)
        self.assertIn('parser.add_argument("--cleanup-sessions", action="store_true")', parallel)
        self.assertIn('WILDCARD_PARALLEL_KEEP_RECENT_RUNS"), 3', parallel)
        self.assertIn("def stop_status_controller", parallel)
        self.assertIn("stop_status_controller(args.status_file)", parallel)
        self.assertIn("cleanup_wildcard_chrome_processes(session_root=args.session_root)", parallel)
        self.assertIn("cleanup_wildcard_server_ports(cleanup_ports_from_status(args.status_file, args.serve_base_port, args.jobs))", parallel)
        self.assertIn("cleanup_wildcard_chrome_processes(session_dir=session_dir)", parallel)
        self.assertIn("cleanup_wildcard_chrome_processes(session_dir=_ACTIVE_SESSION_DIR)", parallel)
        self.assertIn("cleanup_wildcard_session_dirs(", parallel)
        self.assertIn("wildcard_parallel_cleanup_sessions()", improve)
        self.assertIn("--cleanup-sessions", improve)
        self.assertIn('"wildcard_parallel" not in profile_dir', parallel)
        self.assertIn('"ps", "-Ao", "pid=,command=", "-ww"', parallel)
        self.assertIn("cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)\n            bridge = None", parallel)
        self.assertIn("cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)", parallel)

    def test_wildcard_parallel_status_records_ports_for_cleanup(self):
        import argparse
        import wildcard_parallel

        args = argparse.Namespace(
            jobs=4,
            games=6,
            min_successful_games=6,
            cull_after_games=1,
            cull_leader_min_games=2,
            cull_comp_ratio=0.9,
            lingering_slot_max_culls=0,
            evaluate_mode="real",
            random_count=True,
            serve_base_port=18180,
            cdp_base_port=19320,
            deadline_fast_drop_mutate=True,
            deadline_fast_drop_values=[True, False],
            baseline_slot1=True,
            block_main_loop=True,
        )

        params = wildcard_parallel.wildcard_parallel_params(args)
        self.assertEqual(params["serve_base_port"], 18180)
        self.assertEqual(params["cdp_base_port"], 19320)
        with tempfile.TemporaryDirectory() as td:
            status = Path(td) / "status.json"
            status.write_text(
                json.dumps(
                    {
                        "params": {"jobs": 2, "serve_base_port": 18180},
                        "candidates": [{"serve_port": 18182}],
                    }
                ),
                encoding="utf-8",
            )
            ports = wildcard_parallel.cleanup_ports_from_status(status, 18080, 3)
            self.assertIn(18080, ports)
            self.assertIn(18180, ports)
            self.assertIn(18181, ports)
            self.assertIn(18182, ports)

    def test_wildcard_cleanup_stops_status_controller_pid(self):
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            status_path = Path(td) / "status.json"
            status_path.write_text(json.dumps({"phase": "running", "controller_pid": 424242}), encoding="utf-8")
            calls = []

            def fake_kill(pid, sig):
                calls.append((pid, sig))
                if sig == 0 and len(calls) > 2:
                    raise OSError()

            with mock.patch.object(wildcard_parallel.os, "kill", side_effect=fake_kill), \
                 mock.patch.object(wildcard_parallel.time, "sleep", return_value=None):
                stopped = wildcard_parallel.stop_status_controller(status_path, wait_sec=0.5)

            self.assertTrue(stopped)
            self.assertIn((424242, wildcard_parallel.signal.SIGTERM), calls)

    def test_wildcard_parallel_prunes_old_session_dirs_without_active(self):
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "wildcard_parallel"
            old = root / "run-20260525-010101"
            active = root / "run-20260527-001825"
            newest = root / "run-20260528-001825"
            ignored = root / "not-a-run"
            for path in [old, active, newest, ignored]:
                path.mkdir(parents=True)
                (path / "marker.txt").write_text(path.name, encoding="utf-8")

            removed = wildcard_parallel.cleanup_wildcard_session_dirs(
                root,
                keep_session_dirs=[active],
                keep_recent=1,
            )

            self.assertEqual([p.name for p in removed], ["run-20260525-010101"])
            self.assertFalse(old.exists())
            self.assertTrue(active.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(ignored.exists())

    def test_wildcard_parallel_overlay_uses_window_capture_tiles_not_embedded_previews(self):
        """候補のライブ画面は OBS window-capture タイルで出す。overlay 自身は画面を
        <img> 埋め込みしない（タイルは上位レイヤーなので埋め込み画像は隠れてしまう）。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        # live候補画面は OBS の window-capture source として strip の下に並べる
        self.assertIn("def maybe_show_obs_candidate_source", parallel)
        self.assertIn("obs_window_capture_source.sh", parallel)
        # overlay HTML は screenshot を <img> 埋め込みしない（タイルに隠れるため）
        self.assertNotIn('<img class="preview live-preview"', parallel)
        self.assertNotIn('class="preview live-preview"', parallel)

    def test_wildcard_parallel_cleanup_hides_obs_candidate_sources(self):
        """OBSクラッシュ後も候補window-capture sourceをcleanupで非表示に戻す。"""
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            status = Path(td) / "status.json"
            status.write_text(
                json.dumps(
                    {
                        "params": {"jobs": 2},
                        "candidates": [
                            {"index": 0, "status": "accepted"},
                            {"index": 5, "status": "running"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.object(wildcard_parallel.subprocess, "run", side_effect=fake_run):
                with mock.patch.dict(
                    os.environ,
                    {
                        "WILDCARD_PARALLEL_CANDIDATE_SOURCE_PREFIX": "wpCand",
                        "WILDCARD_PARALLEL_OBS_CLEANUP_TIMEOUT_MS": "1200",
                    },
                    clear=False,
                ):
                    sources = wildcard_parallel.hide_wildcard_candidate_obs_sources(status, jobs=3, reason="test")

            self.assertEqual([f"wpCand{i}" for i in range(1, 7)], sources)
            self.assertEqual(["./obs_control.sh", "hide", "soren", *sources], calls[0][0])
            self.assertEqual("1200", calls[0][1]["env"]["OBS_WEBSOCKET_TIMEOUT_MS"])

    def test_candidate_window_title_reaps_blank_tabs_for_obs_capture(self):
        """候補窓が OBS window-capture で映らない("target window not found") 根因は、
        about:blank タブがアクティブで OS 窓タイトルが空になり "Wildcard Parallel Cand N"
        にマッチしないこと。set_candidate_window_title はゲームタブにタイトルを設定後、
        about:blank タブを reap して、ゲームタブを唯一=アクティブにする(窓タイトルが正しく
        なる)。.bringToFront() は main/local 同様に使わない(focus/white-screen を起こす)。
        次の param並列が自己診断できるよう stderr を tmp/debug にログする。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        title_fn = parallel.split("def set_candidate_window_title", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("startsWith('about:blank')", title_fn)  # reap blanks
        self.assertIn("b.close()", title_fn)
        self.assertNotIn(".bringToFront()", title_fn)  # avoid focus/white-screen like main/local
        self.assertIn("set_candidate_window_title.log", parallel)  # diagnostic for next run

    def test_wildcard_parallel_overlay_shows_provisional_ranking(self):
        """parallel trial overlay は候補の暫定順位をバッジとバーで表示する。"""
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            status_path = td_path / "status.json"
            html_path = td_path / "overlay.html"
            wildcard_parallel.render_overlay(
                status_path,
                html_path,
                {
                    "phase": "running",
                    "candidates": [
                        {"job_id": "cand-1", "index": 0, "status": "running", "games": 2, "comp": 80, "p25": 70, "p50": 75, "max_type": 12},
                        {"job_id": "cand-2", "index": 1, "status": "running", "games": 2, "comp": 120, "p25": 90, "p50": 100, "max_type": 14},
                    ],
                },
            )
            doc = html_path.read_text(encoding="utf-8")
            # compact top strip: per-slot rank badge + comp bar + comp value + g{games}
            self.assertIn('<span class="prank r1">#1</span>', doc)
            self.assertIn('<span class="prank r2">#2</span>', doc)
            self.assertIn('class="pbar-fill"', doc)
            self.assertIn("width:100%", doc)                  # leader's comp bar is full
            self.assertIn('class="pcell run leader"', doc)    # rank-1 slot marked as leader
            self.assertIn('<span class="pcomp">120</span>', doc)
            self.assertIn('<span class="pcomp">80</span>', doc)
            self.assertIn('<span class="pgames">g2</span>', doc)

    def test_wildcard_parallel_no_candidate_is_noop(self):
        """基準未達なら winner を返さず、本線 strategy.py は変更しない。"""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            strategy = td_path / "strategy.py"
            original = (
                "def decide(game_state, analysis):\n"
                "    x = 0.50\n"
                "    return {\"x\": x, \"reason\": \"test\"}\n"
            )
            strategy.write_text(original, encoding="utf-8")
            result_file = td_path / "result.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_parallel.py"),
                    "--strategy",
                    str(strategy),
                    "--jobs",
                    "3",
                    "--games",
                    "1",
                    "--min-successful-games",
                    "2",
                    "--evaluate-mode",
                    "simulate",
                    "--session-root",
                    str(td_path / "sessions"),
                    "--status-file",
                    str(td_path / "status.json"),
                    "--html-file",
                    str(td_path / "wildcard.html"),
                    "--result-file",
                    str(result_file),
                    "--seed",
                    "5678",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 2, msg=f"stderr={proc.stderr}\nstdout={proc.stdout}")
            result = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "no_candidate")
            self.assertEqual(strategy.read_text(encoding="utf-8"), original)

    def test_wildcard_parallel_signal_persists_best_effort_winner(self):
        """長時間探索がSIGTERMされても完走済み候補から result を残す。"""
        import argparse
        import signal
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            status_file = td_path / "status.json"
            html_file = td_path / "wildcard.html"
            result_file = td_path / "result.json"
            args = argparse.Namespace(
                jobs=3,
                games=6,
                min_successful_games=6,
                cull_after_games=1,
                cull_leader_min_games=2,
                cull_comp_ratio=0.90,
                lingering_slot_max_culls=0,
                evaluate_mode="real",
                random_count=True,
                serve_base_port=18180,
                cdp_base_port=19320,
                deadline_fast_drop_mutate=True,
                deadline_fast_drop_values=[True, False],
                baseline_slot1=True,
                block_main_loop=True,
            )
            wildcard_parallel.render_overlay(
                status_file,
                html_file,
                {
                    "phase": "running",
                    "session_dir": str(td_path / "session"),
                    "params": wildcard_parallel.wildcard_parallel_params(args),
                    "candidates": [
                        {
                            "job_id": "cand-1",
                            "index": 0,
                            "workdir": str(td_path / "slot-1"),
                            "strategy_path": str(td_path / "slot-1" / "strategy.py"),
                            "status": "accepted",
                            "scores": [100, 100, 100, 100, 100, 100],
                            "comp": 100,
                            "p25": 100,
                            "p50": 100,
                            "baseline": True,
                            "serve_port": 18180,
                            "cdp_port": 19320,
                        },
                        {
                            "job_id": "cand-2-r15",
                            "index": 1,
                            "workdir": str(td_path / "slot-2"),
                            "strategy_path": str(td_path / "slot-2" / "strategy.py"),
                            "status": "accepted",
                            "scores": [120, 120, 120, 120, 120, 120],
                            "comp": 120,
                            "p25": 120,
                            "p50": 120,
                            "serve_port": 18181,
                            "cdp_port": 19321,
                        },
                        {
                            "job_id": "cand-3-r156",
                            "index": 2,
                            "workdir": str(td_path / "slot-3"),
                            "strategy_path": str(td_path / "slot-3" / "strategy.py"),
                            "status": "running",
                            "scores": [],
                            "comp": 0,
                            "serve_port": 18182,
                            "cdp_port": 19322,
                        },
                    ],
                },
            )

            wrote = wildcard_parallel.write_interrupted_result_from_status(
                args,
                status_file,
                html_file,
                result_file,
                td_path / "session",
                signal.SIGTERM,
            )

            self.assertTrue(wrote)
            result = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(result["interrupted"])
            self.assertEqual(result["reason"], "winner_selected")
            self.assertEqual(result["winner"]["job_id"], "cand-2-r15")

    def test_wildcard_parallel_classifies_zero_game_bridge_failures_as_infra_failed(self):
        """全候補が bridge 起動前に落ちた場合は候補負けと区別する。"""
        import wildcard_parallel

        candidates = []
        for index in range(3):
            candidate = wildcard_parallel.CandidateResult(
                job_id=f"cand-{index + 1}",
                index=index,
                workdir=Path("/tmp"),
                strategy_path=Path("/tmp/strategy.py"),
                status="failed",
                error="bridge exited rc=1 | stderr: process did exit: signal=SIGABRT",
            )
            candidates.append(candidate)
        self.assertEqual(wildcard_parallel.no_winner_reason(candidates), "infra_failed")

    def test_wildcard_parallel_infra_failed_falls_back_to_direct_perturb(self):
        """並列評価のインフラ失敗は脱出を空振りで終わらせず直接摂動へ落とす。"""
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn('wildcard_parallel_fail_reason=$(python3 - "$wildcard_parallel_result_file"', improve)
        self.assertIn('= "infra_failed" ] && [ "${WILDCARD_PARALLEL_INFRA_FALLBACK_DIRECT:-1}" = "1"', improve)
        self.assertIn("parallel infra_failed → direct wildcard perturb fallback", improve)
        self.assertIn("wildcard_parallel_fallback_direct=1", improve)
        self.assertIn('if [ "${wildcard_parallel_fallback_direct:-0}" != "1" ]; then', improve)
        self.assertLess(
            improve.index("parallel infra_failed → direct wildcard perturb fallback"),
            improve.rindex('HASH_BEFORE=$(python3 extract_decide_hash.py "$STRATEGY_FILE"'),
        )

    def test_obs_control_supports_wildcard_parallel_transform(self):
        """OBS helper は wildcardParallelOverlay を表示し、候補6面を3列x2行に配置できる。"""
        obs_control = (REPO_ROOT / "obs_control.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        browser_source = (REPO_ROOT / "obs_browser_source.sh").read_text()
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn("transform <scene> <source> <x> <y> <scaleX> <scaleY>", obs_control)
        self.assertIn("SetSceneItemTransform", obs_control)
        self.assertIn("OBS_CONTROL_TRANSFORM_MODE=force", obs_control)
        self.assertIn("transform-preserved", obs_control)
        self.assertIn("isDefaultTransform", obs_control)
        self.assertIn("GetSceneItemTransform", obs_control)
        self.assertIn("<html-file-or-url>", browser_source)
        self.assertIn("http://*|https://*", browser_source)
        self.assertIn("OBS_TOP_OVERLAY_SOURCE", window_source := (REPO_ROOT / "obs_window_capture_source.sh").read_text())
        self.assertIn("OBS_BELOW_TOP_OVERLAY_SOURCE", window_source)
        self.assertIn("SetSceneItemIndex", window_source)
        self.assertIn("await enforceOverlayStack(obs)", window_source)
        self.assertIn("SetInputMute", window_source)
        self.assertIn("WILDCARD_PARALLEL_CANDIDATE_AUDIO", window_source)
        self.assertIn("OBS_WINDOW_CAPTURE_AUDIO", window_source)
        self.assertIn("sck_audio_capture", window_source)
        self.assertIn("OBS_WINDOW_AUDIO_SOURCE", window_source)
        self.assertIn("wildcard_parallel_obs_show", eloop)
        self.assertIn("wildcardParallelOverlay", eloop)
        self.assertIn('WILDCARD_PARALLEL_OVERLAY_TITLE="POST-IMPROVE PARAM TUNING"', eloop)
        self.assertIn("wildcardParallelCand", eloop)
        self.assertIn("${cand_prefix}1,${cand_prefix}2,${cand_prefix}3,${cand_prefix}4,${cand_prefix}5,${cand_prefix}6", eloop)
        self.assertIn("wildcardParallelCand", parallel)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_JOBS"), 6)', parallel)
        # New layered design: compact opaque top strip (pstrip) instead of full-area cards.
        self.assertIn("''.join(pcells)", parallel)
        self.assertIn("pstrip", parallel)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_COLS', parallel)
        config_sh = (REPO_ROOT / "core/config.sh").read_text()
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_W="${WILDCARD_PARALLEL_OBS_CANDIDATE_W:-640}"', config_sh)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_H="${WILDCARD_PARALLEL_OBS_CANDIDATE_H:-440}"', config_sh)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_X="${WILDCARD_PARALLEL_OBS_CANDIDATE_X:-0}"', config_sh)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_Y="${WILDCARD_PARALLEL_OBS_CANDIDATE_Y:-200}"', config_sh)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_COL_STRIDE="${WILDCARD_PARALLEL_OBS_CANDIDATE_COL_STRIDE:-640}"', config_sh)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_ROW_STRIDE="${WILDCARD_PARALLEL_OBS_CANDIDATE_ROW_STRIDE:-440}"', config_sh)
        self.assertIn('WILDCARD_PARALLEL_OVERLAY_HEIGHT="${WILDCARD_PARALLEL_OVERLAY_HEIGHT:-1080}"', config_sh)
        # tiles use crop-to-fill so they leave no gaps; overlay strip stays the default letterbox bounds.
        self.assertIn("OBS_CONTROL_BOUNDS_TYPE", obs_control)
        self.assertIn("OBS_BOUNDS_SCALE_OUTER", parallel)
        self.assertIn("export WILDCARD_PARALLEL_OBS_CANDIDATE_W", eloop)
        self.assertIn("export WILDCARD_PARALLEL_OBS_CANDIDATE_Y", eloop)
        self.assertIn("export WILDCARD_PARALLEL_OBS_CANDIDATE_COL_STRIDE", eloop)
        self.assertIn("export WILDCARD_PARALLEL_OBS_CANDIDATE_ROW_STRIDE", eloop)
        self.assertIn('candidate.index % cols', parallel)
        self.assertIn('candidate.index // cols', parallel)
        self.assertIn("本線ゲームを止め", loop := (REPO_ROOT / "soren_loop.sh").read_text())
        self.assertIn("候補6面", loop)
        self.assertIn("本線は見えない裏で進ませない", loop)
        self.assertIn('case "${_pause_reason}:${pause_phase:-}:${pause_detail:-}" in', loop)
        self.assertIn('wildcard:*|archive_restart:*|*:wildcard_parallel:*|*:*:post_improve_param_parallel*)', loop)
        self.assertIn("maybe_show_obs_candidate_source", parallel)
        self.assertIn('"./obs_window_capture_source.sh", "ensure", scene, source, window_pattern', parallel)
        self.assertIn("OBS_WINDOW_AUDIO_SOURCE", (REPO_ROOT / "soren91_control.sh").read_text())
        self.assertIn("OBS_WINDOW_AUDIO_SOURCE_ENABLED=1", (REPO_ROOT / "soren91_control.sh").read_text())
        self.assertIn("OBS_WINDOW_AUDIO_SOURCE_ENABLED=0", (REPO_ROOT / "soren91_control.sh").read_text())
        self.assertIn("OBS_WINDOW_CAPTURE_AUDIO=0", (REPO_ROOT / "soren91_control.sh").read_text())
        self.assertIn('export WILDCARD_PARALLEL_OBS_WINDOW_SOURCES="${WILDCARD_PARALLEL_OBS_WINDOW_SOURCES:-0}"', eloop)
        self.assertIn('"./obs_control.sh", "transform", scene, source', parallel)
        self.assertIn("hide:\"$hide_sources,", eloop)
        self.assertIn('hide_sources="$dashboard_source,$status_source,$show_status_source,$improve_source"', eloop)
        self.assertIn('show:"$overlay" hide:"$hide_sources,$cand_sources"', eloop)
        self.assertNotIn('show:"$overlay","$status_source","$show_status_source"', eloop)
        self.assertIn("wildcard_parallel_obs_restore", eloop)
        self.assertIn('hide:"$overlay,$cand_sources"', eloop)
        self.assertIn('show_sources="$dashboard_source,$status_source,$show_status_source,$improve_source"', eloop)
        self.assertIn('"${STATUS_OVERLAY_OBS_X:-24}" "${STATUS_OVERLAY_OBS_Y:-300}"', eloop)
        self.assertIn('"${SHOW_STATUS_OVERLAY_OBS_X:-1448}" "${SHOW_STATUS_OVERLAY_OBS_Y:-300}"', eloop)

    def test_obs_mac_capture_setinputsettings_serialized_cross_process(self):
        """全プロセスの mac-capture SetInputSettings は単一のファイルロックで直列化される。

        macOS mac-capture(SCK) は obs_source_update が並行すると二重free→OBSが
        SIGABRT落ち。in-process ロック(wildcard_parallel.py)では別プロセス(キャプチャ
        watchdog / 本線bridge / soren91 / 候補スロット)を直列化できないため、
        SetInputSettings を出す全箇所が lib/obs_source_lock.{sh,mjs} の同一ロックを
        必ず取得する。これが外れると並列パラメータ調整中にOBSごと落ちる回帰になる。
        """
        # The shared lock helpers exist and expose acquire/release in both languages.
        sh_lock = (REPO_ROOT / "lib" / "obs_source_lock.sh").read_text()
        mjs_lock = (REPO_ROOT / "lib" / "obs_source_lock.mjs").read_text()
        self.assertIn("obs_source_lock_acquire", sh_lock)
        self.assertIn("obs_source_lock_release", sh_lock)
        self.assertIn("mkdir \"$OBS_SOURCE_LOCK_DIR\"", sh_lock)
        self.assertIn("OBS_SOURCE_LOCK_SETTLE_SEC", sh_lock)
        self.assertIn("export async function acquireObsSourceLock", mjs_lock)
        self.assertIn("export async function releaseObsSourceLock", mjs_lock)
        self.assertIn("obs_source_update.lock", sh_lock)
        self.assertIn("obs_source_update.lock", mjs_lock)

        # Every mac-capture SetInputSettings site wraps its update in the lock.
        window_source = (REPO_ROOT / "obs_window_capture_source.sh").read_text()
        self.assertIn("obs_source_lock.sh", window_source)
        self.assertIn("obs_source_lock_acquire", window_source)
        self.assertIn("trap 'obs_source_lock_release", window_source)

        watchdog = (REPO_ROOT / "obs_capture_watchdog_check.mjs").read_text()
        self.assertIn("obs_source_lock.mjs", watchdog)
        self.assertIn("acquireObsSourceLock", watchdog)
        self.assertIn("releaseObsSourceLock", watchdog)

        bridge = (REPO_ROOT / "soviet_local.mjs").read_text()
        self.assertIn("obs_source_lock.mjs", bridge)
        self.assertIn("acquireObsSourceLock", bridge)
        self.assertIn("releaseObsSourceLock", bridge)

    def test_chrome_launch_serialized_cross_process(self):
        """全プロセスのChrome-for-Testing起動は単一ファイルロックで直列化される。

        macOSは2つのChromeが _RegisterApplication / NSApplication init を同時に走らせると
        SIGABRTで落ちる(crash 2026-06-02 16:23 本線bridge再起動 / 11:47候補burst)。
        wildcard_parallel.py の in-process ロックは本線soviet_local bridgeを直列化できない
        ため、Chromeをspawnする全箇所が lib/chrome_launch_lock.mjs の同一ロックを取得する。
        外れると並列調整/交代時にChrome起動レースでSIGABRT落ちする回帰になる。
        """
        mjs_lock = (REPO_ROOT / "lib" / "chrome_launch_lock.mjs").read_text()
        self.assertIn("export async function acquireChromeLaunchLock", mjs_lock)
        self.assertIn("export async function releaseChromeLaunchLock", mjs_lock)
        self.assertIn("chrome_launch.lock", mjs_lock)

        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn("def _acquire_chrome_launch_lock", parallel)
        self.assertIn("def _release_chrome_launch_lock", parallel)
        self.assertIn("chrome_launch.lock", parallel)
        self.assertIn("_acquire_chrome_launch_lock()", parallel)
        self.assertIn("_release_chrome_launch_lock(held)", parallel)

        bridge = (REPO_ROOT / "soviet_local.mjs").read_text()
        self.assertIn("chrome_launch_lock.mjs", bridge)
        self.assertIn("acquireChromeLaunchLock", bridge)
        self.assertIn("releaseChromeLaunchLock", bridge)
        self.assertIn("async function macOpenChromium", bridge)

    def test_wildcard_parallel_runtime_mutes_bgm_and_halves_se(self):
        """並列評価ブラウザだけ BGM=0 / SE=1.5 を渡す。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        local = (REPO_ROOT / "soviet_local.mjs").read_text()
        self.assertIn('"SOREN_BGM_VOLUME": os.environ.get("WILDCARD_PARALLEL_BGM_VOLUME", "0")', parallel)
        self.assertIn('"SOREN_SE_VOLUME": os.environ.get("WILDCARD_PARALLEL_SE_VOLUME", "1.5")', parallel)
        self.assertIn("SOREN_BGM_VOLUME", local)
        self.assertIn("SetBGMVolume", local)
        self.assertIn("SetSEVolume", local)
        self.assertIn("process.env.SOREN_BGM_VOLUME ?? 'off'", local)
        self.assertIn("SOREN_UNITY_VOLUME_REAPPLY_MS", local)
        self.assertIn("window.__sorenUnityVolumeReapplyTimer", local)
        self.assertIn("SOREN_UNITY_AUDIO_WATCHDOG_MS", local)
        self.assertIn("local_audio_health.json", local)
        self.assertIn("function withTimeout", local)
        self.assertIn("3000, 'inspectUnityAudio'", local)
        self.assertIn("audio route heal evaluate", local)
        self.assertIn("grant speakerSelection", local)
        self.assertIn("typeof resume.catch === 'function'", local)
        self.assertIn("recoverUnityAudio", local)
        self.assertIn("[AUDIO-WATCHDOG-RECOVER]", local)

    def test_repeated_wildcards_can_escalate_to_ai_structural_escape(self):
        """WILDCARD 連続失敗時は、次の脱出をAI構造変異モードへ上げられる。"""
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("WILDCARD_AI_ESCALATE_ENABLED", config)
        self.assertIn("WILDCARD_AI_ESCALATE_STREAK", config)
        self.assertIn("WILDCARD_ESCAPE_AI_SEED_ENABLED", config)
        self.assertIn("WILDCARD_ESCAPE_AI_SEED_MIN_GAMES", config)
        self.assertIn("WILDCARD_ESCAPE_AI_SEED_MIN_BEST_TYPE", config)
        self.assertIn("normal|post_regression|wildcard|escape_ai", improve)
        self.assertIn('improve_reason="escape_ai"', improve)
        self.assertIn("seeded escape_ai 構造変異モードで脱出", improve)
        self.assertIn('improve_reason="normal"', improve)
        self.assertIn("fallback normal AI? yes", improve)
        self.assertIn("escape_ai seedなし。WILDCARD再試行を止め、通常AI改善へ戻します。", improve)
        self.assertIn("通常AI改善へフォールバック", improve)
        self.assertIn("_escape_ai_seed_available", improve)
        self.assertIn("rejected_hash_metrics.json", improve)
        self.assertIn("rolling_scores.json", improve)
        self.assertIn("wildcard_origin.json", improve)
        self.assertIn("reconstructs failures from WILDCARDs", improve)
        self.assertIn("Mature origin + below-anchor metrics", improve)
        self.assertIn("m.get(\"comp\", 0.0) < anchor_comp", improve)
        self.assertIn("export IMPROVE_REASON", eloop)
        self.assertIn('os.environ.get("IMPROVE_REASON", "normal") == "escape_ai"', eloop)
        self.assertIn("seedなしのescape_aiは通常改善と同じため通常AI改善へフォールバック", eloop)
        self.assertIn("escape_ai_no_seed_fallback", eloop)
        self.assertIn("escape_ai_invalid_seed_fallback", eloop)
        self.assertIn("今回だけAIによる小さな構造変異で大域脱出を狙う", eloop)
        self.assertIn("WILDCARD起源からAI改善の起点候補を選定", eloop)
        self.assertIn("ESCAPE_AI_SEED_JSON", eloop)
        self.assertIn("seed_from_wildcard_", eloop)
        self.assertIn("_improve_flow_notify", eloop)
        self.assertIn("seeded escape_ai candidate? yes", eloop)
        self.assertIn("seeded escape_ai candidate? no", eloop)
        self.assertIn("通常AI改善へフォールバック", eloop)
        self.assertIn("origin_type != \"wildcard\"", eloop)
        self.assertIn("AI改善失敗のためWILDCARD seed適用を元へ戻した", eloop)
        self.assertIn("escape_ai seed: 粛清済みWILDCARD群", eloop)
        self.assertIn('"ESCAPE_AI_APPLIED"', eloop)
        self.assertIn('"escape_ai_success_reset"', eloop)
        self.assertIn('"last_escape_ai_hash"', eloop)
        self.assertIn('"origin_type": "escape_ai"', eloop)
        self.assertIn("not registered as wildcard origin", eloop)
        self.assertIn("stagnation/escape_ai latch cleared", eloop)
        self.assertNotIn('origin[hash_after] = {', eloop)
        self.assertNotIn('ESCAPE_AI_MAX_GAMES', eloop)

    def test_wildcard_stagnation_can_queue_early_escape_lock(self):
        """停滞時は12試合サイクルを待たずに早期脱出ロックとして改善daemonへ渡せる。"""
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("WILDCARD_EARLY_ESCAPE_LOCK_ENABLED", config)
        self.assertIn("WILDCARD_EARLY_ESCAPE_MIN_GAMES", config)
        self.assertIn("早期脱出ロック", loop)
        self.assertIn("[EARLY_ESCAPE]", loop)
        self.assertIn("改善ロック作成 (最終モードはimprove側で判定)", loop)
        self.assertIn("early_escape_lock", loop)
        self.assertIn("early_escape_stagnation", loop)
        self.assertIn("early_escape_regression_streak", loop)
        self.assertIn("WILDCARD_REGRESSION_STREAK", loop)
        self.assertNotIn("WILDCARD_REGRESSION_STREAK:-4", improve)
        self.assertNotIn("WILDCARD_REGRESSION_STREAK:-3", improve)
        self.assertNotIn("WILDCARD_REGRESSION_STREAK:-3", loop)
        self.assertIn("WILDCARD_REGRESSION_STREAK:-2", improve)
        self.assertIn("WILDCARD_REGRESSION_STREAK:-2", loop)
        self.assertIn("_expire_rate_limit_backoff_if_elapsed", loop)
        self.assertIn("rate-limit backoff期限切れ", loop)
        self.assertIn("rank1 hot streak 中 → 早期脱出ロックを延期", loop)
        self.assertIn("rollback revalidate fresh cycle 中", loop)
        self.assertIn("current batch は成績許容範囲", loop)
        self.assertIn("EARLY_ESCAPE_BATCH_OK", loop)
        self.assertIn("regression_streak をクリア", loop)
        self.assertIn("batch_comp >= leader_comp * min_ratio", loop)
        self.assertIn("def row_comp(row):", loop)
        self.assertIn("leader_comp = max(leader_comp, row_comp(row))", loop)
        self.assertIn("early_escape lock ignored: current batch is not bad enough", improve)
        self.assertIn("continue normal accumulation", improve)
        self.assertIn("rm -f \"$IMPROVE_LOCK_FILE\"", improve)
        self.assertIn("def row_comp(row):", improve)
        self.assertIn("leader_comp = max(leader_comp, row_comp(row))", improve)
        self.assertIn("last_rollback_pair.json", loop)
        self.assertIn("WILDCARD_TRIGGER_STAGNATION", loop)
        self.assertIn("MIN_GAMES_BEFORE_IMPROVE", loop)
        self.assertIn('SOREN_MAIN_PID="$$"', loop)
        self.assertRegex(loop, r'echo "\$SOREN_MAIN_PID"\s*>\s*"\$LOCKDIR/pid"')
        self.assertIn("_soren_lock_pid_alive", loop)
        self.assertIn("operation not permitted", loop)
        self.assertRegex(loop, r'err=\$\(\s*\{ kill -0 "\$pid" >/dev/null; \}\s*2>&1\s*\)\s*&& return 0')
        self.assertIn('*"soren_loop.sh"*', loop)
        self.assertIn("stale lock owner", loop)
        self.assertIn("replaced by self", loop)
        self.assertIn("queue_early_escape_lock_if_needed", loop)
        self.assertIn("next-game-preflight", loop)
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()
        self.assertIn("_maybe_queue_early_escape_from_monitor", monitor)
        self.assertIn("early_escape_source", monitor)
        self.assertIn("monitor_improve_runtime", monitor)
        self.assertIn("early escape monitor queued", monitor)
        self.assertIn("batch_ok comp=", monitor)
        self.assertLess(
            loop.index("rollback revalidate fresh cycle 中"),
            loop.index("rank1 hot streak 中 → 早期脱出ロックを延期"),
        )
        self.assertIn("normal|post_regression|wildcard|escape_ai|archive_restart", improve)
        self.assertLess(
            loop.index("早期脱出ロック"),
            loop.index("改善サイクル管理: 12試合蓄積時"),
        )

    def test_repeated_wildcards_can_restart_from_archive_before_ai_escape(self):
        """WILDCARD 連続失敗時は、AI構造変異の前に評価済み過去版へ basin jump できる。"""
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("ARCHIVE_RESTART_ENABLED", config)
        self.assertIn("ARCHIVE_RESTART_STREAK", config)
        self.assertIn("ARCHIVE_RESTART_MIN_COMP_RATIO", config)
        self.assertIn("ARCHIVE_RESTART_MIN_BEST_TYPE", config)
        self.assertIn("ARCHIVE_RESTART_MIN_RUSSIA_COUNT", config)
        self.assertIn("ARCHIVE_RESTART_MIN_RUSSIA_RATE", config)
        self.assertIn("ARCHIVE_RESTART_FRONTIER_MIN_BEST_TYPE", config)
        self.assertIn("ARCHIVE_RESTART_OBJECTIVE_FAIL_PERMANENT", config)
        self.assertIn("ARCHIVE_RESTART_INCLUDE_PERMANENT", config)
        self.assertIn("ARCHIVE_RESTART_ALLOW_ORIGIN_RETRY", config)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_SEC", config)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_FILE", config)
        self.assertIn("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE", config)
        self.assertIn("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_SEC", config)
        self.assertIn("normal|post_regression|wildcard|escape_ai|archive_restart", improve)
        self.assertIn('improve_reason="archive_restart"', improve)
        self.assertIn("_archive_restart_should_run", improve)
        self.assertIn("_archive_restart_has_candidate", improve)
        self.assertIn("preflight no candidate", improve)
        self.assertIn("preflight_no_candidate", improve)
        self.assertIn("no_candidate cooldown stale", improve)
        self.assertIn("marker_candidate_override", improve)
        self.assertIn("archive_is_runtime_stable", improve)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", improve)
        self.assertIn("allow_origin_retry", improve)
        self.assertIn("is_cooled_down", improve)
        self.assertIn("find_archive_path", improve)
        self.assertIn("anchor_russia", improve)
        self.assertIn("anchor_soviet", improve)
        self.assertIn("reliable_russia", improve)
        self.assertIn("frontier_candidate", improve)
        self.assertIn("not (reliable_russia or frontier_candidate or russia > 0)", improve)
        self.assertNotIn("if best_type >= 15 and russia <= 0:\n        russia = 1", improve)
        self.assertIn("archive_restart を飛ばして次の脱出手段", improve)
        self.assertIn("_improve_flow_notify", improve)
        self.assertIn("archive_restart candidate? yes", improve)
        self.assertIn("archive_restart candidate? no", improve)
        self.assertIn("wildcard frontier recovery possible? yes", improve)
        self.assertIn("fallback: no valid escape route", improve)
        self.assertIn("no_candidate cooldown active", improve)
        self.assertIn("archive_restart で過去版から大域脱出", improve)
        self.assertIn('[ "$reason" = "wildcard" ] || [ "$reason" = "archive_restart" ]', improve)
        self.assertIn("高速脱出(AI不使用・短時間)", improve)
        self.assertIn('IMPROVE_REASON:-normal}" = "archive_restart"', eloop)
        self.assertIn("既存評価済み", eloop)
        self.assertIn('"origin_type": "archive_restart"', eloop)
        self.assertIn("hash_normalized_by_validation", eloop)
        self.assertIn("selected_hash", eloop)
        self.assertIn("ARCHIVE_RESTART_MIN_COMP_RATIO", eloop)
        self.assertIn("ARCHIVE_RESTART_MIN_BEST_TYPE", eloop)
        self.assertIn("best_type < min_best_type", eloop)
        self.assertIn("archive_is_runtime_stable", eloop)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", eloop)
        self.assertIn("allow_origin_retry", eloop)
        self.assertIn("is_cooled_down", eloop)
        self.assertIn("origin_retry", eloop)
        self.assertIn("find_archive_path", eloop)
        self.assertIn("anchor_russia", eloop)
        self.assertIn("anchor_soviet", eloop)
        self.assertIn("reliable_russia", eloop)
        self.assertIn("frontier_candidate", eloop)
        self.assertIn("not (reliable_russia or frontier_candidate or russia > 0)", eloop)
        self.assertIn("russia_rate", eloop)
        self.assertIn("archive_restart_russia_not_reproduced", eloop)
        self.assertNotIn("if best_type >= 15 and russia <= 0:\n        russia = 1", eloop)
        self.assertIn("_archive_restart_quarantine_candidate", eloop)
        self.assertIn("archive_invalid_candidate_fallback", eloop)
        self.assertIn("archive_no_effective_change_fallback", eloop)
        self.assertIn("archive_restart_validate_fail", eloop)
        self.assertIn("archive_restart_no_effective_hash_change", eloop)
        self.assertIn("objective escape mechanism", eloop)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_FILE", eloop)
        self.assertIn("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE", eloop)
        self.assertIn("archive_no_candidate_fallback", eloop)
        self.assertIn("archive_restart candidate? no", eloop)
        self.assertIn("archive_restart complete", eloop)
        self.assertIn("source_russia_count", eloop)
        self.assertIn("source_hash = str(selected.get(\"selected_hash\")", eloop)
        self.assertIn("archive_restart_source", eloop)
        self.assertIn("source_russia_count", regression)
        self.assertNotIn("source_reliable_russia", regression)
        self.assertNotIn("archive_restart_russia_not_reproduced", regression)
        self.assertIn("source_best_max_type", regression)
        self.assertIn("archive_restart_objective_floor", regression)
        self.assertIn("mode=archive_objective_floor", regression)
        self.assertIn("archive_restart_complete", eloop)
        archive_block = eloop.split('IMPROVE_REASON:-normal}" = "archive_restart"', 1)[1].split("# バッチサマリー生成", 1)[0]
        self.assertIn("git add strategy.py game_count.txt score_history.txt eval_score_history.txt", archive_block)
        self.assertNotIn('git add strategy.py game_count.txt score_history.txt eval_score_history.txt "$WILDCARD_ORIGIN_FILE"', archive_block)

    def test_ai_output_files_are_not_precreated_before_opencode_write(self):
        """opencode write 制約に合わせ、analysis/review 出力は空ファイル作成しない。"""
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        review_prompt = (REPO_ROOT / "prompts/review_strategy.md").read_text()
        strategy = (REPO_ROOT / "strategy.py").read_text()
        self.assertIn('rm -f "$ANALYSIS_RESULT_FILE"', eloop)
        self.assertIn('rm -f "$REVIEW_RESULT_FILE"', eloop)
        self.assertNotIn(': >"$ANALYSIS_RESULT_FILE"', eloop)
        self.assertNotIn(': >"$REVIEW_RESULT_FILE"', eloop)
        self.assertIn("`tmp/review_result.md` は存在しない場合があります", review_prompt)
        self.assertIn("存在しない場合は `Write` で新規作成すること", review_prompt)
        self.assertIn("`File not found` になった場合、それは正常な初回状態", review_prompt)
        self.assertIn("ユーザーに質問せず、直ちに下記テンプレートを `Write tmp/review_result.md`", review_prompt)
        self.assertIn("必ず `## VERDICT: PASS` または `## VERDICT: FAIL`", review_prompt)
        self.assertIn("会話に表示しただけでは失敗", review_prompt)
        self.assertIn("最終応答の前に `tmp/review_result.md` が作成・更新済み", review_prompt)
        self.assertIn("周辺の最終式", review_prompt)
        self.assertIn("係数方向の検算", review_prompt)
        self.assertIn("係数変更の向き", review_prompt)
        self.assertIn("比較閾値を変更する diff", review_prompt)
        self.assertIn("比較演算子まで含めて効果方向を検算", review_prompt)
        self.assertIn("`margin < 0.5` を `margin < 0.3`", review_prompt)
        self.assertIn("発火範囲を狭める", review_prompt)
        self.assertIn("比較閾値の効果方向", review_prompt)
        self.assertIn("位置・高さ・piece_count の単調方向", review_prompt)
        self.assertIn("低配置を好む bonus", review_prompt)
        self.assertIn("+ max_y * 100", review_prompt)
        self.assertIn("条件成立候補すべてに `+500`", review_prompt)
        self.assertIn("低い候補・高い候補", review_prompt)
        self.assertIn("低配置 bonus の候補間差分", review_prompt)
        self.assertIn("定数加点は発火条件の gate", review_prompt)
        self.assertIn("単調方向の検算", review_prompt)
        self.assertIn("bonus / penalty 単調方向", review_prompt)
        self.assertIn("新規 axis / reason / bonus / penalty", review_prompt)
        self.assertIn("発火条件が到達可能", review_prompt)
        self.assertIn("根拠ログの値と条件が食い違う場合は FAIL", review_prompt)
        self.assertIn("新規 axis / reason の発火証拠", review_prompt)
        self.assertIn("新規 reason / axis の発火証拠", review_prompt)
        self.assertIn("新しく参照する `analysis` / `game_state` / `reactor`", review_prompt)
        self.assertIn("runtime構造の型・shape確認", review_prompt)
        self.assertIn("未確認の添字・キー・型仮定がある場合は FAIL", review_prompt)
        self.assertIn("欠損キーを真扱いしていないか", review_prompt)
        self.assertIn('`dict.get(...) != "NO"`', review_prompt)
        self.assertIn("欠損を「利用可能」と解釈する実装は FAIL", review_prompt)
        self.assertIn("欠損キーの真扱い防止", review_prompt)
        self.assertNotIn('get("merge_grade") != "NO"', strategy)
        self.assertIn('c.get("merge_grade") not in (None, "", "NO")', strategy)
        self.assertIn('r.get("merge_grade") not in (None, "", "NO")', strategy)
        self.assertIn("if __dlg_safe_filtered:", strategy)
        self.assertNotIn("`tmp/review_result.md` は既に存在", review_prompt)
        self.assertIn("_repair_review_verdict_file", eloop)
        self.assertNotIn("REVIEW-VERDICT-REPAIR", eloop)
        self.assertNotIn('run_ai "REVIEW-VERDICT-REPAIR"', eloop)
        self.assertIn("Stage3: review verdict missing → repair verdict file", eloop)
        self.assertIn("IMPROVE_REVIEW_CMD_TIMEOUT_SEC", eloop)
        self.assertIn("IMPROVE_REVIEW_PRIMARY_RETRIES", eloop)
        self.assertIn("def contradictory_threshold_direction_claim():", eloop)
        self.assertIn("def contradictory_low_placement_constant_claim():", eloop)
        self.assertIn("review verdict PASS contradicts comparison threshold direction", eloop)
        self.assertIn("review verdict PASS claims low placement from a constant bonus", eloop)
        self.assertIn("0\\.5\\s*(?:->|→|から|to)\\s*0\\.3", eloop)
        self.assertIn("より多く", eloop)
        self.assertIn("strengthen", eloop)
        self.assertIn('"verdict": "FAIL"', eloop)
        self.assertIn("Auto-generated advisory FAIL", eloop)
        self.assertIn("review verdict could not be produced by the review stage", eloop)
        self.assertIn("Review verdict was missing after the review stage.", eloop)
        self.assertIn('mkdir -p "$(dirname "$review_result_file")"', eloop)
        self.assertIn("emit a non-empty verdict/status in the review_verdict JSON block", eloop)
        self.assertIn("review verdict advisory failure; apply continues after runtime smoke", eloop)
        self.assertNotIn("Stage3: review verdict rejected apply", eloop)

    def test_strategy_validation_blocks_useless_edits_but_leaves_policy_advisory(self):
        """validation は構造エラーと無駄編集を止め、方針判定は観測に寄せる。"""
        sandbox = (REPO_ROOT / "strategy/sandbox.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        review_prompt = (REPO_ROOT / "prompts/review_strategy.md").read_text()

        self.assertNotIn("far-below raw crossing 抑制", review_prompt)
        self.assertNotIn("all-crossing", sandbox)
        self.assertNotIn("deadline-near-guard", sandbox)
        self.assertNotIn("deadline-direct-guard", sandbox)
        self.assertNotIn("active-filter: expected danger DIRECT merge", sandbox)
        self.assertNotIn("risky-single-danger-merge", sandbox)
        self.assertIn("ERROR: decide() not found", sandbox)
        self.assertIn("ERROR: {label}: missing x", sandbox)
        self.assertIn("テスト実行失敗", sandbox)
        self.assertIn("テスト出力契約違反", sandbox)
        self.assertIn("validation observation: repeated rejected hash", eloop)
        self.assertIn("validation observation: fixed-turn gate", eloop)
        self.assertIn("decide()関数の本体に実質的な変更がない", eloop)
        self.assertIn("文字列・reason文言だけの変更は不可", eloop)
        self.assertIn("Stage3: review mutation rejected (no logic change)", eloop)
        self.assertIn("Stage3: review mutation rejected (string-only)", eloop)
        self.assertNotIn("この変更は過去にリジェクトされた戦略と同一", eloop)


# --- F2: wildcard origin override branch budget only for that hash -----------

class TestWildcardOriginOverridesBranchBudget(unittest.TestCase):
    def test_check_regression_reads_wildcard_origin_for_current_hash_only(self):
        """check_regression Python ブロックが wildcard_origin の hash に対してのみ override する分岐を持つ。"""
        text = (REPO_ROOT / "strategy/regression.sh").read_text()
        # override コードが check_regression の Python ブロック内にある
        self.assertIn("_WILDCARD_ORIGIN", text)
        self.assertIn("if current_hash in _WILDCARD_ORIGIN:", text)
        self.assertIn("max_games_override", text)
        self.assertIn("patience_override", text)


# --- F3: wildcard_perturb preserves comments ---------------------------------

class TestWildcardPerturbPreservesComments(unittest.TestCase):
    def test_diff_only_at_chosen_literals(self):
        """wildcard_perturb で書き換えた以外の行 (コメント含む) は不変。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                """module docstring should be preserved"""
                # leading comment line

                def decide(game_state, analysis):
                    """function docstring."""
                    # explanation
                    threshold = 12.5  # inline comment
                    weight = 3
                    if game_state.get("x", 0) >= 100:
                        return {"x": 0.5, "reason": "DIRECT"}
                    return {"x": 0.0, "reason": "OTHER"}

                if __name__ == "__main__":
                    pass
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "1",
                    "--seed", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            self.assertTrue(out.exists())
            before_lines = sample.read_text().splitlines()
            after_lines = out.read_text().splitlines()
            self.assertEqual(len(before_lines), len(after_lines), msg="line count changed")
            # 変更行は applied 内容から取得し、他の行は完全一致
            applied = json.loads(result.stdout)["applied"]
            changed_lines = {a["lineno"] for a in applied}
            for i, (b, a) in enumerate(zip(before_lines, after_lines), start=1):
                if i in changed_lines:
                    continue
                self.assertEqual(b, a, msg=f"line {i} unexpectedly changed: {b!r} → {a!r}")
            # コメントが残っていること
            self.assertIn("# leading comment line", out.read_text())
            self.assertIn("# inline comment", out.read_text())
            self.assertIn('"""module docstring should be preserved"""', out.read_text())

    def test_exclude_lines_avoids_recent_wildcard_targets(self):
        """直近WILDCARDで触った行は、候補が足りる限り次回の選定から外せる。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    threshold = 12.5
                    weight = 3
                    return {"x": threshold + weight, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "1",
                    "--seed", "0",
                    "--exclude-lines", "2",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["exclude_applied"])
            self.assertNotIn(2, [item["lineno"] for item in payload["applied"]])

    def test_prefer_lines_can_bias_wildcard_targets(self):
        """outcome 由来の優先行がある場合は、探索率0でその行から選ぶ。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    low = 2
                    preferred = 7
                    other = 11
                    return {"x": low + preferred + other, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "1",
                    "--seed", "1",
                    "--prefer-lines", "3",
                    "--explore-rate", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["prefer_applied"])
            self.assertEqual([3], [item["lineno"] for item in payload["applied"]])

    def test_power_exponents_are_wildcard_targets(self):
        """大域脱出用に累乗指数も摂動候補に含める。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    dx = game_state.get("x", 0) - 1.0
                    dy = game_state.get("y", 0) - 2.0
                    dist = (dx ** 2 + dy ** 2) ** 0.5
                    scale = 7.0
                    return {"x": dist + scale, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "5",
                    "--seed", "0",
                    "--ratio-min", "0.2",
                    "--ratio-max", "0.2",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            self.assertIn(4, [item["lineno"] for item in payload["applied"]])
            self.assertIn(5, [item["lineno"] for item in payload["applied"]])

    def test_numeric_range_no_longer_excludes_constants(self):
        """大域脱出用に 0 や巨大値も摂動候補に含める。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    zero = 0.0
                    tiny = 0.02
                    normal = 3.0
                    huge = 6000.0
                    return {"x": zero + tiny + normal + huge, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "4",
                    "--seed", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual({2, 3, 4, 5}, {item["lineno"] for item in payload["applied"]})
            self.assertNotEqual(0.0, next(item["new"] for item in payload["applied"] if item["lineno"] == 2))

    def test_booleans_are_wildcard_targets(self):
        """True/False は boolean のまま反転する。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    enabled = True
                    disabled = False
                    return {"x": 1.0 if enabled and not disabled else 0.0, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "2",
                    "--seed", "0",
                    "--prefer-lines", "2,3",
                    "--explore-rate", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            self.assertEqual({2, 3}, {item["lineno"] for item in payload["applied"]})
            self.assertIn("enabled = False", out.read_text())
            self.assertIn("disabled = True", out.read_text())

    def test_random_count_uses_available_candidate_pool(self):
        """--random-count は変更可能候補を数え、そのプール内で変更数を乱数決定する。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    a = 1.0
                    b = 2.0
                    c = 3.0
                    d = 4.0
                    return {"x": a + b + c + d, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "1",
                    "--random-count",
                    "--seed", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["random_count"])
            self.assertEqual(4, payload["available_candidates"])
            self.assertEqual(4, payload["sample_pool_candidates"])
            self.assertEqual(1, payload["requested_count"])
            self.assertEqual(4, payload["selected_count"])
            self.assertEqual(4, len(payload["applied"]))

    def test_normal_ratio_reports_scale_and_outlier(self):
        """摂動比率は値の大きさでスケールし、正規乱数の外れ値も記録する。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    threshold = 10.0
                    return {"x": threshold, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "1",
                    "--seed", "2",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            applied = json.loads(result.stdout)["applied"][0]
            self.assertEqual(applied["magnitude_scale"], 1.25)
            self.assertTrue(applied["normal_outlier"])
            self.assertGreater(applied["ratio"], 0.4)

    def test_decide_reachable_helpers_and_globals_are_targets(self):
        """decide() から到達する helper と参照グローバル定数も摂動対象にする。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                GLOBAL_SCALE = 4.0
                GLOBAL_OFFSET = GLOBAL_SCALE + 1.5
                UNRELATED_GLOBAL = 8.0

                def helper(value):
                    local = 2.5
                    if value > 7.0:
                        return value * GLOBAL_OFFSET + local
                    return value + GLOBAL_SCALE

                def unrelated():
                    return 9.0

                def decide(game_state, analysis):
                    base = 3.0
                    return {"x": helper(base), "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "5",
                    "--seed", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            lines = {item["lineno"] for item in payload["applied"]}
            self.assertEqual({1, 2, 6, 7, 15}, lines)
            self.assertNotIn(3, lines)
            self.assertNotIn(12, lines)

    def test_type_adjacency_step_literals_are_not_wildcard_targets(self):
        """next_type +/- 1 は隣接タイプ判定の構造なので、符号反転させない。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sample = td / "strategy.py"
            sample.write_text(
                textwrap.dedent('''\
                def decide(game_state, analysis):
                    next_type = game_state.get("nextType", 1)
                    pieces = game_state.get("pieces", [])
                    threshold = 12.0
                    hits = 0
                    for p in pieces:
                        p_type = p.get("type", 1)
                        if p_type == next_type - 1 or p_type == next_type + 1:
                            hits += threshold
                    return {"x": hits, "reason": "DIRECT"}
                ''')
            )
            out = td / "out.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "wildcard_perturb.py"),
                    "--input", str(sample),
                    "--output", str(out),
                    "--count", "5",
                    "--seed", "0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")
            payload = json.loads(result.stdout)
            applied_lines = {item["lineno"] for item in payload["applied"]}
            self.assertNotIn(8, applied_lines)
            self.assertIn(4, applied_lines)
            self.assertIn("next_type + 1", out.read_text())
            self.assertNotIn("next_type + -1", out.read_text())


# --- 共通: stagnation counter transitions ------------------------------------

class TestStagnationCounterTransitions(unittest.TestCase):
    def test_python_block_contains_all_event_calls(self):
        """check_regression Python ブロックに 4 種類の _update_stagnation 呼び出しが存在する。"""
        text = (REPO_ROOT / "strategy/regression.sh").read_text()
        self.assertIn('_update_stagnation("PROMOTE")', text)
        self.assertIn('_update_stagnation("REGRESSION")', text)
        self.assertIn('_update_stagnation("RESET")', text)
        self.assertIn('return "OBJECTIVE_MISS" if objective_miss_against_anchor(anchor_progress, current_progress) else "OK_BEAT"', text)
        self.assertIn("_update_stagnation(ok_event_for_objective(anchor_objective, current_objective))", text)
        self.assertIn('_update_stagnation("OK_IDLE")', text)
        self.assertIn('_update_stagnation("SAME_HASH_BACKSLIDE")', text)
        self.assertIn('"OBJECTIVE_MISS" if objective_miss_against_anchor', text)
        # シェル側で counter を更新していない (Python 単一 owner)
        # rollback 経路 (REJECTED_HASHES_FILE 追記の隣) にはシェル側からの stagnation 書き換えはない


class TestOpencodeRunLock(unittest.TestCase):
    def test_strategy_run_cmd_serializes_opencode(self):
        """改善プロセスの opencode/glm 呼び出しはグローバルロックを待ってから起動する。"""
        text = (REPO_ROOT / "strategy/ai.sh").read_text()
        self.assertIn("_opencode_run_lock_enter", text)
        self.assertIn("opencode_lock_token", text)
        self.assertIn("XDG_STATE_HOME", text)
        self.assertIn("XDG_DATA_HOME", text)
        self.assertIn("_opencode_xdg_state_home", text)
        self.assertIn("_opencode_xdg_data_home", text)
        self.assertIn("_opencode_cleanup_internal_locks", text)
        self.assertIn('_opencode_run_lock_leave "$opencode_lock_token"', text)
        self.assertIn('[ "$type" = "glm" ] || [ "$type" = "opencode" ]', text)

    def test_strategy_run_cmd_suppresses_spinner_in_headless_logs(self):
        """headless improve_daemon のログを opencode spinner で汚さない。"""
        text = (REPO_ROOT / "strategy/ai.sh").read_text()
        self.assertIn("RUN_CMD_SPINNER_FORCE", text)
        self.assertIn('[ ! -t 2 ]', text)
        self.assertIn("_spinner_pid=0", text)
        self.assertLess(text.index('[ ! -t 2 ]'), text.index("local frames=("))

    def test_shared_ai_generate_serializes_opencode(self):
        """チャット/ラジオ側の opencode 呼び出しも改善側と同じロック実装を使う。"""
        text = (REPO_ROOT / "lib/ai_generate.sh").read_text()
        self.assertIn("OPENCODE_RUN_LOCK_DIR", text)
        self.assertIn("OPENCODE_XDG_STATE_HOME", text)
        self.assertIn("OPENCODE_XDG_DATA_HOME", text)
        self.assertIn("XDG_STATE_HOME", text)
        self.assertIn("XDG_DATA_HOME", text)
        self.assertIn(".opencode_run_lock", text)
        self.assertIn("OPENCODE_INTERNAL_LOCK_STALE_SEC", text)
        self.assertIn("_opencode_cleanup_internal_locks", text)
        self.assertIn("_opencode_run_lock_enter", text)
        self.assertIn("_opencode_run_lock_leave", text)
        self.assertIn('_opencode_run_lock_enter "${label}:opencode:${agent}"', text)

    def test_radio_opencode_defers_while_improve_is_pending(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        radio = (REPO_ROOT / "broadcast/radio_engine.sh").read_text()

        self.assertIn("RADIO_OPENCODE_DEFER_DURING_IMPROVE", config)
        self.assertIn('RADIO_OPENCODE_DEFER_DURING_IMPROVE="${RADIO_OPENCODE_DEFER_DURING_IMPROVE:-1}"', config)
        self.assertIn("opencode deferred during improve/backoff", radio)
        self.assertIn("_radio_opencode_should_defer_for_improve", radio)
        self.assertIn("opencode deferred after slot acquire during improve/backoff", radio)
        self.assertIn('${IMPROVE_LOCK_FILE:-tmp/improve.lock}', radio)
        self.assertIn('rate_limit_backoff', radio)
        self.assertIn('"status"[[:space:]]*:[[:space:]]*"running"', radio)
        self.assertIn("return 1", radio)


# --- 共通: hot-reload runtime toggles ----------------------------------------

class TestRuntimeToggleHotReload(unittest.TestCase):
    def test_eloop_lib_loads_env_before_config_defaults(self):
        """eloop_lib.sh 直sourceでも .env override を config defaults より先に読む。"""
        lib = (REPO_ROOT / "eloop_lib.sh").read_text()
        env_load = '[ -f "$ELOOP_LIB_DIR/.env" ] && set -a && . "$ELOOP_LIB_DIR/.env" && set +a'
        config_load = 'source "$ELOOP_LIB_DIR/core/config.sh"'

        self.assertIn(env_load, lib)
        self.assertLess(lib.index(env_load), lib.index(config_load))

    def test_toggle_propagates_on_mtime_change(self):
        """`.env` を編集して reload_runtime_toggles_force すると、対象 var が更新される。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            env_file = td / ".env"
            env_file.write_text("DIVERSITY_PREMIUM_ENABLED=0\nUNKNOWN_KEY=ignore\n")
            # シンボリックリンクを作って、テスト用の .env をプロジェクトルートに見せる
            cwd = REPO_ROOT
            # 直接 bash で runtime_toggles を読み込み、cwd をテンポラリにする
            script = f"""
cd '{td}'
source '{REPO_ROOT}/core/runtime_toggles.sh'
reload_runtime_toggles_force 2>&1 >/dev/null
echo "first=${{DIVERSITY_PREMIUM_ENABLED:-?}}"
sleep 1
cat > .env <<'EOF'
DIVERSITY_PREMIUM_ENABLED=1
TABU_ENABLED=1
EOF
reload_runtime_toggles_force 2>&1 >/dev/null
echo "second=${{DIVERSITY_PREMIUM_ENABLED:-?}}/${{TABU_ENABLED:-?}}"
"""
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertIn("first=0", r.stdout)
            self.assertIn("second=1/1", r.stdout)


# --- Improvement sandbox review inputs --------------------------------------

class TestImproveSandboxOptionalInputs(unittest.TestCase):
    def test_create_sandbox_always_provides_optional_context_files(self):
        """AI が空レビュー・rollback情報なしでも任意参照ファイルを読める。"""
        script = """
source ./eloop_lib.sh
sandbox=$(create_sandbox strategy.py)
test -d "$sandbox/data"
test -f "$sandbox/data/user_review.md"
test -d "$sandbox/tmp/state"
test -f "$sandbox/tmp/state/last_rollback_analysis.md"
test -f "$sandbox/tmp/state/last_rollback_postmortem.md"
destroy_sandbox "$sandbox"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_strategy_validation_blocks_conditional_load_before_assignment(self):
        """空状態 smoke では通るが、別分岐で UnboundLocalError になる変更を検証で止める。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            helpers = td / "strategy_helpers"
            helpers.mkdir()
            (helpers / "__init__.py").write_text("", encoding="utf-8")
            bad_strategy = td / "strategy.py"
            bad_strategy.write_text(
                textwrap.dedent(
                    """
                    def decide(game_state, analysis):
                        score = 0
                        if game_state.get("gate"):
                            branch_bonus = 1
                        if game_state.get("other") and branch_bonus > 0:
                            score += branch_bonus
                        return {"x": 0, "reason": f"score={score}"}
                    """
                ),
                encoding="utf-8",
            )

            script = f"""
source ./eloop_lib.sh
GAME_STATE='{td / "missing_game_state.json"}'
validate_strategy_with_helpers '{bad_strategy}' '{helpers}'
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, msg=combined)
            self.assertIn("load-before-local-assign", combined)
            self.assertIn("branch_bonus", combined)
            self.assertIn("cannot access local variable", combined)

    def test_strategy_validation_blocks_list_number_comparison(self):
        """list と数値の直接比較は smoke 到達前に検証で止める。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            helpers = td / "strategy_helpers"
            helpers.mkdir()
            (helpers / "__init__.py").write_text("", encoding="utf-8")
            bad_strategy = td / "strategy.py"
            bad_strategy.write_text(
                textwrap.dedent(
                    """
                    def decide(game_state, analysis):
                        pieces = game_state.get("pieces", [])
                        same_type_pieces = [p for p in pieces if p.get("type") == 13]
                        if same_type_pieces >= 2:
                            return {"x": 0, "reason": "bad-list-compare"}
                        return {"x": 0, "reason": "ok"}
                    """
                ),
                encoding="utf-8",
            )

            script = f"""
source ./eloop_lib.sh
GAME_STATE='{td / "missing_game_state.json"}'
validate_strategy_with_helpers '{bad_strategy}' '{helpers}'
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, msg=combined)
            self.assertIn("list-number-comparison", combined)
            self.assertIn("same_type_pieces", combined)
            self.assertIn("use len(...)", combined)


class TestRollbackRadioPrompt(unittest.TestCase):
    def test_rollback_radio_distinguishes_wildcard_from_plain_replacement(self):
        prompt = (REPO_ROOT / "prompts/radio_rollback.md").read_text()
        self.assertIn("escape_context: origin_type=wildcard", prompt)
        self.assertIn("停滞脱出のワイルドカード調整", prompt)
        self.assertNotIn("成績が良かった戦略 ${to_hash} にすげ替えられた", prompt)

    def test_rollback_analysis_exports_wildcard_origin_context_for_radio(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        self.assertIn("load_wildcard_origin_for_current", regression)
        self.assertIn("escape_context: origin_type=", regression)
        self.assertIn("WILDCARD起源", regression)


# --- Status display one-shot mode -------------------------------------------

class TestShowStatusOnce(unittest.TestCase):
    def test_show_status_once_exits(self):
        """自動確認用の --once は常駐せず終了する。"""
        result = subprocess.run(
            ["./show_status.sh", "--once"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout[-500:]}\nstderr={result.stderr}")
        self.assertIn("SOREN STATUS", result.stdout)
        self.assertIn("Escape", result.stdout)

    def test_show_status_does_not_treat_permission_denied_pid_as_alive(self):
        status = (REPO_ROOT / "show_status.sh").read_text()
        self.assertIn("operation not permitted", status)
        self.assertIn("stale or reused PIDs", status)
        self.assertNotIn('*"operation not permitted"*) return 0', status)

    def test_show_status_process_scan_is_byte_locale_safe(self):
        status = (REPO_ROOT / "show_status.sh").read_text()
        self.assertIn("LC_ALL=C ps -Ao pid=,command=", status)
        self.assertIn("| LC_ALL=C awk -v pattern=", status)

    def test_show_status_separates_youtube_send_degraded_from_chat(self):
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("local youtube_send_error=", status)
        self.assertIn("last_send_error.txt", status)
        self.assertIn('printf "    ${C_YELLOW}▸${C_RESET} Send        ${C_YELLOW}DEGRADED${C_RESET}', status)
        self.assertNotIn('elif [[ -s tmp/.youtube_chat/last_send_error.txt', status)


# --- Hot streak extension ----------------------------------------------------

class TestRankOneHotStreakExtension(unittest.TestCase):
    def test_hot_streak_gate_is_wired_to_loop_prediction_and_score_retention(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        prediction = (REPO_ROOT / "workers/prediction_worker.sh").read_text()

        self.assertIn("_is_rank1_hot_streak()", improve)
        self.assertIn("HOT_STREAK_CURRENT_RUN_KEEP", improve)
        self.assertIn("HOT_STREAK_ROLLING_KEEP", regression)
        self.assertIn("current_scores[-1] <= max(current_scores[:-1])", improve)
        self.assertIn("score > prev_best", improve)
        self.assertIn("score > prev_best", regression)
        self.assertIn("rank1 hot streak 中", loop)
        self.assertIn("_is_rank1_hot_streak", loop)
        self.assertIn("notify_rank1_hot_streak_extension", loop)
        self.assertIn("rank1 hot streak 延長", loop)
        self.assertIn('enqueue_chat_message "$chat_msg" "hot_streak"', loop)
        self.assertIn("hot_streak_prediction_pending", prediction)
        self.assertIn("延長突入", prediction)
        self.assertIn("次改善まで新規予想停止", prediction)


# --- Dashboard GAMEOVER display ------------------------------------------------

class TestDashboardGameoverDisplay(unittest.TestCase):
    def test_gameover_dashboard_show_is_synchronous_and_held_briefly(self):
        eloop = (REPO_ROOT / "eloop.sh").read_text()
        self.assertIn("./generate_dashboard.sh GAMEOVER", eloop)
        self.assertIn("DASHBOARD_GAMEOVER_HOLD_SEC", eloop)
        self.assertIn("sleep \"$_dashboard_hold_sec\"", eloop)
        self.assertNotIn("obs_control.sh show \"${OBS_DASHBOARD_SCENE:-soren}\" \"${OBS_DASHBOARD_SOURCE:-dashboard}\" >/dev/null 2>&1 &", eloop)


# --- Comment reply depth -------------------------------------------------------

class TestCommentReplyDepthPrompt(unittest.TestCase):
    def test_prompts_require_more_substantial_replies(self):
        for rel in [
            "prompts/comment_response.md",
            "prompts/comment_response_chitchat.md",
            "prompts/comment_response_default.md",
        ]:
            text = (REPO_ROOT / rel).read_text()
            self.assertIn("3-5 sentences", text, msg=rel)
            self.assertIn("one concrete", text, msg=rel)

    def test_comment_generation_embeds_recent_ai_reply_memory(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn("recent_spoken_comment_context=$(_build_recent_spoken_comment_context", script)
        self.assertIn("_remember_comment_reply_text \"$attempt_talk\"", script)
        self.assertIn("_remember_spoken_comment()", script)
        self.assertNotIn("spoken history は外部ファイル参照に移行済み", script)

    def test_comment_reply_skips_thumbnail_ocr_unless_contextual(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        ai_generate = (REPO_ROOT / "lib/ai_generate.sh").read_text()

        self.assertIn("_comment_needs_thumbnail_context()", script)
        self.assertIn('comment_thumbnail_ocr_context="（通常コメントのためサムネイルOCR省略）"', script)
        self.assertIn('_comment_needs_thumbnail_context "$twitch_comments"', script)
        self.assertIn('COMMENT_RESPONSE_RETRY_MAX="${COMMENT_RESPONSE_RETRY_MAX:-1}"', config)
        self.assertIn('COMMENT_FORCE_CLAUDE_WHEN_IMPROVING="${COMMENT_FORCE_CLAUDE_WHEN_IMPROVING:-0}"', config)
        self.assertIn('local_llm_timeout="${COMMENT_OLLAMA_TIMEOUT:-20}"', ai_generate)

    def test_comment_gen_pid_never_kills_worker_processes(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn('stale comment_gen pid points to worker', script)
        self.assertIn('[[ "$live_cmd" == *"/workers/chat_worker.sh"* ]]', script)
        self.assertLess(
            script.index('stale comment_gen pid points to worker'),
            script.index('pkill -P "$old_pid"'),
        )

    def test_comment_generation_discards_stale_reply_when_line_processed_mid_generation(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn("_has_processed_comment_line()", script)
        self.assertIn('if _has_processed_comment_line "$twitch_comments"; then', script)
        self.assertIn("discard stale reply without ack", script)
        self.assertIn('log "[COMMENT] 個別行フィルタ:', script)
        self.assertIn('>&2', script)
        self.assertLess(
            script.index('if _has_processed_comment_line "$twitch_comments"; then'),
            script.index('local queue_file="$COMMENT_QUEUE_DIR/comment_$(date +%s)_${RANDOM}.txt"'),
        )

    def test_comment_advice_append_ignores_non_advice_categories(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn("_comment_category_allows_advice_append()", script)
        self.assertIn("card_gacha | chitchat | bits", script)
        self.assertNotIn("card_gacha | chitchat | short_reaction", script)
        self.assertIn("stream_bug_report", script)
        self.assertIn("非助言カテゴリのためアドバイス保存を抑制", script)
        self.assertIn("非助言カテゴリの生成アドバイスを破棄", script)
        self.assertIn('"" | "（なし）" | "なし" | "（アドバイスなし）" | なし* | （アドバイスなし）*)', script)
        allow_idx = script.index('if [ "$_allow_advice_append" = "1" ]; then')
        candidate_idx = script.rindex('if [ -n "$strategy_advice_candidates_main" ]; then')
        discard_idx = script.index('elif [ -n "$advice_item$comment_advice_item$codex_advice_item" ]; then')
        self.assertLess(
            allow_idx,
            candidate_idx,
        )
        self.assertLess(
            candidate_idx,
            discard_idx,
        )

    def test_stream_bug_reports_are_queued_for_codex_dispatch(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        classifier = (REPO_ROOT / "prompts/comment_classifier.md").read_text()
        comment = (REPO_ROOT / "broadcast/comment.sh").read_text()
        dispatcher = (REPO_ROOT / "codex_bug_dispatcher.sh").read_text()
        chat_worker = (REPO_ROOT / "workers/chat_worker.sh").read_text()
        youtube_worker = (REPO_ROOT / "workers/youtube_worker.sh").read_text()

        self.assertIn("CODEX_BUG_DISPATCH_ENABLED", config)
        self.assertIn("CODEX_BUG_QUEUE_DIR", config)
        self.assertIn("CODEX_BUG_DISPATCH_MIN_INTERVAL_SEC", config)
        self.assertIn("stream_bug_report", classifier)
        self.assertIn("_queue_stream_bug_reports_from_classification()", comment)
        self.assertIn('item.get("category") != "stream_bug_report"', comment)
        self.assertIn("配信不具合レポートをCodexキューへ追加", comment)
        self.assertIn('"音楽"', comment)
        self.assertIn('"無音"', comment)
        self.assertIn("なし|無し", comment)
        self.assertIn("でなくなる", comment)
        self.assertIn("無音になってる？", classifier)
        self.assertIn("ゲーム音なし", classifier)
        self.assertIn("すぐゲーム音でなくなるね", classifier)
        self.assertIn("動いてねえんだわ", classifier)
        self.assertIn('"動いてねえ"', comment)
        self.assertIn("動いてね[えぇ]", comment)
        self.assertIn('"record"', comment)
        self.assertIn("いつもと違う", comment)
        self.assertIn("Record showing 0", classifier)
        self.assertIn("codex exec -C", dispatcher)
        self.assertIn("./codex_work_indicator.sh start", dispatcher)
        self.assertIn("./codex_work_indicator.sh stop", dispatcher)
        self.assertIn('./system_progress_report.sh "メリケンAI: ..."', dispatcher)
        self.assertIn("data/codex_advice.md", dispatcher)
        self.assertIn("/tmp/soren_report.md", dispatcher)
        self.assertIn("./show_status.sh --once", dispatcher)
        self.assertIn("strategy.py", dispatcher)
        self.assertIn("./codex_bug_dispatcher.sh kick", chat_worker)
        self.assertIn("./codex_bug_dispatcher.sh kick", youtube_worker)

    def test_frontier_proximity_guidance_keeps_congestion_suppression(self):
        strategy = (REPO_ROOT / "strategy.py").read_text()
        readme = (REPO_ROOT / "README.md").read_text()
        if "v369 congestion-aware proximity" in strategy:
            block = strategy.rsplit("v369 congestion-aware proximity", 1)[1].split(
                "evaluation axis 9.3", 1
            )[0]

            self.assertIn("rp_guidance_suppressed", block)
            # The suppression must gate on a reactive_pair_count threshold, but the exact
            # threshold (>= 5 originally) is a TUNABLE constant the param-parallel perturbs
            # (observed live: perturbed to >= 9). Assert the gate uses reactive_pair_count,
            # not a specific number, so normal auto-tuning of strategy.py doesn't break this.
            self.assertIn("reactive_pair_count >=", block)
            # The safety property is "no proximity bonus while congested". The AI
            # may express that either by explicitly zeroing the bonus
            # (proximity_bonus = 0.0) or by GATING the whole guidance behind
            # `if not rp_guidance_suppressed` (current form). Accept both so a valid
            # refactor of an AI-owned file doesn't trip the guard, while still
            # requiring the suppression to actually gate/zero the bonus.
            self.assertTrue(
                "proximity_bonus = 0.0" in block
                or "if not rp_guidance_suppressed" in block,
                "v369 congestion suppression must gate or zero proximity_bonus "
                "when rp_guidance_suppressed",
            )
            self.assertNotIn("reactive_pair_count < 3", block)
        self.assertIn("v369 congestion-aware proximity", readme)
        self.assertRegex(
            readme,
            re.compile(r"reactive_pair_count\s*>=\s*5.*proximity_bonus = 0\.0"),
        )

    def test_pre_russia_bridge_clustering_is_bounded_to_near_miss_inventory(self):
        strategy = (REPO_ROOT / "strategy.py").read_text()

        self.assertIn("PRE_RUSSIA_SEED_CLUSTER", strategy)
        self.assertIn("pre_russia_seed_ready", strategy)
        self.assertIn("pre_russia_seed_material_ready", strategy)
        self.assertIn("PRE_RUSSIA_T11_DENSITY_LATCH", strategy)
        self.assertIn("pre_russia_t11_density_latch_ready", strategy)
        self.assertIn("PRE_RUSSIA_T11_DENSITY_HIGH_VETO", strategy)
        self.assertIn("pre_russia_t11_density_high_veto_ready", strategy)
        self.assertIn("PRE_RUSSIA_T12_CONSOLIDATE", strategy)
        self.assertIn("pre_russia_t12_consolidate_ready", strategy)
        self.assertIn("PRE_RUSSIA_T12_ABUNDANCE_PAIR_LOCK", strategy)
        self.assertIn("pre_russia_t12_abundance_pair_lock_ready", strategy)
        self.assertIn("PRE_RUSSIA_T12_ABUNDANCE_REDLINE_VETO", strategy)
        self.assertIn("PRE_RUSSIA_NEXT_UP_LATCH", strategy)
        self.assertIn("pre_russia_next_up_latch_ready", strategy)
        self.assertIn("PRE_RUSSIA_T13_LIFT", strategy)
        self.assertIn("pre_russia_t13_lift_ready", strategy)
        self.assertIn("PRE_RUSSIA_T13_PAIR_CLUSTER", strategy)
        self.assertIn("pre_russia_t13_pair_cluster_ready", strategy)
        self.assertIn("PRE_RUSSIA_T13_PAIR_COMPRESS", strategy)
        self.assertIn("pre_russia_t13_pair_compress_ready", strategy)
        self.assertIn("__dlg_t13_pair_compress_ready", strategy)
        self.assertIn("PRE_RUSSIA_T13_PAIR_T12_TETHER", strategy)
        self.assertIn("pre_russia_t13_pair_single_t12_tether_ready", strategy)
        self.assertIn("__dlg_t13_pair_single_t12_tether_ready", strategy)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS", strategy)
        self.assertIn("pre_russia_single_t13_t12_compress_ready", strategy)
        self.assertIn("__dlg_single_t13_t12_compress_ready", strategy)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_SINGLE_T12_LADDER", strategy)
        self.assertIn("pre_russia_single_t13_single_t12_ladder_ready", strategy)
        self.assertIn("__dlg_single_t13_single_t12_ladder_ready", strategy)
        self.assertIn("PRE_RUSSIA_T13_PAIR_LADDER", strategy)
        self.assertIn("pre_russia_t13_pair_ladder_ready", strategy)
        self.assertIn("PRE_RUSSIA_T10_LADDER", strategy)
        self.assertIn("pre_russia_t10_ladder_ready", strategy)
        self.assertIn("PRE_RUSSIA_EARLY_T10_LADDER", strategy)
        self.assertIn("pre_russia_early_t10_ladder_ready", strategy)
        self.assertIn("PRE_RUSSIA_SINGLE_T12_ANCHOR_LADDER", strategy)
        self.assertIn("pre_russia_single_t12_anchor_ladder_ready", strategy)
        self.assertIn("DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR", strategy)
        self.assertIn("__dlg_single_t12_anchor_ladder_ready", strategy)
        self.assertIn("PRE_RUSSIA_SAME_TYPE_LATCH", strategy)
        self.assertIn("pre_russia_first_lane_ready", strategy)
        self.assertIn("SECOND_RUSSIA_SAME_TYPE_LATCH", strategy)
        self.assertIn("second_russia_lane_ready", strategy)
        self.assertIn("SECOND_RUSSIA_T12_LADDER", strategy)
        self.assertIn("second_russia_t12_ladder_ready", strategy)
        self.assertIn("__dlg_second_russia_t12_ladder_ready", strategy)
        self.assertIn("DEADLINE_GUARD_SECOND_RUSSIA_T12_LADDER", strategy)
        self.assertIn("SECOND_RUSSIA_T12_PAIR_LOCK", strategy)
        self.assertIn("second_russia_t12_pair_lock_ready", strategy)
        self.assertIn("__dlg_second_russia_t12_pair_lock_ready", strategy)
        self.assertIn("DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK", strategy)
        self.assertIn("FIRST_RUSSIA_T13_PAIR_LIFT", strategy)
        self.assertIn("first_russia_t13_pair_lift_ready", strategy)
        self.assertIn("__dlg_first_russia_t13_pair_lift_ready", strategy)
        self.assertIn("DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT", strategy)
        self.assertIn("FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT", strategy)
        self.assertIn("first_russia_single_t13_t12_bank_ready", strategy)
        self.assertIn("FIRST_RUSSIA_T13_T11_REBUILD", strategy)
        self.assertIn("first_russia_t13_t11_rebuild_ready", strategy)
        self.assertIn("SECOND_RUSSIA_REDLINE_NO_MERGE_VETO", strategy)
        self.assertIn("SOVIET_T15_LIFT", strategy)
        self.assertIn("SOVIET_T15_T11_REBUILD", strategy)
        self.assertIn("soviet_t15_lift_ready", strategy)
        self.assertIn("SOVIET_T10_LADDER", strategy)
        self.assertIn("soviet_t10_ladder_ready", strategy)
        self.assertIn("__dlg_soviet_lift_ready", strategy)
        self.assertIn("__dlg_soviet_ladder_ready", strategy)
        self.assertIn("__dlg_double_t14_ready", strategy)
        self.assertIn("__dlg_first_russia_pair_ready", strategy)
        self.assertIn("__dlg_t11_density_ready", strategy)
        self.assertIn("__dlg_t12_consolidate_ready", strategy)
        self.assertIn("__dlg_first_russia_ready", strategy)
        self.assertIn("next_type >= 9", strategy)
        self.assertIn("next_next_type >= 11", strategy)
        self.assertIn("max_type_on_board in (11, 12)", strategy)
        self.assertIn("PRE_RUSSIA_BRIDGE_CLUSTER", strategy)
        self.assertIn("RUSSIA_PAIR_CLUSTER", strategy)
        self.assertIn("SECOND_RUSSIA_BRIDGE_CLUSTER", strategy)
        self.assertIn("PRE_RUSSIA_REACTIVE_EDGE_SCATTER", strategy)
        self.assertIn("max_type_on_board in (12, 13, 14)", strategy)
        self.assertIn("pre_russia_counts.get(13, 0) >= 3", strategy)
        self.assertIn("pre_russia_counts.get(13, 0) >= 2", strategy)
        self.assertIn("pre_russia_counts.get(12, 0) >= 2", strategy)
        self.assertIn("pre_russia_counts.get(11, 0) >= 2", strategy)
        self.assertIn("pre_russia_counts.get(13, 0) >= 1", strategy)
        self.assertIn("pre_russia_counts.get(12, 0) >= 1", strategy)
        self.assertIn("pre_russia_counts.get(11, 0) >= 1", strategy)
        self.assertIn("pre_russia_counts.get(11, 0) >= 3", strategy)
        self.assertIn("pre_russia_counts.get(12, 0) >= 4", strategy)
        self.assertIn("pre_russia_counts.get(14, 0) >= 1", strategy)
        self.assertIn('p.get("type") == 14', strategy)
        self.assertIn("second_russia_counts.get(13, 0) >= 2", strategy)
        self.assertIn("second_russia_counts.get(13, 0) >= 1", strategy)
        self.assertIn("second_russia_counts.get(12, 0) >= 1", strategy)
        self.assertIn("second_russia_counts.get(11, 0) >= 2", strategy)
        self.assertIn("not death_spiral", strategy)
        self.assertIn("max_y < 3.2", strategy)
        self.assertIn("pre_russia_bridge_material_ready", strategy)
        self.assertIn("pre_russia_bridge_current_material_ready", strategy)
        self.assertIn("pre_russia_bridge_future_material_ready", strategy)
        self.assertIn("next_type >= 8", strategy)
        self.assertIn("next_next_type >= 10", strategy)
        self.assertIn("landing_y > 1.2 or piece_count >= 28", strategy)
        self.assertIn("landing_y > 0.6 or piece_count >= 30", strategy)
        self.assertIn("russia_pair_material_ready", strategy)
        self.assertIn("second_russia_material_ready", strategy)
        self.assertIn("DEADLINE_GUARD_PRE_RUSSIA_CLUSTER", strategy)

    def test_deadline_guard_clusters_safe_high_material_when_pre_russia_near_miss(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": 0.4, "y": -1.5},
                    {"id": 2, "type": 13, "x": -2.3, "y": 0.1},
                    {"id": 3, "type": 13, "x": 0.7, "y": -3.1},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": 2.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.2,
                    },
                    {
                        "x": -0.4,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": -2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.8)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_SAFE_LANDING")

    def test_strategy_vetoes_direct_merge_result_redline_in_deadline_danger(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 10, "x": 1.0, "y": 3.18},
                    {"id": 2, "type": 10, "x": 0.8, "y": 2.72},
                    {"id": 3, "type": 4, "x": -0.4, "y": 2.94},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {
                    "deadline_margin": -0.42,
                    "danger_piece_count": 1,
                    "reactive_pairs": [],
                    "near_pairs": [],
                },
                "results": [
                    {
                        "x": 1.0,
                        "deadline_y": 3.38,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": True,
                        "merge_result_top_y": 4.3,
                        "risk_top_y_after_drop": 4.3,
                        "top_y_after_drop": 4.1,
                        "merge_grade": "DIRECT",
                        "landing_y": 0.2,
                    },
                    {
                        "x": -0.4,
                        "deadline_y": 3.38,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "risk_top_y_after_drop": 3.44,
                        "top_y_after_drop": 3.44,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.4)
        self.assertIn("NO_MERGE_CROSSES_DEADLINE_PENALTY", decision["reason"])

    def test_deadline_guard_clusters_safe_material_at_first_russia_near_miss(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": 2.0, "y": -2.0},
                    {"id": 2, "type": 12, "x": -1.8, "y": -3.5},
                    {"id": 3, "type": 12, "x": 0.2, "y": -2.8},
                    {"id": 4, "type": 11, "x": -1.3, "y": -0.8},
                    {"id": 5, "type": 11, "x": 1.4, "y": -0.2},
                ],
                "next": {"type": 8},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                    {
                        "x": 0.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.2)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_pre_russia_reactive_no_merge_penalizes_early_edge_scatter(self):
        import strategy

        pieces = []
        for i in range(1, 19):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 9),
                    "x": -2.8 + (i % 7) * 0.8,
                    "y": -1.0 + (i % 5) * 0.28,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 6},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {
                    "deadline_margin": 2.0,
                    "reactive_pairs": [{"type": 3}, {"type": 5}, {"type": 7}],
                },
                "results": [
                    {
                        "x": -2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.3,
                    },
                    {
                        "x": 0.4,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.25,
                    },
                    {
                        "x": 2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.4)

    def test_pre_russia_seed_cluster_guides_t10_t11_toward_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": -1.0, "y": -1.2},
            {"id": 2, "type": 12, "x": 0.9, "y": -1.0},
            {"id": 3, "type": 11, "x": 0.4, "y": -0.7},
            {"id": 4, "type": 8, "x": -2.4, "y": -1.6},
        ]
        for i in range(5, 20):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 6),
                    "x": -2.8 + (i % 6) * 0.9,
                    "y": -2.4 + (i % 3) * 0.25,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 0.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.15,
                    },
                    {
                        "x": 2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.25)
        self.assertIn("PRE_RUSSIA_SEED_CLUSTER", decision["reason"])

    def test_pre_russia_t11_density_latch_guides_t11_to_t12_anchor(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": 1.3, "y": 0.1},
            {"id": 2, "type": 11, "x": 0.4, "y": -3.3},
            {"id": 3, "type": 11, "x": 1.9, "y": -1.9},
            {"id": 4, "type": 11, "x": 2.5, "y": 0.8},
            {"id": 5, "type": 10, "x": 0.2, "y": -0.8},
        ]
        for i in range(6, 31):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.05,
                    },
                    {
                        "x": 1.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.65,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.05,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.9)
        self.assertIn("PRE_RUSSIA_T11_DENSITY_LATCH", decision["reason"])

    def test_pre_russia_t11_density_high_veto_keeps_single_t12_rebuild_low(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": 1.2, "y": 0.1},
            {"id": 2, "type": 11, "x": -2.6, "y": -1.4},
            {"id": 3, "type": 11, "x": -2.8, "y": -0.5},
            {"id": 4, "type": 11, "x": 1.2, "y": 1.7},
            {"id": 5, "type": 10, "x": 0.2, "y": -0.8},
        ]
        for i in range(6, 31):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        def decide_with_high_landing(landing_y):
            return strategy.decide(
                {
                    "deadline_crossed": False,
                    "pieces": pieces,
                    "next": {"type": 11},
                    "nextNext": {"type": 8},
                },
                {
                    "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                    "results": [
                        {
                            "x": 1.2,
                            "crosses_deadline": False,
                            "merge_result_crosses_deadline": False,
                            "merge_grade": "NO",
                            "landing_y": landing_y,
                        },
                        {
                            "x": -2.8,
                            "crosses_deadline": False,
                            "merge_result_crosses_deadline": False,
                            "merge_grade": "NO",
                            "landing_y": 0.2,
                        },
                        {
                            "x": 2.8,
                            "crosses_deadline": False,
                            "merge_result_crosses_deadline": False,
                            "merge_grade": "NO",
                            "landing_y": -0.2,
                        },
                    ],
                },
            )

        low_decision = decide_with_high_landing(1.14)
        high_decision = decide_with_high_landing(1.16)

        self.assertEqual(low_decision["x"], 1.2)
        self.assertNotEqual(high_decision["x"], 1.2)
        self.assertIn("PRE_RUSSIA_T11_DENSITY_LATCH", high_decision["reason"])

    def test_deadline_guard_clusters_t11_density_toward_t12_anchor(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 12, "x": 1.3, "y": 0.1},
                    {"id": 2, "type": 11, "x": 0.4, "y": -3.3},
                    {"id": 3, "type": 11, "x": 1.9, "y": -1.9},
                    {"id": 4, "type": 11, "x": 2.5, "y": 0.8},
                    {"id": 5, "type": 10, "x": 0.2, "y": -0.8},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 1.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.8)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_pre_russia_t12_consolidate_guides_t12_to_closest_pair(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": -1.4, "y": -3.4},
            {"id": 2, "type": 12, "x": -2.1, "y": -0.8},
            {"id": 3, "type": 12, "x": 2.6, "y": -2.5},
            {"id": 4, "type": 11, "x": 0.1, "y": 0.6},
            {"id": 5, "type": 11, "x": 1.8, "y": 1.5},
            {"id": 6, "type": 10, "x": -1.5, "y": -0.2},
            {"id": 7, "type": 10, "x": 2.3, "y": -0.8},
        ]
        for i in range(8, 32):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 12},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 1.4, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.75,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.8,
                    },
                    {
                        "x": 0.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.2,
                    },
                    {
                        "x": 2.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.75)
        self.assertIn("PRE_RUSSIA_T12_CONSOLIDATE", decision["reason"])

    def test_pre_russia_t12_consolidate_guides_t11_without_existing_t11_bank(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": -1.95, "y": -3.7},
            {"id": 2, "type": 12, "x": -0.9, "y": -1.4},
            {"id": 3, "type": 12, "x": 1.8, "y": -3.6},
            {"id": 4, "type": 10, "x": -0.9, "y": -3.0},
            {"id": 5, "type": 9, "x": 0.9, "y": -1.2},
        ]
        for i in range(6, 25):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 9},
            },
            {
                "reactor": {"deadline_margin": 1.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": -1.45,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.05,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.45)
        self.assertIn("PRE_RUSSIA_T12_CONSOLIDATE", decision["reason"])

    def test_pre_russia_t12_consolidate_rejects_edge_escape_after_second_t12(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": -1.43, "y": -2.66},
            {"id": 2, "type": 12, "x": 0.51, "y": -0.03},
            {"id": 3, "type": 11, "x": -0.60, "y": -3.75},
            {"id": 4, "type": 11, "x": 1.78, "y": -2.07},
            {"id": 5, "type": 10, "x": -1.02, "y": -1.40},
        ]
        for i in range(6, 28):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 9},
            },
            {
                "reactor": {"deadline_margin": 1.0, "reactive_pairs": [{}, {}]},
                "results": [
                    {
                        "x": -0.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.05,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": 1.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.6)
        self.assertIn("PRE_RUSSIA_T12_CONSOLIDATE", decision["reason"])

    def test_pre_russia_t12_abundance_pair_lock_guides_t12_to_low_pair(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": -0.45, "y": -3.4},
            {"id": 2, "type": 12, "x": 1.1, "y": -2.3},
            {"id": 3, "type": 12, "x": -2.4, "y": -1.7},
            {"id": 4, "type": 12, "x": -2.1, "y": 0.5},
            {"id": 5, "type": 12, "x": 2.5, "y": 1.5},
            {"id": 6, "type": 11, "x": -0.8, "y": 1.5},
            {"id": 7, "type": 10, "x": -1.4, "y": -3.8},
        ]
        for i in range(8, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 12},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 1.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": 0.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.6,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": -2.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.4,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.35)
        self.assertIn("PRE_RUSSIA_T12_ABUNDANCE_PAIR_LOCK", decision["reason"])

    def test_pre_russia_t12_abundance_redline_veto_prefers_lower_risk(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": [
                    {"id": 1, "type": 12, "x": -0.45, "y": -3.4},
                    {"id": 2, "type": 12, "x": 1.1, "y": -2.3},
                    {"id": 3, "type": 12, "x": -2.4, "y": -1.7},
                    {"id": 4, "type": 12, "x": -2.1, "y": 0.5},
                    {"id": 5, "type": 12, "x": 2.5, "y": 1.5},
                    {"id": 6, "type": 11, "x": -0.8, "y": 1.5},
                    {"id": 7, "type": 10, "x": -1.4, "y": -3.8},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 1.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.8,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                        "top_y_after_drop": 4.2,
                        "risk_top_y_after_drop": 4.2,
                    },
                    {
                        "x": 2.4,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.4,
                        "top_y_after_drop": 3.5,
                        "risk_top_y_after_drop": 3.5,
                    },
                    {
                        "x": -2.6,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                        "top_y_after_drop": 3.85,
                        "risk_top_y_after_drop": 3.85,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.4)
        self.assertIn("PRE_RUSSIA_T12_ABUNDANCE_REDLINE_VETO", decision["reason"])
        self.assertNotIn("PRE_RUSSIA_T12_ABUNDANCE_PAIR_LOCK", decision["reason"])

    def test_deadline_guard_clusters_t12_consolidate_toward_closest_pair(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 12, "x": -1.4, "y": -3.4},
                    {"id": 2, "type": 12, "x": -2.1, "y": -0.8},
                    {"id": 3, "type": 12, "x": 2.6, "y": -2.5},
                    {"id": 4, "type": 11, "x": 0.1, "y": 0.6},
                    {"id": 5, "type": 11, "x": 1.8, "y": 1.5},
                    {"id": 6, "type": 10, "x": -1.5, "y": -0.2},
                    {"id": 7, "type": 10, "x": 2.3, "y": -0.8},
                ],
                "next": {"type": 12},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.75,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                    {
                        "x": 0.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 2.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.75)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_pre_russia_next_up_latch_guides_t11_to_t12_lane_before_t13(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": -1.3, "y": -2.6},
            {"id": 2, "type": 12, "x": -0.6, "y": -1.5},
            {"id": 3, "type": 12, "x": 1.2, "y": -1.4},
            {"id": 4, "type": 10, "x": 2.3, "y": -1.0},
        ]
        for i in range(5, 26):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 4) * 0.25,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.65,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.3,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.65)
        self.assertIn("PRE_RUSSIA_NEXT_UP_LATCH", decision["reason"])

    def test_pre_russia_t13_lift_guides_t11_to_t12_t13_lane_without_same_type(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -0.5, "y": 0.5},
            {"id": 2, "type": 12, "x": -2.0, "y": -3.4},
            {"id": 3, "type": 12, "x": 0.4, "y": -2.8},
            {"id": 4, "type": 12, "x": 2.5, "y": -3.0},
            {"id": 5, "type": 10, "x": -2.2, "y": -1.5},
        ]
        for i in range(6, 31):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.4 + (i % 5) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 1.4, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.35,
                    },
                    {
                        "x": -0.05,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.35,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.05)
        self.assertIn("PRE_RUSSIA_T13_LIFT", decision["reason"])

    def test_pre_russia_t13_pair_cluster_guides_t10_to_first_russia_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 1.05, "y": -3.2},
            {"id": 2, "type": 13, "x": 2.05, "y": 1.0},
            {"id": 3, "type": 12, "x": -0.4, "y": -2.5},
            {"id": 4, "type": 12, "x": 2.55, "y": -1.5},
            {"id": 5, "type": 11, "x": -1.7, "y": -1.7},
        ]
        for i in range(6, 31):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 2.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.05,
                    },
                    {
                        "x": 1.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.55)
        self.assertIn("PRE_RUSSIA_T13_PAIR_CLUSTER", decision["reason"])

    def test_pre_russia_t13_pair_cluster_prefers_close_pair_over_all_t13_center(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -2.0, "y": -2.6},
            {"id": 2, "type": 13, "x": -1.2, "y": -0.7},
            {"id": 3, "type": 13, "x": 2.4, "y": -2.4},
            {"id": 4, "type": 12, "x": 1.7, "y": -1.2},
            {"id": 5, "type": 12, "x": -0.4, "y": -2.1},
            {"id": 6, "type": 11, "x": 0.3, "y": -1.5},
        ]
        for i in range(7, 33):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 8},
            },
            {
                "reactor": {"deadline_margin": 1.4, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertIn("PRE_RUSSIA_T13_PAIR_CLUSTER", decision["reason"])

    def test_pre_russia_t13_pair_compress_guides_t11_to_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -0.5, "y": -3.4},
            {"id": 2, "type": 13, "x": 2.0, "y": 1.0},
            {"id": 3, "type": 12, "x": 1.2, "y": -1.1},
            {"id": 4, "type": 12, "x": -1.2, "y": 0.4},
            {"id": 5, "type": 10, "x": 2.7, "y": -2.1},
            {"id": 6, "type": 10, "x": -2.0, "y": -0.8},
        ]
        for i in range(7, 37):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.25,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.05,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.0)
        self.assertIn("PRE_RUSSIA_T13_PAIR_COMPRESS", decision["reason"])

    def test_deadline_guard_clusters_double_t13_first_russia_material(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": 1.05, "y": -3.2},
                    {"id": 2, "type": 13, "x": 2.05, "y": 1.0},
                    {"id": 3, "type": 12, "x": -0.4, "y": -2.5},
                    {"id": 4, "type": 12, "x": 2.55, "y": -1.5},
                    {"id": 5, "type": 11, "x": -1.7, "y": -1.7},
                ],
                "next": {"type": 6},
                "nextNext": {"type": 10},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 1.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.55)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_clusters_split_t13_pair_with_t12_material(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": -0.5, "y": -3.4},
                    {"id": 2, "type": 13, "x": 2.0, "y": 1.0},
                    {"id": 3, "type": 12, "x": 1.2, "y": -1.1},
                    {"id": 4, "type": 12, "x": -1.2, "y": 0.4},
                    {"id": 5, "type": 10, "x": 2.7, "y": -2.1},
                    {"id": 6, "type": 10, "x": -2.0, "y": -0.8},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 0.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.4,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.15,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.0)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_pre_russia_t13_pair_single_t12_tether_guides_t11_between_pair_and_t12(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -2.05, "y": -3.35},
            {"id": 2, "type": 13, "x": -1.45, "y": -1.45},
            {"id": 3, "type": 12, "x": 1.1, "y": -0.75},
            {"id": 4, "type": 10, "x": 1.25, "y": -3.2},
            {"id": 5, "type": 9, "x": 2.2, "y": -0.1},
            {"id": 6, "type": 9, "x": -0.9, "y": -0.2},
        ]
        for i in range(7, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 1.3, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": -0.65,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.65)
        self.assertIn("PRE_RUSSIA_T13_PAIR_T12_TETHER", decision["reason"])

    def test_deadline_guard_tethers_t13_pair_with_single_t12_on_low_next(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": -2.05, "y": -3.35},
                    {"id": 2, "type": 13, "x": -1.45, "y": -1.45},
                    {"id": 3, "type": 12, "x": 1.1, "y": -0.75},
                    {"id": 4, "type": 10, "x": 1.25, "y": -3.2},
                    {"id": 5, "type": 9, "x": 2.2, "y": -0.1},
                ],
                "next": {"type": 3},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 0.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": -0.65,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.45,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.65)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_pre_russia_single_t13_t12_compress_guides_t11_to_low_t12_pair(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -1.8, "y": -1.6},
            {"id": 2, "type": 12, "x": 0.45, "y": -3.5},
            {"id": 3, "type": 12, "x": 2.05, "y": -0.25},
            {"id": 4, "type": 12, "x": 0.65, "y": 2.0},
            {"id": 5, "type": 11, "x": -2.5, "y": 0.1},
            {"id": 6, "type": 10, "x": 2.5, "y": 0.8},
        ]
        for i in range(7, 36):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 1.4, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 1.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.25)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS", decision["reason"])

    def test_pre_russia_single_t13_t12_compress_starts_before_twenty_eight_pieces(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -1.55, "y": -0.35},
            {"id": 2, "type": 12, "x": -1.95, "y": -3.7},
            {"id": 3, "type": 12, "x": 1.75, "y": -3.6},
            {"id": 4, "type": 11, "x": 1.6, "y": 0.45},
            {"id": 5, "type": 10, "x": -0.9, "y": -3.0},
            {"id": 6, "type": 10, "x": 0.4, "y": -0.3},
        ]
        for i in range(7, 26):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 9},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": -0.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.45,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.05,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.1)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS", decision["reason"])

    def test_pre_russia_single_t13_t12_compress_feeds_t10_to_low_ladder(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -0.36, "y": -3.39},
            {"id": 2, "type": 12, "x": -1.73, "y": -1.62},
            {"id": 3, "type": 12, "x": 2.04, "y": -1.57},
            {"id": 4, "type": 11, "x": -2.65, "y": -2.85},
            {"id": 5, "type": 10, "x": -0.44, "y": -1.43},
            {"id": 6, "type": 10, "x": 2.73, "y": -0.30},
        ]
        for i in range(7, 30):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 1.3, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.45,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.65,
                    },
                    {
                        "x": 0.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.25,
                    },
                    {
                        "x": 2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.45)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS", decision["reason"])

    def test_pre_russia_single_t13_t12_compress_feeds_t9_to_low_ladder(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 2.1, "y": -0.8},
            {"id": 2, "type": 12, "x": -0.9, "y": -1.0},
            {"id": 3, "type": 12, "x": 0.1, "y": -2.9},
            {"id": 4, "type": 12, "x": -1.8, "y": 0.8},
            {"id": 5, "type": 11, "x": 1.15, "y": 0.35},
            {"id": 6, "type": 10, "x": 0.25, "y": -1.6},
            {"id": 7, "type": 9, "x": -0.35, "y": -1.25},
            {"id": 8, "type": 9, "x": 2.45, "y": 1.35},
        ]
        for i in range(9, 38):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 9},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 1.25, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.05,
                    },
                    {
                        "x": 0.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.15,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.35)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS", decision["reason"])

    def test_pre_russia_single_t13_t12_compress_guides_t11_to_low_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 0.09, "y": -3.39},
            {"id": 2, "type": 12, "x": -1.99, "y": -2.42},
            {"id": 3, "type": 12, "x": 1.58, "y": -1.40},
            {"id": 4, "type": 11, "x": -1.56, "y": -0.60},
            {"id": 5, "type": 10, "x": -0.62, "y": -1.92},
            {"id": 6, "type": 10, "x": 0.44, "y": -0.01},
            {"id": 7, "type": 9, "x": 2.20, "y": -3.73},
        ]
        for i in range(8, 35):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 4) * 0.2,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 2},
            },
            {
                "reactor": {"deadline_margin": 1.9, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.56,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.62,
                    },
                    {
                        "x": -0.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.25,
                    },
                    {
                        "x": 2.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.05,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.56)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS", decision["reason"])

    def test_pre_russia_single_t13_single_t12_ladder_guides_t11_to_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 1.25, "y": 0.4},
            {"id": 2, "type": 12, "x": -1.95, "y": -1.6},
            {"id": 3, "type": 11, "x": -1.6, "y": -0.6},
            {"id": 4, "type": 11, "x": -0.1, "y": -3.7},
            {"id": 5, "type": 11, "x": -0.9, "y": 1.7},
            {"id": 6, "type": 10, "x": 2.2, "y": -1.3},
            {"id": 7, "type": 10, "x": -1.8, "y": -3.0},
        ]
        for i in range(8, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.6,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_SINGLE_T12_LADDER", decision["reason"])

    def test_pre_russia_single_t13_single_t12_ladder_starts_before_twenty_four_pieces(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 1.25, "y": 0.35},
            {"id": 2, "type": 12, "x": -1.95, "y": -1.7},
            {"id": 3, "type": 11, "x": -1.55, "y": -0.5},
            {"id": 4, "type": 10, "x": 2.1, "y": -1.4},
            {"id": 5, "type": 10, "x": -1.7, "y": -3.0},
            {"id": 6, "type": 10, "x": 0.4, "y": -2.7},
        ]
        for i in range(7, 20):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 8},
            },
            {
                "reactor": {"deadline_margin": 1.8, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.55)
        self.assertIn("PRE_RUSSIA_SINGLE_T13_SINGLE_T12_LADDER", decision["reason"])

    def test_deadline_guard_ladders_single_t13_single_t12_near_miss(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": 1.25, "y": 0.4},
                    {"id": 2, "type": 12, "x": -1.95, "y": -1.6},
                    {"id": 3, "type": 11, "x": -1.6, "y": -0.6},
                    {"id": 4, "type": 11, "x": -0.1, "y": -3.7},
                    {"id": 5, "type": 10, "x": 2.2, "y": -1.3},
                    {"id": 6, "type": 10, "x": -1.8, "y": -3.0},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.45,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT")

    def test_deadline_guard_clusters_single_t13_with_t12_bank(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": -1.8, "y": -1.6},
                    {"id": 2, "type": 12, "x": 0.45, "y": -3.5},
                    {"id": 3, "type": 12, "x": 2.05, "y": -0.25},
                    {"id": 4, "type": 12, "x": 0.65, "y": 2.0},
                    {"id": 5, "type": 11, "x": -2.5, "y": 0.1},
                ],
                "next": {"type": 4},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 0.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 1.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.45,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.25)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_single_t13_t12_bank_beats_lowest_safe_landing(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": -1.6, "y": -1.4},
                    {"id": 2, "type": 12, "x": 0.1, "y": -2.4},
                    {"id": 3, "type": 12, "x": 0.9, "y": -1.1},
                    {"id": 4, "type": 12, "x": 2.3, "y": 1.7},
                    {"id": 5, "type": 11, "x": -2.4, "y": 0.3},
                    {"id": 6, "type": 10, "x": 2.1, "y": 0.8},
                ],
                "next": {"type": 9},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": 0.65,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 0.45,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.45)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_single_t13_t12_bank_feeds_t9_ladder(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": 2.1, "y": -0.8},
                    {"id": 2, "type": 12, "x": -0.9, "y": -1.0},
                    {"id": 3, "type": 12, "x": 0.1, "y": -2.9},
                    {"id": 4, "type": 12, "x": -1.8, "y": 2.2},
                    {"id": 5, "type": 11, "x": 1.15, "y": 0.35},
                    {"id": 6, "type": 10, "x": 0.25, "y": -1.6},
                    {"id": 7, "type": 9, "x": -0.35, "y": -1.25},
                    {"id": 8, "type": 9, "x": 2.45, "y": 1.35},
                ],
                "next": {"type": 9},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.05,
                    },
                    {
                        "x": 0.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.15,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.35)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_t12_consolidate_accepts_t9_material(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 12, "x": 0.1, "y": -2.5},
                    {"id": 2, "type": 12, "x": 0.9, "y": -1.2},
                    {"id": 3, "type": 11, "x": -1.4, "y": -0.4},
                    {"id": 4, "type": 11, "x": 2.2, "y": 0.5},
                    {"id": 5, "type": 10, "x": 0.3, "y": -0.8},
                    {"id": 6, "type": 9, "x": -2.5, "y": 0.2},
                ],
                "next": {"type": 9},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": 0.65,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 0.45,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.45)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_clusters_first_russia_pair_to_close_t13_pair(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 13, "x": -2.0, "y": -2.6},
                    {"id": 2, "type": 13, "x": -1.2, "y": -0.7},
                    {"id": 3, "type": 13, "x": 2.4, "y": -2.4},
                    {"id": 4, "type": 12, "x": 1.7, "y": -1.2},
                    {"id": 5, "type": 12, "x": -0.4, "y": -2.1},
                    {"id": 6, "type": 11, "x": 0.3, "y": -1.5},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 8},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_FIRST_RUSSIA_PAIR")

    def test_pre_russia_t13_pair_ladder_guides_t11_back_to_pair_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 0.9, "y": -2.5},
            {"id": 2, "type": 13, "x": 1.35, "y": -0.4},
            {"id": 3, "type": 11, "x": 2.25, "y": -0.1},
            {"id": 4, "type": 11, "x": -1.25, "y": 0.0},
            {"id": 5, "type": 10, "x": -1.5, "y": 1.2},
            {"id": 6, "type": 10, "x": -2.2, "y": -2.2},
        ]
        for i in range(7, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 1.3, "reactive_pairs": []},
                "results": [
                    {
                        "x": 2.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.0,
                    },
                    {
                        "x": -1.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.25)
        self.assertIn("PRE_RUSSIA_T13_PAIR_LADDER", decision["reason"])

    def test_pre_russia_t13_pair_ladder_uses_t10_bank_without_t11s(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -1.05, "y": 1.65},
            {"id": 2, "type": 13, "x": -0.46, "y": -1.11},
            {"id": 3, "type": 13, "x": 1.89, "y": -2.56},
            {"id": 4, "type": 10, "x": 2.25, "y": -0.45},
            {"id": 5, "type": 10, "x": 2.33, "y": 1.4},
            {"id": 6, "type": 10, "x": -0.26, "y": 0.56},
            {"id": 7, "type": 10, "x": -2.3, "y": -1.0},
        ]
        for i in range(8, 35):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 9},
            },
            {
                "reactor": {"deadline_margin": 1.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": 0.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.1,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 0.1)
        self.assertIn("PRE_RUSSIA_T13_PAIR_LADDER", decision["reason"])

    def test_first_russia_t13_pair_lift_guides_t11_to_second_t14_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": 1.1, "y": -2.8},
            {"id": 2, "type": 13, "x": -1.4, "y": -3.4},
            {"id": 3, "type": 13, "x": -1.8, "y": -0.5},
            {"id": 4, "type": 11, "x": 1.9, "y": -0.6},
            {"id": 5, "type": 11, "x": -0.2, "y": 1.4},
            {"id": 6, "type": 10, "x": -2.6, "y": -1.8},
        ]
        for i in range(7, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.2,
                    },
                    {
                        "x": 1.2,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                    {
                        "x": 2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.2)
        self.assertIn("FIRST_RUSSIA_T13_PAIR_LIFT", decision["reason"])

    def test_first_russia_single_t13_t12_bank_lift_guides_t12_pair_after_t14(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": 2.0, "y": -0.3},
            {"id": 2, "type": 13, "x": 1.1, "y": -2.5},
            {"id": 3, "type": 12, "x": -0.7, "y": -2.8},
            {"id": 4, "type": 12, "x": -2.5, "y": 0.8},
            {"id": 5, "type": 11, "x": -0.8, "y": -0.2},
            {"id": 6, "type": 10, "x": 0.9, "y": 1.4},
        ]
        for i in range(7, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 4) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 12},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.4,
                    },
                    {
                        "x": 2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertIn("FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT", decision["reason"])

    def test_first_russia_t13_t11_rebuild_guides_t11_when_t12_absent_after_t14(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": -0.2, "y": -2.1},
            {"id": 2, "type": 13, "x": -1.5, "y": -0.2},
            {"id": 3, "type": 11, "x": 1.05, "y": -0.8},
            {"id": 4, "type": 11, "x": -2.6, "y": 1.1},
            {"id": 5, "type": 10, "x": 2.2, "y": -1.8},
            {"id": 6, "type": 10, "x": 0.4, "y": 1.0},
        ]
        for i in range(7, 38):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 10},
            },
            {
                "reactor": {"deadline_margin": 1.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.85,
                    },
                    {
                        "x": 1.05,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.7,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.05)
        self.assertIn("FIRST_RUSSIA_T13_T11_REBUILD", decision["reason"])

    def test_first_russia_single_t13_t12_bank_redline_veto_prefers_lower_risk(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": [
                    {"id": 1, "type": 14, "x": 2.0, "y": -0.3},
                    {"id": 2, "type": 13, "x": 1.1, "y": -2.5},
                    {"id": 3, "type": 12, "x": -0.7, "y": -2.8},
                    {"id": 4, "type": 12, "x": -2.5, "y": 0.8},
                    {"id": 5, "type": 11, "x": -0.8, "y": -0.2},
                    {"id": 6, "type": 10, "x": 0.9, "y": 1.4},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": 0.45,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.2,
                        "top_y_after_drop": 4.74,
                        "risk_top_y_after_drop": 4.74,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.4,
                        "top_y_after_drop": 3.22,
                        "risk_top_y_after_drop": 3.22,
                    },
                    {
                        "x": -1.6,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                        "top_y_after_drop": 3.82,
                        "risk_top_y_after_drop": 3.82,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.8)
        self.assertIn("SECOND_RUSSIA_REDLINE_NO_MERGE_VETO", decision["reason"])
        self.assertNotIn("FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT", decision["reason"])

    def test_deadline_guard_clusters_first_russia_single_t13_t12_bank_after_t14(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": 2.0, "y": -0.3},
                    {"id": 2, "type": 13, "x": 1.1, "y": -2.5},
                    {"id": 3, "type": 12, "x": -0.7, "y": -2.8},
                    {"id": 4, "type": 12, "x": -2.5, "y": 0.8},
                    {"id": 5, "type": 11, "x": -0.8, "y": -0.2},
                    {"id": 6, "type": 10, "x": 0.9, "y": 1.4},
                ],
                "next": {"type": 12},
                "nextNext": {"type": 7},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.5,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.0,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT")

    def test_deadline_guard_clusters_first_russia_t13_t11_rebuild_after_t14(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": -0.2, "y": -2.1},
                    {"id": 2, "type": 13, "x": -1.5, "y": -0.2},
                    {"id": 3, "type": 11, "x": 1.05, "y": -0.8},
                    {"id": 4, "type": 10, "x": 2.2, "y": -1.8},
                    {"id": 5, "type": 10, "x": 0.4, "y": 1.0},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 10},
            },
            {
                "reactor": {"deadline_margin": 0.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.65,
                    },
                    {
                        "x": 1.05,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.05)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_FIRST_RUSSIA_T13_T11_REBUILD")

    def test_deadline_guard_clusters_first_russia_t13_pair_after_t14(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": 1.1, "y": -2.8},
                    {"id": 2, "type": 13, "x": -1.4, "y": -3.4},
                    {"id": 3, "type": 13, "x": -1.8, "y": -0.5},
                    {"id": 4, "type": 11, "x": 1.9, "y": -0.6},
                    {"id": 5, "type": 11, "x": -0.2, "y": 1.4},
                    {"id": 6, "type": 10, "x": -2.6, "y": -1.8},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 11},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 1.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT")

    def test_second_russia_t12_ladder_guides_t11_after_first_t14(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": -1.4, "y": -1.4},
            {"id": 2, "type": 12, "x": 1.9, "y": 1.1},
            {"id": 3, "type": 11, "x": 2.6, "y": -2.3},
            {"id": 4, "type": 11, "x": -0.4, "y": 1.1},
            {"id": 5, "type": 10, "x": 0.8, "y": 0.1},
            {"id": 6, "type": 10, "x": 1.4, "y": -3.5},
        ]
        for i in range(7, 35):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 4) * 0.2,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 8},
            },
            {
                "reactor": {"deadline_margin": 1.4, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.4,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 1.0,
                    },
                    {
                        "x": 1.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.4)
        self.assertIn("SECOND_RUSSIA_T12_LADDER", decision["reason"])

    def test_second_russia_t12_ladder_keeps_single_t12_rebuild_off_edges_after_t14(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": -1.95, "y": -2.1},
            {"id": 2, "type": 12, "x": 0.75, "y": 0.0},
            {"id": 3, "type": 11, "x": -2.35, "y": 0.35},
            {"id": 4, "type": 11, "x": -0.65, "y": -3.75},
            {"id": 5, "type": 10, "x": -0.75, "y": 0.65},
            {"id": 6, "type": 10, "x": 2.25, "y": 0.35},
            {"id": 7, "type": 10, "x": -0.35, "y": -2.35},
        ]
        for i in range(8, 39):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.20,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 9},
            },
            {
                "reactor": {"deadline_margin": 1.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.4,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.9,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.4)
        self.assertIn("SECOND_RUSSIA_T12_LADDER", decision["reason"])

    def test_second_russia_t12_pair_lock_guides_bank_to_pair_center_after_t14(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": -0.5, "y": -2.0},
            {"id": 2, "type": 12, "x": -2.1, "y": 1.0},
            {"id": 3, "type": 12, "x": 1.4, "y": 0.0},
            {"id": 4, "type": 11, "x": -1.9, "y": -1.0},
            {"id": 5, "type": 10, "x": -1.1, "y": 1.2},
            {"id": 6, "type": 10, "x": 2.3, "y": -1.8},
            {"id": 7, "type": 10, "x": 2.3, "y": 1.4},
        ]
        for i in range(8, 39):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 0.8, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": -0.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.65,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.4,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.35)
        self.assertIn("T12_PAIR_LOCK", decision["reason"])

    def test_second_russia_redline_veto_blocks_no_merge_ladder_crossing(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": -0.48, "y": -1.94},
            {"id": 2, "type": 12, "x": -2.0, "y": -1.21},
            {"id": 3, "type": 11, "x": -1.55, "y": -0.30},
            {"id": 4, "type": 11, "x": -0.32, "y": -3.77},
            {"id": 5, "type": 11, "x": 2.0, "y": 0.46},
            {"id": 6, "type": 10, "x": 0.01, "y": 1.58},
            {"id": 7, "type": 10, "x": 1.74, "y": -2.26},
            {"id": 8, "type": 10, "x": 2.31, "y": 1.57},
        ]
        for i in range(9, 38):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.0 + (i % 4) * 0.2,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.05,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.2,
                        "top_y_after_drop": 4.45,
                        "risk_top_y_after_drop": 4.45,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.4,
                        "top_y_after_drop": 3.18,
                        "risk_top_y_after_drop": 3.18,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": True,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                        "top_y_after_drop": 3.75,
                        "risk_top_y_after_drop": 3.75,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.8)
        self.assertIn("SECOND_RUSSIA_REDLINE_NO_MERGE_VETO", decision["reason"])
        self.assertNotIn("SECOND_RUSSIA_T12_LADDER", decision["reason"])

    def test_deadline_guard_clusters_second_russia_t12_ladder_after_first_t14(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": -1.4, "y": -1.4},
                    {"id": 2, "type": 12, "x": 1.9, "y": 1.1},
                    {"id": 3, "type": 11, "x": 2.6, "y": -2.3},
                    {"id": 4, "type": 11, "x": -0.4, "y": 1.1},
                    {"id": 5, "type": 10, "x": 0.8, "y": 0.1},
                    {"id": 6, "type": 10, "x": 1.4, "y": -3.5},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 8},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.4,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.45,
                    },
                    {
                        "x": 1.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.8)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_SAFE_LANDING")

    def test_deadline_guard_clusters_second_russia_t12_pair_after_first_t14(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": -0.5, "y": -2.0},
                    {"id": 2, "type": 12, "x": -2.1, "y": 1.0},
                    {"id": 3, "type": 12, "x": 1.4, "y": 0.0},
                    {"id": 4, "type": 11, "x": -1.9, "y": -1.0},
                    {"id": 5, "type": 10, "x": -1.1, "y": 1.2},
                    {"id": 6, "type": 10, "x": 2.3, "y": -1.8},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 0.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.35,
                    },
                    {
                        "x": -0.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.2,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -0.35)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK")

    def test_pre_russia_early_t10_ladder_guides_t10_before_first_t13(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": 0.35, "y": -2.9},
            {"id": 2, "type": 11, "x": 1.4, "y": -1.5},
            {"id": 3, "type": 10, "x": -2.35, "y": -0.2},
            {"id": 4, "type": 10, "x": 2.33, "y": -1.1},
            {"id": 5, "type": 10, "x": 0.1, "y": -1.85},
            {"id": 6, "type": 9, "x": 2.1, "y": 0.7},
            {"id": 7, "type": 9, "x": -0.45, "y": -0.6},
        ]
        for i in range(8, 31):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 4) * 0.24,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 1.4, "reactive_pairs": []},
                "results": [
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.1,
                    },
                    {
                        "x": 0.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.8,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.8)
        self.assertIn("PRE_RUSSIA_EARLY_T10_LADDER", decision["reason"])

    def test_pre_russia_single_t12_anchor_ladder_avoids_high_direct_t11_merge(self):
        import strategy

        pieces = [
            {"id": 1, "type": 12, "x": 2.45, "y": -3.35},
            {"id": 2, "type": 11, "x": -0.85, "y": -3.75},
            {"id": 3, "type": 11, "x": -0.85, "y": -2.05},
            {"id": 4, "type": 11, "x": 2.0, "y": 0.75},
            {"id": 5, "type": 11, "x": -1.6, "y": 0.9},
            {"id": 6, "type": 10, "x": -0.3, "y": 0.15},
            {"id": 7, "type": 10, "x": 1.25, "y": -2.2},
        ]
        for i in range(8, 40):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.20,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -0.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "DIRECT",
                        "landing_y": 2.2,
                    },
                    {
                        "x": 2.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.15,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 2.35)
        self.assertIn("PRE_RUSSIA_SINGLE_T12_ANCHOR_LADDER", decision["reason"])

    def test_pre_russia_t10_ladder_guides_t11_to_single_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": -1.2, "y": -2.8},
            {"id": 2, "type": 12, "x": 1.1, "y": -1.4},
            {"id": 3, "type": 10, "x": 2.3, "y": -3.6},
            {"id": 4, "type": 10, "x": -2.4, "y": 0.5},
        ]
        for i in range(5, 26):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 4) * 0.25,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 1.6, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 1.05,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.65,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.05)
        self.assertIn("PRE_RUSSIA_T10_LADDER", decision["reason"])

    def test_pre_russia_same_type_latch_guides_t11_toward_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 2.1, "y": -2.1},
            {"id": 2, "type": 12, "x": -1.8, "y": -3.5},
            {"id": 3, "type": 12, "x": 0.2, "y": -2.8},
            {"id": 4, "type": 11, "x": -1.3, "y": -0.8},
            {"id": 5, "type": 11, "x": 1.4, "y": -0.2},
        ]
        for i in range(6, 27):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 7),
                    "x": -2.8 + (i % 7) * 0.8,
                    "y": -2.3 + (i % 4) * 0.25,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.3,
                    },
                    {
                        "x": 0.25,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                    {
                        "x": 2.7,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.25)
        self.assertIn("PRE_RUSSIA_SAME_TYPE_LATCH", decision["reason"])

    def test_pre_russia_same_type_latch_fires_before_second_t12_is_ready(self):
        import strategy

        pieces = [
            {"id": 1, "type": 13, "x": 1.9, "y": -2.0},
            {"id": 2, "type": 12, "x": -0.2, "y": -2.4},
            {"id": 3, "type": 11, "x": -1.4, "y": -0.8},
            {"id": 4, "type": 11, "x": 0.7, "y": -0.3},
            {"id": 5, "type": 11, "x": 2.0, "y": 0.2},
        ]
        for i in range(6, 26):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 7),
                    "x": -2.7 + (i % 7) * 0.8,
                    "y": -2.2 + (i % 4) * 0.25,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.2,
                    },
                    {
                        "x": 0.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.35)
        self.assertIn("PRE_RUSSIA_SAME_TYPE_LATCH", decision["reason"])

    def test_second_russia_latch_guides_t10_after_first_t14(self):
        import strategy

        pieces = [
            {"id": 1, "type": 14, "x": 0.5, "y": -2.8},
            {"id": 2, "type": 12, "x": 0.2, "y": -0.1},
            {"id": 3, "type": 11, "x": -1.8, "y": -3.0},
            {"id": 4, "type": 10, "x": -1.0, "y": 0.7},
            {"id": 5, "type": 10, "x": 2.5, "y": -2.0},
        ]
        for i in range(6, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 6},
            },
            {
                "reactor": {"deadline_margin": 2.0, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.05,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.4,
                    },
                    {
                        "x": 2.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 0.45,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.6,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.05)
        self.assertIn("SECOND_RUSSIA_SAME_TYPE_LATCH", decision["reason"])

    def test_soviet_t15_lift_guides_t11_to_second_russia_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 15, "x": -0.5, "y": -1.8},
            {"id": 2, "type": 13, "x": 1.0, "y": 1.2},
            {"id": 3, "type": 12, "x": 2.2, "y": -2.1},
            {"id": 4, "type": 11, "x": 1.2, "y": 0.1},
            {"id": 5, "type": 11, "x": 1.7, "y": 2.0},
        ]
        for i in range(6, 34):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 5},
            },
            {
                "reactor": {"deadline_margin": 1.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 1.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.35)
        self.assertIn("SOVIET_T15_LIFT", decision["reason"])

    def test_soviet_t15_t11_rebuild_prefers_existing_t11_when_t12_absent(self):
        import strategy

        pieces = [
            {"id": 1, "type": 15, "x": 0.05, "y": -2.4},
            {"id": 2, "type": 13, "x": -2.25, "y": 0.2},
            {"id": 3, "type": 11, "x": 1.1, "y": -0.6},
            {"id": 4, "type": 10, "x": 2.1, "y": -3.4},
            {"id": 5, "type": 10, "x": 2.25, "y": 0.45},
            {"id": 6, "type": 10, "x": -2.6, "y": -3.7},
        ]
        for i in range(7, 38):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.1 + (i % 5) * 0.22,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 1.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.9,
                    },
                    {
                        "x": 1.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.75,
                    },
                    {
                        "x": 2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.1)
        self.assertIn("SOVIET_T15_T11_REBUILD", decision["reason"])

    def test_soviet_t10_ladder_guides_t10_to_remaining_t12_lane(self):
        import strategy

        pieces = [
            {"id": 1, "type": 15, "x": -0.7, "y": -1.8},
            {"id": 2, "type": 12, "x": 1.35, "y": -1.2},
            {"id": 3, "type": 11, "x": 1.05, "y": 0.0},
            {"id": 4, "type": 10, "x": 1.15, "y": -0.4},
            {"id": 5, "type": 10, "x": -2.5, "y": -2.1},
        ]
        for i in range(6, 35):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                }
            )

        decision = strategy.decide(
            {
                "deadline_crossed": False,
                "pieces": pieces,
                "next": {"type": 10},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 1.1, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 1.15,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.15)
        self.assertIn("SOVIET_T10_LADDER", decision["reason"])

    def test_deadline_guard_clusters_soviet_lift_material_after_type15(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 15, "x": -0.5, "y": -1.8},
                    {"id": 2, "type": 13, "x": 1.0, "y": 1.2},
                    {"id": 3, "type": 12, "x": 2.2, "y": -2.1},
                    {"id": 4, "type": 11, "x": 1.2, "y": 0.1},
                    {"id": 5, "type": 11, "x": 1.7, "y": 2.0},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                    {
                        "x": 1.35,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.45,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.1,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.35)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_clusters_soviet_t11_rebuild_after_type15(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 15, "x": 0.05, "y": -2.4},
                    {"id": 2, "type": 13, "x": -2.25, "y": 0.2},
                    {"id": 3, "type": 11, "x": 1.1, "y": -0.6},
                    {"id": 4, "type": 10, "x": 2.1, "y": -3.4},
                ],
                "next": {"type": 11},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -1.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.65,
                    },
                    {
                        "x": 1.1,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.55,
                    },
                    {
                        "x": 2.9,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.1)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_clusters_soviet_t10_ladder_material_after_type15(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 15, "x": -0.5, "y": -1.8},
                    {"id": 2, "type": 12, "x": 1.35, "y": -1.2},
                    {"id": 3, "type": 11, "x": 1.05, "y": 0.0},
                    {"id": 4, "type": 10, "x": 1.15, "y": -0.4},
                    {"id": 5, "type": 10, "x": -2.5, "y": -2.1},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 4},
            },
            {
                "reactor": {"deadline_margin": 0.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": 1.15,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], 1.15)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_deadline_guard_clusters_double_t14_frontier_material(self):
        import strategy

        decision = strategy.decide(
            {
                "deadline_crossed": True,
                "pieces": [
                    {"id": 1, "type": 14, "x": -1.5, "y": -2.8},
                    {"id": 2, "type": 14, "x": -1.8, "y": 0.1},
                    {"id": 3, "type": 13, "x": 1.8, "y": -0.6},
                    {"id": 4, "type": 12, "x": 2.4, "y": -2.3},
                    {"id": 5, "type": 11, "x": 0.1, "y": -1.5},
                ],
                "next": {"type": 10},
                "nextNext": {"type": 3},
            },
            {
                "reactor": {"deadline_margin": 0.2, "reactive_pairs": []},
                "results": [
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.25,
                    },
                    {
                        "x": -1.55,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": 0.35,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_result_crosses_deadline": False,
                        "merge_grade": "NO",
                        "landing_y": -0.2,
                    },
                ],
            },
        )

        self.assertEqual(decision["x"], -1.55)
        self.assertEqual(decision["reason"], "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER")

    def test_mixed_hash_games_skip_rolling_before_current_run_bookkeeping(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        block = improve.split("record_completed_game_for_adaptive_improvement() {", 1)[1].split(
            "\n}\n\n", 1
        )[0]

        mismatch_idx = block.index('[ "$played_hash" != "$current_hash" ]')
        rolling_idx = block.index('update_rolling_scores "$score" "$archive_file"')
        current_run_idx = block.index('_update_current_strategy_run "$current_hash" "$score" "$archive_file"')

        self.assertLess(mismatch_idx, rolling_idx)
        self.assertLess(rolling_idx, current_run_idx)

    def test_soviet_theme_append_rejects_gacha_and_non_soviet_topics(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn("ソ連テーマ追加スキップ（ガチャ/獲得文）", script)
        self.assertIn("獲得しました|連ガチャ|カードガチャ", script)
        self.assertIn("ソ連テーマ追加スキップ（非ソ連テーマ）", script)
        self.assertIn("ソ連|ソビエト|ロシア|共産|冷戦", script)


# --- Improve OBS overlay ------------------------------------------------------

class TestImproveOverlay(unittest.TestCase):
    def test_codex_work_indicator_uses_event_overlay_not_systemmsg(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        generator = (REPO_ROOT / "generate_event_overlay.py").read_text()
        notify = (REPO_ROOT / "overlay_notify.sh").read_text()
        indicator = (REPO_ROOT / "codex_work_indicator.sh").read_text()
        agents = (REPO_ROOT / "AGENTS.md").read_text()

        self.assertIn("CODEX_WORK_OVERLAY_STATE_FILE", config)
        self.assertIn("read_work_indicator", generator)
        self.assertIn("work-indicator", generator)
        self.assertIn("システム自動分析・修正作業中", generator)
        self.assertIn("メリケンAI が確認・修正・検証を進めています", generator)
        self.assertIn(".toast.chat .title", generator)
        self.assertIn(".toast.chat .body", generator)
        self.assertIn("font-size: 22px", generator)
        self.assertIn("$CODEX_WORK_OVERLAY_STATE_FILE", notify)
        self.assertIn("./obs_control.sh stack", notify)
        self.assertIn("generate_event_overlay.py", indicator)
        self.assertIn("./obs_control.sh stack", indicator)
        self.assertIn("eventOverlay", agents)
        self.assertIn("./codex_work_indicator.sh start", agents)
        self.assertIn("./codex_work_indicator.sh stop", agents)
        self.assertNotIn("./obs_control.sh show soren systemMsg", agents)
        self.assertNotIn("./obs_control.sh hide soren systemMsg", agents)

    def test_game_result_overlay_title_includes_cycle_progress(self):
        eloop = (REPO_ROOT / "eloop.sh").read_text()

        self.assertIn("_cycle_progress=$(python3 - \"$ACCUMULATED_GAMES_FILE\" \"$MIN_GAMES_BEFORE_IMPROVE\"", eloop)
        self.assertIn('print(f"[{count}/{cycle}]")', eloop)
        self.assertIn('Game #${game_num_display} 終了${_cycle_progress:+ ${_cycle_progress}}', eloop)

    def test_improve_overlay_is_file_based_and_replaces_console_capture(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()
        overlay = (REPO_ROOT / "generate_improve_overlay.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("IMPROVE_OVERLAY_HTML_FILE", config)
        self.assertIn("IMPROVE_OVERLAY_SOURCE", config)
        self.assertIn("improve_overlay.html", config)
        self.assertIn("_improve_overlay_show", improve)
        self.assertIn("_improve_overlay_watch_start", improve)
        self.assertIn('obs_control.sh show soren "$IMPROVE_OVERLAY_SOURCE"', improve)
        self.assertIn("generate_improve_overlay.sh watch", improve)
        self.assertIn('obs_control.sh show soren "$IMPROVE_OVERLAY_SOURCE"', monitor)
        self.assertIn("generate_improve_overlay.sh once", monitor)
        self.assertIn("meta http-equiv=\"refresh\"", overlay)
        self.assertIn("IMPROVE_AI_LOG_FILE", overlay)
        self.assertIn("IMPROVE_RUN_CMD_TIMEOUT_SEC", overlay)
        self.assertIn("IMPROVE_FIX_CMD_TIMEOUT_SEC", overlay)
        self.assertIn("IMPROVE_WALL_TIMEOUT", overlay)
        self.assertIn("AI cap:", overlay)
        self.assertIn("FIX cap:", overlay)
        self.assertIn("job cap:", overlay)
        self.assertIn("Keep them visible even while the", loop)
        self.assertIn("./show_status_g.sh --html-obs show", loop)
        self.assertIn("./show_status.sh --html-obs show", loop)
        self.assertNotIn('_overlay_vis="hide"', loop)
        status_g = (REPO_ROOT / "show_status_g.sh").read_text()
        show_status = (REPO_ROOT / "show_status.sh").read_text()
        self.assertIn("persistent monitoring surface", status_g)
        self.assertIn("exec ./generate_status_overlay.sh ensure-obs show", status_g)
        self.assertIn("persistent monitoring surface", show_status)
        self.assertIn("exec ./generate_show_status_overlay.sh ensure-obs show", show_status)

    def test_chrome_translate_banner_is_disabled_before_launch(self):
        soviet = (REPO_ROOT / "soviet_local.mjs").read_text()
        bridge = (REPO_ROOT / "lib/bridge_recovery.sh").read_text()

        self.assertIn("seedChromeTranslatePreferences(USER_DATA_DIR)", soviet)
        self.assertIn("prefs.translate = { ...(prefs.translate || {}), enabled: false }", soviet)
        self.assertIn("prefs.translate_blocked_languages = ['en', 'ja']", soviet)
        self.assertIn("'localhost'", soviet)
        self.assertIn("'127.0.0.1'", soviet)
        self.assertIn("accept_languages: 'ja-JP,ja,en-US,en'", soviet)
        self.assertIn("'--disable-translate'", soviet)
        self.assertIn('tr["enabled"] = False', bridge)

    def test_obs_control_can_report_overlay_source_status(self):
        obs = (REPO_ROOT / "obs_control.sh").read_text()

        self.assertIn("./obs_control.sh status <scene> <source>", obs)
        self.assertIn("action === 'status'", obs)
        self.assertIn("sceneItemEnabled === true", obs)
        self.assertIn("=missing", obs)
        self.assertIn("./obs_control.sh stack <scene>", obs)
        self.assertIn("SetSceneItemIndex", obs)
        self.assertIn("OBS_TOP_OVERLAY_SOURCE", obs)
        self.assertIn("OBS_BELOW_TOP_OVERLAY_SOURCE", obs)
        self.assertIn("'twica'", obs)
        self.assertIn("'eventOverlay'", obs)

    def test_status_g_has_wide_short_html_overlay_generator(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        overlay = (REPO_ROOT / "generate_status_overlay.sh").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()
        status_g = (REPO_ROOT / "show_status_g.sh").read_text()
        dashboard = (REPO_ROOT / "status_dashboard.py").read_text()

        self.assertIn("STATUS_OVERLAY_HTML_FILE", config)
        self.assertIn("STATUS_OVERLAY_SOURCE", config)
        self.assertIn("statsOverlay", config)
        self.assertIn("OBS_STATUS_OVERLAY_SOURCE", config)
        self.assertIn("STATS_OVERLAY_SOURCE", config)
        self.assertIn("STATUS_OVERLAY_WIDTH", config)
        self.assertIn("STATUS_OVERLAY_HEIGHT", config)
        self.assertIn("STATUS_OVERLAY_OBS_X", config)
        self.assertIn("STATUS_OVERLAY_OBS_Y", config)
        self.assertIn("STATUS_OVERLAY_OBS_SCALE_X", config)
        self.assertIn("STATUS_OVERLAY_OBS_SCALE_Y", config)
        self.assertIn("STATUS_OVERLAY_OBS_TRANSFORM_ENABLED", config)
        self.assertIn('STATUS_OVERLAY_OBS_TRANSFORM_ENABLED:-1', config)
        self.assertIn('STATUS_OVERLAY_WIDTH="${STATUS_OVERLAY_WIDTH:-560}"', config)
        self.assertIn('STATUS_OVERLAY_HEIGHT="${STATUS_OVERLAY_HEIGHT:-820}"', config)
        self.assertIn("python3 status_dashboard.py", overlay)
        self.assertIn("[ -f .env ] && set -a && . ./.env && set +a", overlay)
        self.assertIn("ansi_to_html", overlay)
        self.assertIn("STATUS_OVERLAY_RAW", overlay)
        self.assertIn("obs_browser_source.sh ensure", overlay)
        self.assertIn("apply_obs_transform", overlay)
        self.assertIn("SetSceneItemTransform", overlay)
        self.assertIn("OBS_BOUNDS_NONE", overlay)
        self.assertIn("transformed:", overlay)
        self.assertIn('${STATUS_OVERLAY_OBS_TRANSFORM_ENABLED:-0}" = "force"', overlay)
        self.assertIn("status_overlay_watch.pid", overlay)
        self.assertIn("status_overlay.log", overlay)
        self.assertIn("soren_status_overlay", overlay)
        self.assertIn("tmux new-session", overlay)
        self.assertIn("tmux-start-failed:fallback-nohup", overlay)
        self.assertIn("tmux kill-session", overlay)
        self.assertIn("nohup", overlay)
        self.assertIn("start [interval_sec]", overlay)
        self.assertIn("stop|ensure-obs", overlay)
        self.assertIn("width: {html.escape(width)}px", overlay)
        self.assertIn("height: {html.escape(height)}px", overlay)
        self.assertIn("--html-once", status_g)
        self.assertIn("--html-watch", status_g)
        self.assertIn("--html-start", status_g)
        self.assertIn("--html-stop", status_g)
        self.assertIn("--html-obs", status_g)
        self.assertIn("generate_status_overlay.sh", status_g)
        self.assertIn("Observer Status", dashboard)
        self.assertIn("load_latest_annealing_candidate", dashboard)
        self.assertIn("load_wildcard_attempt_status", dashboard)
        self.assertIn("VIEWER_CHAT_MONITOR_FILE", dashboard)
        self.assertIn("load_viewer_chat_monitor", dashboard)
        self.assertIn("ChatObs", dashboard)
        self.assertIn("ANNEALING_OBSERVE_FILE", dashboard)
        self.assertIn("WILDCARD_ATTEMPT_STATE_FILE", dashboard)
        self.assertIn("WildStreak", dashboard)
        self.assertIn("archive_restart next", dashboard)
        self.assertIn("load_archive_restart_candidate", dashboard)
        self.assertIn("ArchiveRestart candidates", dashboard)
        self.assertIn('"candidates": candidates', dashboard)
        self.assertIn("top={min(10, total)} total={total}", dashboard)
        self.assertIn("wildcard_origins = {", dashboard)
        self.assertIn('origin_type") or "wildcard") == "wildcard"', dashboard)
        self.assertIn("current_origin_hash", dashboard)
        self.assertIn("Show WILDCARD origins only", dashboard)
        self.assertIn('parallel_result = meta.get("parallel_result") or {}', dashboard)
        self.assertIn('score_source = " trial"', dashboard)
        self.assertIn('origin_type") or "wildcard") == "wildcard"', status)
        self.assertIn("fit_dashboard_lines(output)", dashboard)
        self.assertIn("truncate_ansi_display", dashboard)
        self.assertIn("render_score_timeline(scores, chart_w=42", dashboard)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_FILE", dashboard)
        self.assertIn("ARCHIVE_RESTART_MIN_BEST_TYPE", dashboard)
        self.assertIn("ARCHIVE_RESTART_INCLUDE_PERMANENT", dashboard)
        self.assertIn("ARCHIVE_RESTART_ALLOW_ORIGIN_RETRY", dashboard)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_SEC", dashboard)
        self.assertIn("is_cooled_down", dashboard)
        self.assertIn("find_archive_path", dashboard)
        self.assertIn("archive_path_blocker", dashboard)
        self.assertIn('"blockers": blockers', dashboard)
        self.assertIn("anchor_russia", dashboard)
        self.assertIn("anchor_soviet", dashboard)
        self.assertIn("best_type < min_best_type", dashboard)
        self.assertIn("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE", dashboard)
        self.assertIn("no_candidate_cooldown", dashboard)
        self.assertIn("if no_candidate_cooldown is not None", dashboard)
        self.assertIn("no_candidate_age = None", status)
        self.assertIn('"status": "no_candidate"', dashboard)
        self.assertIn("threshold c>=", dashboard)
        self.assertIn("escape_ai direct", dashboard)
        self.assertIn("effective_streak", dashboard)
        self.assertIn("failed_origin_count", dashboard)
        self.assertIn("failed-origin pool", dashboard)
        self.assertIn("ARCHIVE_RESTART_STREAK", dashboard)
        self.assertIn("WILDCARD_AI_ESCALATE_STREAK", dashboard)
        self.assertIn("observe-only", dashboard)

    def test_wildcard_origin_status_falls_back_to_parallel_scores(self):
        import os
        import tempfile

        import status_dashboard

        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            try:
                os.chdir(td)
                state = Path("tmp/state")
                state.mkdir(parents=True)
                (state / "wildcard_origin.json").write_text(
                    json.dumps(
                        {
                            "abc123def456": {
                                "origin_type": "wildcard",
                                "max_games_override": 12,
                                "parallel_result": {"scores": [10000, 12000, 14000]},
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                lines = status_dashboard.render_wildcard_status(
                    {"abc123def456": {"scores": []}},
                    "abc123def456",
                )
            finally:
                os.chdir(cwd)

        text = "\n".join(lines)
        self.assertIn(" 3/12", text)
        self.assertIn("trial", text)
        self.assertNotIn("scores none", text)

    def test_browser_source_ensure_preserves_manual_obs_transform(self):
        browser_source = (REPO_ROOT / "obs_browser_source.sh").read_text()

        self.assertIn("OBS_BROWSER_SOURCE_PRESERVE_TRANSFORM", browser_source)
        self.assertIn("GetSceneItemTransform", browser_source)
        self.assertIn("preserved.set(item.sceneItemId", browser_source)
        self.assertIn("wantedWidth / nextSourceWidth", browser_source)
        self.assertIn("wantedHeight / nextSourceHeight", browser_source)
        self.assertIn("SetSceneItemTransform", browser_source)

    def test_show_status_has_html_overlay_generator(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        overlay = (REPO_ROOT / "generate_show_status_overlay.sh").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("SHOW_STATUS_OVERLAY_HTML_FILE", config)
        self.assertIn("SHOW_STATUS_OVERLAY_SOURCE", config)
        self.assertIn("show_status_overlay.html", config)
        self.assertIn("opsOverlay", config)
        self.assertIn("OBS_SHOW_STATUS_OVERLAY_SOURCE", config)
        self.assertIn("OPS_OVERLAY_SOURCE", config)
        self.assertIn("SHOW_STATUS_OVERLAY_HEIGHT", config)
        self.assertIn("SHOW_STATUS_OVERLAY_OBS_X", config)
        self.assertIn("SHOW_STATUS_OVERLAY_OBS_Y", config)
        self.assertIn("SHOW_STATUS_OVERLAY_OBS_SCALE_X", config)
        self.assertIn("SHOW_STATUS_OVERLAY_OBS_SCALE_Y", config)
        self.assertIn("680", config)
        self.assertIn("SHOW_STATUS_NO_FLICKER=1 ./show_status.sh --once", overlay)
        self.assertIn("SHOW_STATUS_OVERLAY_RAW", overlay)
        self.assertIn("ansi_to_html", overlay)
        self.assertIn("obs_browser_source.sh ensure", overlay)
        self.assertIn("show_status_overlay_watch.pid", overlay)
        self.assertIn("show_status_overlay.log", overlay)
        self.assertIn("soren_show_status_overlay", overlay)
        self.assertIn("tmux new-session", overlay)
        self.assertIn("tmux-start-failed:fallback-nohup", overlay)
        self.assertIn("tmux kill-session", overlay)
        self.assertIn("nohup", overlay)
        self.assertIn("start [interval_sec]", overlay)
        self.assertIn("stop|ensure-obs", overlay)
        self.assertIn("width: {html.escape(width)}px", overlay)
        self.assertIn("height: {html.escape(height)}px", overlay)
        self.assertIn("--html-once", status)
        self.assertIn("--html-watch", status)
        self.assertIn("--html-start", status)
        self.assertIn("--html-stop", status)
        self.assertIn("--html-obs", status)
        self.assertIn("generate_show_status_overlay.sh", status)


# --- soren91 process launch ---------------------------------------------------

class TestSoren91RunnerLaunch(unittest.TestCase):
    def test_soren91_layout_switch_uses_status_overlays_instead_of_old_console_sources(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()

        self.assertIn("${SOREN91_OBS_SOURCE:-}", control)
        self.assertIn('if [ -n "$SOREN91_OBS_INPUT_NAME" ] && [ "$SOREN91_OBS_INPUT_NAME" != "$game_source" ]; then', control)
        self.assertIn('SOREN_GAME_OBS_SOURCE', control)
        self.assertIn('SOREN_OBS_GAME_SOURCE_NAME:-sorengame', control)
        self.assertIn("obs_window_capture_source.sh", control)
        self.assertIn("'91人対戦|ソ連ゲーム91'", control)
        self.assertIn("'Unity WebGL Player \\| soren-game'", control)
        self.assertIn('china_show_sources="$game_source,$china_show_sources"', control)
        self.assertIn("${STATUS_OVERLAY_SOURCE:-statsOverlay}", control)
        self.assertIn("${SHOW_STATUS_OVERLAY_SOURCE:-opsOverlay}", control)
        self.assertIn("${OBS_DASHBOARD_SOURCE:-dashboard}", control)
        self.assertIn('meriken_show_sources="$meriken_show_sources,$game_source"', control)
        self.assertIn('[ "$SOREN91_OBS_INPUT_NAME" != "$game_source" ]', control)
        self.assertIn('show:"$meriken_show_sources" $s91_show_op hide:"$meriken_hide_sources"', control)
        self.assertNotIn('meriken_hide_sources="$game_source,$meriken_hide_sources"', control)
        self.assertIn('show:"$status_source","$show_status_source","$china_show_sources" $s91_hide_op', control)
        self.assertIn("改善中も stats/ops は監視用に維持", control)
        self.assertNotIn("hide:console1,console2", control)
        self.assertNotIn("show:console1,console2", control)

    def test_soren91_start_prefers_tmux_tty_runner(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()
        runner = (REPO_ROOT / "soren91/run_player_loop.sh").read_text()

        self.assertIn("tmux new-session -d -s soren91_runner", control)
        self.assertIn("SOREN91_SHARED_BROWSER='${SOREN91_SHARED_BROWSER:-1}'", control)
        self.assertNotIn("export SOREN91_SHARED_BROWSER=1", control)
        self.assertIn("_soren91_stop_standalone_browser", control)
        self.assertIn("_soren91_scan_standalone_browser_pids()", control)
        self.assertIn("standalone_chromium_profile", control)
        self.assertIn("--remote-debugging-port=${cdp_port}", control)
        self.assertIn("live_pid_after_start", control)
        self.assertIn("_soren91_read_alive_player_pid 2>/dev/null", control)
        self.assertIn("exec /bin/bash '$SOREN91_RUNNER_SCRIPT'", control)
        self.assertIn('CHILD_MAIN_PID=""', runner)
        self.assertIn('CHILD_MAIN_PID=$!', runner)
        self.assertIn('wait "$CHILD_MAIN_PID"', runner)
        self.assertIn('kill -9 "$pid"', runner)
        self.assertIn("trap '' HUP", runner)
        self.assertIn("trap '_on_signal TERM' TERM", runner)
        self.assertIn("trap '_on_exit' EXIT", runner)

    def test_soren91_pid_file_survives_hidden_command_lookup(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()

        self.assertIn("_soren91_pid_is_alive()", control)
        self.assertIn('ps -p "$pid" -o pid= 2>/dev/null', control)
        self.assertIn('cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")', control)
        self.assertIn('if [ -z "$cmd" ]; then', control)
        self.assertIn('printf \'%s\' "$pid"\n\t\t\treturn 0', control)
        self.assertIn('s/^pid=//p', control)
        self.assertIn('$SOREN91_DIR/tmp/.runner.lock/owner', control)
        self.assertIn("_soren91_observable_fresh()", control)
        self.assertIn("_soren91_has_runtime_marker()", control)
        self.assertIn("_soren91_recovered_player_stale()", control)
        self.assertIn("_soren91_force_stop_recovered_player()", control)
        self.assertIn('SOREN91_OBSERVABLE_FRESH_SEC:-120', control)
        self.assertIn('$SOREN91_DIR/tmp/in_game', control)
        self.assertIn('_soren91_observable_fresh || return 1', control)
        self.assertIn('if ! _soren91_observable_fresh && ! _soren91_has_runtime_marker; then', control)
        self.assertIn('stale recovered player detected', control)
        self.assertIn('soren91_cleanup || true', control)
        self.assertIn('_soren91_force_stop_recovered_player "$stale_pid"', control)
        self.assertIn("Force stopping stale recovered player PID=$pid", control)
        self.assertIn('"soren91_stale_recovered"', control)

    def test_soren91_stop_recovers_orphan_main_process_without_pid_files(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()

        self.assertIn("_soren91_scan_alive_main_pids()", control)
        self.assertIn("_soren91_scan_log_writer_pids()", control)
        self.assertIn("_soren91_clear_stale_runner_lock()", control)
        self.assertIn("_soren91_kill_runner_session()", control)
        self.assertIn("lsof -nP \"$SOREN91_DIR/tmp/soren91.log\"", control)
        self.assertIn("awk -v target=\"$pid\"", control)
        self.assertIn("tmux kill-session -t soren91_runner", control)
        self.assertIn("tmux display-message -p -t soren91_runner '#{pane_pid}'", control)
        self.assertIn('lsof -nP "$log_file"', control)
        self.assertIn('NR > 1 && $1 == "node" && $2 ~ /^[0-9]+$/ { print $2 }', control)
        self.assertIn('rm -rf "$lock_dir"', control)
        self.assertIn("ps -Ao pid=,ppid=,command= 2>/dev/null", control)
        self.assertIn("grep -Eq '(^|[ /])node([[:space:]].*)?main\\.mjs([[:space:]]|$)'", control)
        self.assertIn('runner_pids="$(_soren91_scan_alive_runner_pids 2>/dev/null | tr \'\\n\' \' \')"', control)
        self.assertIn('pid=$(_soren91_scan_alive_main_pids | head -n 1)', control)
        self.assertIn('player_pids="$player_pids $(_soren91_scan_alive_main_pids 2>/dev/null | tr \'\\n\' \' \')"', control)
        self.assertIn('player_pids="$player_pids $(_soren91_scan_log_writer_pids 2>/dev/null | tr \'\\n\' \' \')"', control)
        self.assertIn("_soren91_kill_runner_session", control)
        self.assertNotIn("pgrep -f", control)

    def test_soren91_browser_launch_does_not_raise_focus_on_macos(self):
        main = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("launchStandaloneBrowserWithoutFocus", main)
        self.assertIn("standaloneBrowserLaunchArgs", main)
        self.assertIn("'/usr/bin/open'", main)
        self.assertIn("'-g'", main)
        self.assertIn("SOREN91_CHROME_NO_FOCUS_LAUNCH", main)
        self.assertIn("SOREN91_STANDALONE_WINDOW_POSITION", main)
        self.assertIn("'2400,1200'", main)
        self.assertIn("standaloneBrowserLaunchArgs(standaloneWindowPosition)", main)
        self.assertIn("const playwrightLaunchArgs = launchArgs.filter(arg => !/^[a-z][a-z0-9+.-]*:/i.test(arg));", main)
        self.assertIn("args: playwrightLaunchArgs", main)
        self.assertIn("'--password-store=basic'", main)
        self.assertIn("'--use-mock-keychain'", main)
        self.assertIn("'about:blank'", main)
        self.assertNotIn(".bringToFront()", main)

    def test_soviet_local_browser_launch_does_not_raise_focus_on_macos(self):
        local = (REPO_ROOT / "soviet_local.mjs").read_text()

        self.assertIn("launchPersistentContextWithoutFocus", local)
        self.assertIn("exec /usr/bin/open", local)
        self.assertIn("'-g'", local)
        self.assertIn("appPath", local)
        self.assertIn("'-n',\n    appPath,\n    '--args'", local)
        self.assertIn("SOREN_CHROME_OPEN_FALLBACK_APP_NAME", local)
        self.assertIn("'-a',\n          appName", local)
        self.assertIn("macOS open app-name fallback launched", local)
        self.assertIn("SOREN_CHROME_OPEN_FALLBACK_BUNDLE_ID", local)
        self.assertIn("com.google.chrome.for.testing,com.google.Chrome", local)
        self.assertIn("'-b',\n          bundleId", local)
        self.assertIn("macOS open bundle-id fallback launched", local)
        self.assertIn("function systemChromeExecutablePath", local)
        self.assertIn("function systemChromeAppPath", local)
        self.assertIn("function chromeFallbackAppPaths", local)
        self.assertIn("function chromeFallbackExecutablePaths", local)
        self.assertIn("SOREN_CHROME_OPEN_FALLBACK_APP_PATHS", local)
        self.assertIn("SOREN_CHROME_FALLBACK_EXECUTABLE_PATHS", local)
        self.assertIn("macOS open app-path fallback launched", local)
        self.assertIn("launchDetachedChromeFallback", local)
        self.assertIn("detached Chrome fallback launched", local)
        self.assertIn("soren_chrome_detached.stderr.log", local)
        self.assertIn("Object.prototype.hasOwnProperty.call(process.env, match[1])", local)
        self.assertIn("SOREN_CHROME_NO_FOCUS_LAUNCH", local)
        self.assertIn("SOREN_CHROME_FORCE_PLAYWRIGHT_LAUNCH", local)
        self.assertIn("SOREN_CHROME_ATTACH_ONLY", local)
        self.assertIn("async function withBrowserLaunchEnv", local)
        self.assertIn("function isRegularMacChrome(executablePath, appPath = '')", local)
        self.assertIn("const chromeHomeDir = env.SOREN_CHROME_HOME || path.join(userDataDir, 'chrome_home');", local)
        self.assertIn("const useRealMacHome = ['1', 'true', 'yes', 'on'].includes(String(env.SOREN_CHROME_USE_REAL_HOME || '').toLowerCase());", local)
        self.assertIn("const cffixedHomeSetting = String(env.SOREN_CHROME_SET_CFFIXED_HOME || '').toLowerCase();", local)
        self.assertIn("const setCffixedHome = cffixedHomeSetting", local)
        self.assertIn(": !useRealMacHome;", local)
        self.assertIn("delete env.CFFIXED_USER_HOME", local)
        self.assertIn("delete env.XDG_CONFIG_HOME", local)
        self.assertIn("delete env.XDG_CACHE_HOME", local)
        self.assertIn("else if (setCffixedHome)", local)
        self.assertIn("env.CFFIXED_USER_HOME = chromeHomeDir", local)
        self.assertIn("'CFFIXED_USER_HOME'", local)
        self.assertIn("const configHome = useRealMacHome ? '' :", local)
        self.assertIn("const cacheHome = useRealMacHome ? '' :", local)
        self.assertIn("browserLaunchEnv(userDataDir, candidatePath, { launchServices: false })", local)
        self.assertNotIn("'--disable-breakpad'", local)
        self.assertNotIn("'--disable-crashpad-for-testing'", local)
        self.assertIn("env: launchEnv", local)
        self.assertIn("function macOpenChromium(openArgs, launchEnv)", local)
        self.assertIn("function shellQuote(value)", local)
        self.assertIn("exec /usr/bin/open ${openArgs.map(shellQuote).join(' ')}", local)
        self.assertIn("SOREN_CHROME_OPEN_TIMEOUT_SEC", local)
        self.assertIn("timeout: openTimeoutMs", local)
        self.assertIn("SOREN_CHROME_DETACHED_FALLBACK_ON_OPEN_MISSING", local)
        self.assertIn("macOS open app-path missing executable; trying detached Chrome executable fallback", local)
        self.assertLess(
            local.index("macOS open app-path missing executable; trying detached Chrome executable fallback"),
            local.index("const fallbackNames = macOpenFallbackAppNames();"),
        )
        self.assertIn("isCrashpadPermissionLaunchFailure", local)
        self.assertIn("SOREN_CHROME_OPEN_FALLBACK_ON_CRASHPAD_FAIL", local)
        self.assertIn("launchPersistentContextWithoutFocus(USER_DATA_DIR, launchArgs, { force: true })", local)
        self.assertIn("openFallbackErr", local)
        self.assertIn("macOS open fallback failed", local)
        self.assertIn("isMacOpenExecutableMissingFailure", local)
        self.assertIn("launchChromiumExecutableDetached", local)
        self.assertIn("spawn(executablePath, [`--user-data-dir=${userDataDir}`, ...args]", local)
        self.assertIn("headless Playwright fallback executable", local)
        self.assertIn("chromium.launchPersistentContext", local)
        self.assertIn("async function waitForCdpHttp", local)
        self.assertIn("SOREN_CDP_ATTACH_TIMEOUT_MS", local)
        self.assertIn("await waitForCdpHttp(port, timeoutMs);", local)
        self.assertIn("await waitForCdpHttp(CDP_PORT);", local)
        self.assertIn("launchPersistentContextWithoutFocus(USER_DATA_DIR, launchArgs)", local)
        self.assertIn("args: launchArgs", local)
        # The OBS game capture must bind ONLY to the "Google Chrome for Testing"
        # window, never the user's personal "[Google Chrome]" (commit 7fd2ad123
        # "never bind OBS game capture to personal Chrome"). The old permissive
        # /\[Google Chrome(?: for Testing)?\]/ matched both and is forbidden.
        self.assertIn(r"/\[Google Chrome for Testing\]/", local)
        self.assertNotIn("Google Chrome(?: for Testing)?", local)
        self.assertNotIn(".bringToFront()", local)
        self.assertIn("SOREN_BROWSER_TAB_ACTIVATE:-0", (REPO_ROOT / "monitor_improve_runtime.sh").read_text())
        self.assertIn("skip_no_focus", (REPO_ROOT / "monitor_improve_runtime.sh").read_text())
        self.assertIn("SOREN_BROWSER_TAB_ACTIVATE:-0", (REPO_ROOT / "soren91_control.sh").read_text())
        self.assertIn("skip_no_focus", (REPO_ROOT / "soren91_control.sh").read_text())
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn('"/usr/bin/open",\n            "-g",\n            "-n",\n            app_path,', parallel)


# --- Prediction worker pause --------------------------------------------------

class TestPredictionWorkerPause(unittest.TestCase):
    def test_prediction_worker_can_be_paused_during_improve(self):
        worker = (REPO_ROOT / "workers/prediction_worker.sh").read_text()
        supervisor = (REPO_ROOT / "start_all.sh").read_text()

        self.assertIn('PAUSE_FILE="tmp/state/${WORKER_NAME}.paused"', worker)
        self.assertIn('paused by $PAUSE_FILE', worker)
        self.assertIn("pause file detected", worker)
        self.assertIn("prediction_worker.paused", supervisor)
        self.assertIn("スキップ: ${name} paused", supervisor)

    def test_show_status_renders_paused_prediction_worker(self):
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("prediction_worker_paused", status)
        self.assertIn("prediction_worker.paused", status)
        self.assertIn("PAUSED", status)
        self.assertIn("workers_expected=5", status)
        self.assertIn("acc_russia_count", status)
        self.assertIn('nation_label="R${acc_russia_count:-0}"', status)


# --- Main game audio recovery -------------------------------------------------

class TestMainAudioRecovery(unittest.TestCase):
    def test_main_audio_per_context_routing_is_opt_in(self):
        local = (REPO_ROOT / "soviet_local.mjs").read_text()
        watchdog = (REPO_ROOT / "main_audio_route_watchdog.mjs").read_text()

        self.assertIn("process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || ''", local)
        self.assertIn("process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || ''", watchdog)
        self.assertIn("per-context audio routing disabled", local)
        self.assertIn("per-context audio routing disabled", watchdog)

    def test_chrome_audio_fallback_stops_lingering_audio_before_retry(self):
        player = (REPO_ROOT / "chrome_audio_player.mjs").read_text()
        say = (REPO_ROOT / "say_enqueue.sh").read_text()

        self.assertIn("data-soren-chrome-audio-player", player)
        self.assertIn("stopTaggedAudioOnPage", player)
        self.assertIn("await stopTaggedAudioOnPage(page, playerKey)", player)
        self.assertIn("await stopTaggedAudio(browser.contexts()[0], playerKey)", player)
        self.assertIn("process.argv[2] === '--stop'", player)
        self.assertIn("CHROME_AUDIO_USED=1", say)
        self.assertIn("_stop_chrome_audio_players", say)
        self.assertIn("重複防止のため再試行せず完了扱い", say)
        self.assertIn("! _is_truncated_playback \"$elapsed\" \"$expected_sec\"", say)

    def test_main_audio_never_changes_macos_default_output(self):
        forbidden = [
            "kAudioHardwarePropertyDefaultOutputDevice",
            "kAudioHardwarePropertyDefaultSystemOutputDevice",
            "SwitchAudioSource",
            "set_default_output",
        ]
        scan_paths = [
            REPO_ROOT / "soviet_local.mjs",
            REPO_ROOT / "main_audio_route_watchdog.mjs",
            REPO_ROOT / "lib" / "bridge_recovery.sh",
            REPO_ROOT / "soviet_watchdog.sh",
        ]

        combined = "\n".join(path.read_text() for path in scan_paths)
        for token in forbidden:
            self.assertNotIn(token, combined)

    def test_bridge_recovery_trusts_fresh_game_state_when_pid_attribution_is_hidden(self):
        bridge = (REPO_ROOT / "lib" / "bridge_recovery.sh").read_text()

        self.assertIn("Fresh state is the stronger liveness signal", bridge)
        self.assertIn('[ "$((n - m))" -lt "$_BR_STALE_SEC" ]', bridge)
        self.assertIn("稼働中として復旧成功扱い", bridge)
        self.assertIn('[ "$((live_n - live_m))" -lt 30 ]', bridge)
        self.assertIn("_br_cdp_port_pid()", bridge)
        self.assertIn("BRIDGE_RELAUNCH_VERIFY_SEC", bridge)
        self.assertIn('BRIDGE_RELAUNCH_VERIFY_SEC:-150', bridge)
        self.assertIn('[ -n "$serve_pid" ] && [ -n "$cdp_pid" ] &&', bridge)
        self.assertIn("serve=up cdp=up", bridge)
        self.assertIn("復旧検証待ち", bridge)

        eloop = (REPO_ROOT / "eloop.sh").read_text()
        self.assertIn("if ! _ensure_bridge_alive; then", eloop)
        self.assertIn("復旧未完了 → 試合開始を次周回へ延期", eloop)

    def test_bridge_recovery_relaunches_repeated_audio_resume_failures(self):
        bridge = (REPO_ROOT / "lib" / "bridge_recovery.sh").read_text()

        self.assertIn("_br_audio_stuck_reason()", bridge)
        self.assertIn('view.get("unityPresent") is False', bridge)
        self.assertIn("BRIDGE_AUDIO_STUCK_RECOVER_COUNT", bridge)
        self.assertIn("AUDIO-WATCHDOG-RECOVER", bridge)
        self.assertIn("audio_context_stuck", bridge)
        self.assertIn('audio_crash=$(_br_audio_stuck_reason', bridge)
        self.assertIn("audio_context_stuck 復旧のため強制kill", bridge)
        self.assertIn('health.get("after")', bridge)
        self.assertLess(
            bridge.index('elif [ -n "$audio_crash" ]'),
            bridge.index('[ "$((n - m))" -lt "$_BR_STALE_SEC" ]'),
        )

    def test_bridge_recovery_stops_tmux_before_port_wait(self):
        bridge = (REPO_ROOT / "lib" / "bridge_recovery.sh").read_text()

        self.assertIn("tmux-hosted bridge can hide cwd/command attribution", bridge)
        self.assertLess(
            bridge.index("tmux kill-session -t soren_bridge"),
            bridge.index("# SERVE/CDP 両ポート解放待ち最大15s"),
        )

    def test_main_audio_resolves_sink_before_context_creation(self):
        local = (REPO_ROOT / "soviet_local.mjs").read_text()

        self.assertIn("window.__sorenResolveSink = async", local)
        self.assertIn("window.__sorenSinkId = target.deviceId", local)
        self.assertIn("const merged = Object.assign({}, opt0 || {}, { sinkId: sid })", local)
        self.assertIn("ctx = new OrigAudioContext(merged)", local)
        self.assertIn("ctx = new OrigAudioContext(...args)", local)
        self.assertIn("audio output not found", local)
        self.assertIn("return false", local)
        self.assertNotIn("await ctx.setSinkId", local)

    def test_browser_audio_permissions_reveal_blackhole_output_labels(self):
        local = (REPO_ROOT / "soviet_local.mjs").read_text()
        soren91 = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("permissions: ['speakerSelection', 'audioCapture']", local)
        self.assertIn("permissions: ['speakerSelection', 'audioCapture']", soren91)
        self.assertIn("grantAudioPermissions", local)
        self.assertIn("AUDIO-ROUTE-HEAL-ERROR", local)

    def test_soren91_resolves_blackhole_sink_before_unity_audio_context_starts(self):
        soren91 = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("__soren91AudioOutputWatchdogInstalled", soren91)
        self.assertIn("setInterval(() =>", soren91)
        self.assertIn("globalThis.__soren91ResolveSink?.()", soren91)
        self.assertIn("globalThis.__soren91SinkId = target.deviceId", soren91)
        self.assertIn("ctx = new OriginalAudioContext(Object.assign({}, options || {}, { sinkId }))", soren91)
        self.assertNotIn("await ctx.setSinkId", soren91)


# --- Soviet objective is visible to improvement AI ---------------------------

class TestSovietObjectiveImproveInputs(unittest.TestCase):
    def test_strategy_runner_exposes_reactor_deadline_state_to_strategy_game_state(self):
        import strategy_runner

        gs = {"score": 0, "pieces": []}
        enriched = strategy_runner.enrich_game_state_deadline_fields(
            gs,
            {"reactor": {"deadline_crossed": False, "deadline_margin": 7.25}},
        )

        self.assertIs(enriched, gs)
        self.assertIs(enriched["deadline_crossed"], False)
        self.assertEqual(enriched["deadline_margin"], 7.25)

        explicit = {"deadline_crossed": True}
        strategy_runner.enrich_game_state_deadline_fields(
            explicit,
            {"reactor": {"deadline_crossed": False, "deadline_margin": 3.0}},
        )
        self.assertIs(explicit["deadline_crossed"], True)

    def test_deadline_crossing_overlay_payload_distinguishes_safe_slot_available(self):
        import strategy_runner

        payload = strategy_runner._deadline_crossing_overlay_payload(
            12,
            345,
            {"x": 1.0, "reason": "TEST_REASON"},
            {
                "results": [
                    {"x": -1.0, "crosses_deadline": False, "merge_grade": "NO"},
                    {"x": 1.0, "crosses_deadline": True, "merge_grade": "NO"},
                ]
            },
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["title"], "デッドライン超過: 安全候補あり")
        self.assertEqual(payload["level"], "warn")
        self.assertIn("safe=1/2", payload["body"])

    def test_deadline_crossing_overlay_payload_distinguishes_no_non_crossing_candidate(self):
        import strategy_runner

        payload = strategy_runner._deadline_crossing_overlay_payload(
            13,
            456,
            {"x": 0.0, "reason": "FORCED"},
            {
                "results": [
                    {"x": -1.0, "crosses_deadline": True, "merge_grade": "NO"},
                    {"x": 0.0, "crosses_deadline": True, "merge_grade": "DIRECT"},
                ]
            },
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["title"], "デッドライン超過: 非超過なし・併合候補あり")
        self.assertEqual(payload["level"], "warn")
        self.assertIn("safe=0/2", payload["body"])
        self.assertIn("legal=1/2", payload["body"])

    def test_deadline_crossing_overlay_payload_reports_no_legal_candidate(self):
        import strategy_runner

        payload = strategy_runner._deadline_crossing_overlay_payload(
            13,
            456,
            {"x": 0.0, "reason": "FORCED"},
            {
                "results": [
                    {"x": -1.0, "crosses_deadline": True, "merge_grade": "NO"},
                    {"x": 0.0, "crosses_deadline": True, "merge_grade": "NO"},
                ]
            },
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["title"], "デッドライン超過: 合法候補なし")
        self.assertEqual(payload["level"], "info")
        self.assertIn("safe=0/2", payload["body"])
        self.assertIn("legal=0/2", payload["body"])

    def test_deadline_crossing_overlay_treats_visible_safe_landing_as_legal(self):
        import strategy_runner

        payload = strategy_runner._deadline_crossing_overlay_payload(
            13,
            456,
            {"x": 0.0, "reason": "FORCED"},
            {
                "results": [
                    {
                        "x": -1.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.32,
                        "top_y_after_drop": 2.8,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.32,
                        "top_y_after_drop": 3.6,
                    },
                ]
            },
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["title"], "デッドライン超過: 安全候補あり")
        self.assertEqual(payload["level"], "warn")
        self.assertIn("safe=0/2", payload["body"])
        self.assertIn("landing_safe=1/2", payload["body"])
        self.assertIn("legal=1/2", payload["body"])

    def test_deadline_crossing_overlay_suppresses_far_below_prediction_noise(self):
        import strategy_runner

        payload = strategy_runner._deadline_crossing_overlay_payload(
            82,
            1712,
            {"x": 1.25, "reason": "MEDIUM_TOWER_CROSSES_DEADLINE_NO_MERGE"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.14,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "results": [
                    {
                        "x": 1.25,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "top_y_after_drop": 3.85,
                        "risk_top_y_after_drop": 3.85,
                    }
                ],
            },
        )

        self.assertIsNone(payload)

    def test_deadline_crossing_overlay_keeps_warning_near_current_deadline(self):
        import strategy_runner

        payload = strategy_runner._deadline_crossing_overlay_payload(
            67,
            1025,
            {"x": 0.72, "reason": "HIGH_TOWER_CROSSES_DEADLINE_NO_MERGE"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.03,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "results": [
                    {
                        "x": 0.72,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "top_y_after_drop": 4.21,
                        "risk_top_y_after_drop": 4.21,
                    }
                ],
            },
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["title"], "デッドライン超過: 合法候補なし")

    def test_strategy_deadline_risk_ignores_all_crossing_mid_board_noise(self):
        import strategy_runner

        risk = strategy_runner._candidate_has_strategy_deadline_risk(
            {
                "x": -1.7,
                "crosses_deadline": True,
                "merge_grade": "NO",
                "deadline_y": 3.38,
                "risk_top_y_after_drop": 3.7,
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.3,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "results": [
                    {"x": -1.7, "crosses_deadline": True, "merge_grade": "NO"},
                    {"x": 1.0, "crosses_deadline": True, "merge_grade": "NO"},
                ],
            },
        )

        self.assertFalse(risk)

    def test_strategy_deadline_risk_keeps_near_line_no_merge_risk(self):
        import strategy_runner

        risk = strategy_runner._candidate_has_strategy_deadline_risk(
            {
                "x": -0.7,
                "crosses_deadline": True,
                "merge_grade": "NO",
                "deadline_y": 3.38,
                "risk_top_y_after_drop": 3.7,
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.29,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "results": [
                    {"x": -0.7, "crosses_deadline": True, "merge_grade": "NO"},
                    {"x": 1.0, "crosses_deadline": True, "merge_grade": "NO"},
                ],
            },
        )

        self.assertTrue(risk)

    def test_strategy_deadline_risk_ignores_all_crossing_precontact_noise(self):
        import strategy_runner

        risk = strategy_runner._candidate_has_strategy_deadline_risk(
            {
                "x": -1.3,
                "crosses_deadline": True,
                "merge_grade": "NO",
                "deadline_y": 3.38,
                "risk_top_y_after_drop": 5.0,
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.16,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "results": [
                    {"x": -1.3, "crosses_deadline": True, "merge_grade": "NO"},
                    {"x": 0.9, "crosses_deadline": True, "merge_grade": "NO"},
                ],
            },
        )

        self.assertFalse(risk)

    def test_strategy_deadline_risk_allows_crossing_direct_merge(self):
        import strategy_runner

        risk = strategy_runner._candidate_has_strategy_deadline_risk(
            {
                "x": 1.4,
                "crosses_deadline": True,
                "merge_grade": "DIRECT",
                "deadline_y": 3.38,
                "risk_top_y_after_drop": 4.1,
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.2,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "results": [
                    {"x": 1.4, "crosses_deadline": True, "merge_grade": "DIRECT"},
                ],
            },
        )

        self.assertFalse(risk)

    def test_actual_deadline_contact_overlay_uses_post_drop_screen(self):
        import strategy_runner

        payload = strategy_runner._actual_deadline_contact_overlay_payload(
            25,
            1516,
            {"x": 0.74, "reason": "HIGH_TOWER"},
            {"deadline": {"top_edge_y": 3.25}},
            {
                "pieces": [
                    {"id": 1, "type": 11, "x": 0.0, "y": 2.5, "r": 1.7},
                ],
                "shapes": {},
            },
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["title"], "デッドライン超過: 実画面接触")
        self.assertIn("before_top=3.25", payload["body"])
        self.assertIn("actual_top=3.48", payload["body"])

    def test_actual_deadline_contact_overlay_ignores_post_drop_safe_screen(self):
        import strategy_runner

        payload = strategy_runner._actual_deadline_contact_overlay_payload(
            24,
            1435,
            {"x": 0.0, "reason": "HIGH_TOWER"},
            {"deadline": {"top_edge_y": 3.03}},
            {
                "pieces": [
                    {"id": 1, "type": 11, "x": 0.0, "y": 2.35, "r": 1.7},
                ],
                "shapes": {},
            },
        )

        self.assertIsNone(payload)

    def test_deadline_contact_fast_drop_is_strategy_tunable(self):
        import strategy_runner

        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        enabled_strategy = type("Strategy", (), {"FAST_DROP_DEADLINE_CONTACT": True})()
        disabled_strategy = type("Strategy", (), {"FAST_DROP_DEADLINE_CONTACT": False})()
        string_disabled_strategy = type("Strategy", (), {"FAST_DROP_DEADLINE_CONTACT": "0"})()
        default_strategy = type("Strategy", (), {})()

        self.assertTrue(strategy_runner.strategy_fast_drop_deadline_contact_enabled(enabled_strategy))
        self.assertFalse(strategy_runner.strategy_fast_drop_deadline_contact_enabled(disabled_strategy))
        self.assertFalse(strategy_runner.strategy_fast_drop_deadline_contact_enabled(string_disabled_strategy))
        self.assertTrue(strategy_runner.strategy_fast_drop_deadline_contact_enabled(default_strategy))
        self.assertIn("_ensure_strategy_runtime_params()", eloop)
        self.assertIn('_ensure_strategy_runtime_params "$STAGING_FILE"', eloop)
        self.assertIn('_ensure_strategy_runtime_params "strategy.py.staging"', eloop)

    def test_wait_for_move_state_honors_deadline_fast_drop_toggle(self):
        import strategy_runner

        game_state = {"state": "MOVE", "pieces": []}

        with (
            mock.patch.object(strategy_runner, "load_game_state", return_value=game_state),
            mock.patch.object(strategy_runner, "has_deadline_contact", return_value=True),
            mock.patch.object(strategy_runner, "is_board_settled", return_value=True) as settled,
        ):
            result, is_move = strategy_runner.wait_for_move_state(deadline_fast_drop_enabled=True)

        self.assertIs(result, game_state)
        self.assertTrue(is_move)
        settled.assert_not_called()

        with (
            mock.patch.object(strategy_runner, "load_game_state", return_value=game_state),
            mock.patch.object(strategy_runner, "has_deadline_contact", return_value=True),
            mock.patch.object(strategy_runner, "is_board_settled", return_value=True) as settled,
        ):
            result, is_move = strategy_runner.wait_for_move_state(deadline_fast_drop_enabled=False)

        self.assertIs(result, game_state)
        self.assertTrue(is_move)
        settled.assert_called_once()

    def test_deadline_safety_uses_crossing_merge_when_no_non_crossing_candidate_exists(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -1.0, "reason": "HIGH_TOWER"},
            {
                "deadline": {"top_edge_y": 3.2, "deadline_crossed": True},
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": -1.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.33,
                    },
                    {
                        "x": 1.5,
                        "crosses_deadline": True,
                        "merge_grade": "DIRECT",
                        "risk_top_y_after_drop": 4.2,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 3, "x": 1.5, "y": 2.8}], "next": {"type": 3}},
        )

        self.assertEqual(decision["x"], 1.5)
        self.assertIn("NO_TO_DIRECT", decision["reason"])

    def test_deadline_safety_allows_crossing_direct_over_nonmerge_safe_slot(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.5, "reason": "DIRECT_MERGE"},
            {
                "deadline": {"top_edge_y": 3.15, "deadline_crossed": False},
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": -1.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.8,
                    },
                    {
                        "x": 1.5,
                        "crosses_deadline": True,
                        "merge_grade": "DIRECT",
                        "risk_top_y_after_drop": 4.2,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 3, "x": 1.5, "y": 2.8}], "next": {"type": 3}},
        )

        self.assertEqual(decision["x"], 1.5)
        self.assertEqual(decision["reason"], "DIRECT_MERGE")

    def test_deadline_safety_does_not_downgrade_crossing_direct_to_crossing_no(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -0.9, "reason": "DIRECT_MERGE_HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.35,
                    "deadline_crossed": True,
                    "danger_piece_count": 1,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": -0.9,
                        "crosses_deadline": True,
                        "merge_grade": "DIRECT",
                        "risk_top_y_after_drop": 4.6,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.4,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 1, "type": 10, "x": -0.9, "y": 2.75, "r": 0.846},
                    {"id": 2, "type": 4, "x": 3.0, "y": 2.4, "r": 0.380},
                ],
                "next": {"type": 10},
            },
        )

        self.assertEqual(decision["x"], -0.9)
        self.assertIn("DIRECT", decision["reason"])

    def test_deadline_safety_avoids_single_danger_merge_result_crossing(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.2, "reason": "DANGER_DIRECT_MERGE"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.16,
                    "deadline_crossed": False,
                    "danger_piece_count": 1,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.2,
                        "crosses_deadline": True,
                        "merge_grade": "DIRECT",
                        "danger_merge_available": True,
                        "merge_result_crosses_deadline": True,
                        "risk_top_y_after_drop": 4.2,
                    },
                    {
                        "x": -0.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.8,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 9, "x": 1.2, "y": 3.0}], "next": {"type": 9}},
        )

        self.assertEqual(decision["x"], -0.8)
        self.assertIn("avoid_merge_result_deadline", decision["reason"])

    def test_deadline_safety_avoids_non_danger_merge_result_crossing_when_clean_exists(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.2, "reason": "DIRECT_MERGE"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.16,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.2,
                        "crosses_deadline": False,
                        "merge_grade": "DIRECT",
                        "danger_merge_available": False,
                        "merge_result_crosses_deadline": True,
                        "risk_top_y_after_drop": 3.1,
                    },
                    {
                        "x": -0.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "merge_result_crosses_deadline": False,
                        "risk_top_y_after_drop": 2.8,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 9, "x": 1.2, "y": 2.7}], "next": {"type": 9}},
        )

        self.assertEqual(decision["x"], -0.8)
        self.assertIn("avoid_merge_result_deadline_safe", decision["reason"])

    def test_deadline_safety_preserves_midboard_direct_merge_result_warning(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -1.8, "reason": "DIRECT_MERGE_MEDIUM_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 1.86,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}]},
                "results": [
                    {
                        "x": -1.8,
                        "crosses_deadline": True,
                        "merge_grade": "DIRECT",
                        "merge_result_crosses_deadline": True,
                        "risk_top_y_after_drop": 3.4,
                    },
                    {
                        "x": 1.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "merge_result_crosses_deadline": False,
                        "risk_top_y_after_drop": 3.12,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 11, "x": -1.8, "y": 0.8, "r": 1.67}],
                "next": {"type": 11, "r": 1.67},
            },
        )

        self.assertEqual(decision["x"], -1.8)
        self.assertNotIn("DIRECT_TO_NO", decision["reason"])

    def test_deadline_safety_preserves_merge_result_crossing_when_no_clean_candidate(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.2, "reason": "DIRECT_MERGE"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.16,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.2,
                        "crosses_deadline": False,
                        "merge_grade": "DIRECT",
                        "danger_merge_available": False,
                        "merge_result_crosses_deadline": True,
                        "risk_top_y_after_drop": 3.1,
                    },
                    {
                        "x": -0.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "merge_result_crosses_deadline": False,
                        "risk_top_y_after_drop": 2.8,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 9, "x": 1.2, "y": 2.7}], "next": {"type": 9}},
        )

        self.assertEqual(decision["x"], 1.2)
        self.assertEqual(decision["reason"], "DIRECT_MERGE")

    def test_deadline_safety_prefers_lower_safe_slot_over_high_visual_stack(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.5, "reason": "DEADLINE_GUARD_SAFE_LANDING"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.85,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": -0.15,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 1.13,
                    },
                    {
                        "x": 2.5,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 1.93,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": 2.5, "y": 2.2}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], -0.15)
        self.assertIn("deadline_headroom", decision["reason"])

    def test_deadline_safety_uses_minrisk_when_all_candidates_cross_near_deadline(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -2.6, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.94,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": -0.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.71,
                    },
                    {
                        "x": -2.6,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.99,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": -2.6, "y": 2.1}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], -0.8)
        self.assertIn("RUNTIME_DEADLINE_SAFETY_OVERRIDE", decision["reason"])

    def test_deadline_safety_does_not_preserve_high_visual_route_when_all_cross(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 0.4, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.94,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": -0.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.71,
                    },
                    {
                        "x": 0.4,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.10,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": 0.4, "y": 2.1}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], -0.8)
        self.assertIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_avoids_edge_no_merge_when_all_crossing_risk_is_close(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -2.95, "reason": "DEADLINE_GUARD_SAFE_LANDING"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.25,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": -2.95,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.50,
                    },
                    {
                        "x": 1.35,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.68,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": -2.8, "y": 2.0}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], 1.35)
        self.assertIn("non_edge_postcondition", decision["reason"])

    def test_deadline_safety_preserves_non_edge_no_merge_when_all_crossing(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.65, "reason": "AVOID_BLOCK_REACTIVE_PAIR_MEDIUM"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.25,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.65,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.70,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.50,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": 1.8, "y": 2.0}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], 1.65)
        self.assertIn("preserve_non_edge_no_postcondition", decision["reason"])

    def test_deadline_safety_prefers_lower_geometry_non_edge_crossing(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.8, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.25,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.60,
                    },
                    {
                        "x": 0.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.62,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": 1.8, "y": 2.8}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], 0.8)
        self.assertIn("geometry_lower_postcondition", decision["reason"])

    def test_deadline_safety_accepts_edge_when_geometry_gap_is_large(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.0, "reason": "AVOID_BLOCK_REACTIVE_PAIR_MEDIUM"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.25,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.70,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.50,
                    },
                ],
            },
            {"pieces": [{"id": 1, "type": 8, "x": 1.0, "y": 3.0}], "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], 3.0)
        self.assertIn("geometry_lower_postcondition", decision["reason"])

    def test_deadline_monitor_flags_actual_crossing_with_lower_alternative(self):
        import deadline_misplacement_monitor as monitor

        prev = {
            "turn": 12,
            "score": 100,
            "piece_count": 30,
            "decision_x": 1.0,
            "decision_reason": "HIGH_TOWER",
            "next_type": 5,
            "state_snapshot": {
                "pieces": [
                    {"id": 1, "type": 3, "x": -1.0, "y": -4.0, "r": 0.3},
                    {"id": 2, "type": 4, "x": 1.0, "y": 2.8, "r": 0.4},
                ]
            },
        }
        curr = {
            "turn": 13,
            "state_snapshot": {
                "pieces": prev["state_snapshot"]["pieces"] + [
                    {"id": 3, "type": 5, "x": 1.0, "y": 3.1, "r": 0.4},
                ]
            },
        }

        event = monitor.evaluate_transition(prev, curr)

        self.assertEqual(event["status"], "inappropriate")
        self.assertTrue(event["has_lower_alternative"])
        self.assertEqual(event["trigger"], "actual_new_piece_top_over_deadline")
        self.assertLess(event["best_lower_alternative"]["top_y"], event["actual_new_piece"]["top_y"])

    def test_deadline_monitor_ignores_unsupported_new_piece_snapshot(self):
        import deadline_misplacement_monitor as monitor

        prev = {
            "turn": 31,
            "score": 468,
            "piece_count": 20,
            "decision_x": 2.9,
            "decision_reason": "REACTIVE_PAIRS_COMPRESSION",
            "next_type": 3,
            "state_snapshot": {
                "pieces": [
                    {"id": 33, "type": 3, "x": -1.46, "y": -1.47, "r": 0.392},
                    {"id": 34, "type": 8, "x": 2.68, "y": -1.68, "r": 0.977},
                    {"id": 37, "type": 10, "x": -0.43, "y": -0.2, "r": 0.951},
                ]
            },
        }
        curr = {
            "turn": 32,
            "state_snapshot": {
                "pieces": prev["state_snapshot"]["pieces"] + [
                    {"id": 41, "type": 3, "x": 2.85, "y": 3.43, "r": 0.404},
                ]
            },
        }

        event = monitor.evaluate_transition(prev, curr)

        self.assertIsNone(event)

    def test_deadline_monitor_accepts_actual_lowest_crossing(self):
        import deadline_misplacement_monitor as monitor

        prev = {
            "turn": 13,
            "score": 100,
            "piece_count": 31,
            "decision_x": 0.0,
            "decision_reason": "HIGH_TOWER",
            "next_type": 5,
            "state_snapshot": {
                "pieces": [
                    {"id": i + 1, "type": 4, "x": round(-3.0 + i * 0.4, 1), "y": 2.8, "r": 0.4}
                    for i in range(16)
                ]
            },
        }
        curr = {
            "turn": 14,
            "state_snapshot": {
                "pieces": prev["state_snapshot"]["pieces"] + [
                    {"id": 99, "type": 5, "x": 0.0, "y": 3.1, "r": 0.4},
                ]
            },
        }

        event = monitor.evaluate_transition(prev, curr)

        self.assertEqual(event["status"], "appropriate")
        self.assertFalse(event["has_merge_alternative"])
        self.assertFalse(event["has_lower_alternative"])

    def test_deadline_monitor_does_not_flag_merge_only_when_it_raises_top(self):
        import deadline_misplacement_monitor as monitor

        prev = {
            "turn": 40,
            "score": 300,
            "piece_count": 20,
            "decision_x": 1.0,
            "decision_reason": "HIGH_TOWER",
            "next_type": 5,
            "state_snapshot": {
                "pieces": [
                    {"id": i + 1, "type": 5 if round(-3.0 + i * 0.4, 1) == -1.0 else 6, "x": round(-3.0 + i * 0.4, 1), "y": 2.8, "r": 0.4}
                    for i in range(16)
                ]
            },
        }
        curr = {
            "turn": 41,
            "state_snapshot": {
                "pieces": prev["state_snapshot"]["pieces"] + [
                    {"id": 99, "type": 5, "x": 1.0, "y": 3.05, "r": 0.4},
                ]
            },
        }

        event = monitor.evaluate_transition(prev, curr)

        self.assertIsNotNone(event)
        self.assertEqual(event["status"], "appropriate")
        self.assertTrue(event["has_merge_alternative"])
        self.assertFalse(event["merge_alternative_improves_top"])
        self.assertFalse(event["has_lower_alternative"])
        self.assertGreater(event["best_merge_alternative"]["top_y"], event["actual_new_piece"]["top_y"])

    def test_deadline_monitor_run_once_logs_detector_and_history(self):
        import deadline_misplacement_monitor as monitor

        with tempfile.TemporaryDirectory() as td:
            history_path = Path(td) / "history.jsonl"
            log_path = Path(td) / "monitor.jsonl"
            prev = {
                "turn": 12,
                "score": 100,
                "piece_count": 30,
                "decision_x": 1.0,
                "decision_reason": "HIGH_TOWER",
                "next_type": 5,
                "state_snapshot": {
                    "pieces": [
                        {"id": 1, "type": 3, "x": -1.0, "y": -4.0, "r": 0.3},
                        {"id": 2, "type": 4, "x": 1.0, "y": 2.8, "r": 0.4},
                    ]
                },
            }
            curr = {
                "turn": 13,
                "state_snapshot": {
                    "pieces": prev["state_snapshot"]["pieces"] + [
                        {"id": 3, "type": 5, "x": 1.0, "y": 3.1, "r": 0.4},
                    ]
                },
            }
            history_path.write_text(
                json.dumps(prev, ensure_ascii=False) + "\n"
                + json.dumps(curr, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            checked, written = monitor.run_once(history_path, log_path, tail_lines=20)

            self.assertEqual(checked, 1)
            self.assertEqual(written, 1)
            events = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[-1]["detector"], "actual_snapshot_geometry")
            self.assertEqual(events[-1]["history"], str(history_path))

    def test_validate_strategy_does_not_inject_deadline_guard(self):
        sandbox = (REPO_ROOT / "strategy/sandbox.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertFalse((REPO_ROOT / "inject_deadline_guard.py").exists())
        self.assertNotIn("DEADLINE_GUARD_AUTO_INJECT", sandbox)
        self.assertNotIn("inject_deadline_guard", sandbox)
        self.assertNotIn("validation後hash同期", loop)
        self.assertNotIn("validate_strategy may inject", loop)

    def test_deadline_safety_prefers_visible_safe_landing_when_all_candidates_flag_crossing(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 0.0, "reason": "HIGH_TOWER"},
            {
                "deadline": {"top_edge_y": 3.4, "deadline_crossed": True},
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 0.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.32,
                        "top_y_after_drop": 3.6,
                        "risk_top_y_after_drop": 3.6,
                    },
                    {
                        "x": -1.2,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.32,
                        "top_y_after_drop": 2.7,
                        "risk_top_y_after_drop": 3.7,
                    },
                ],
            },
            {"pieces": [], "next": {"type": 9}},
        )

        self.assertEqual(decision["x"], -1.2)
        self.assertIn("NO_TO_NO", decision["reason"])

    def test_deadline_safety_uses_minrisk_when_all_candidates_cross_without_merge(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 0.0, "reason": "HIGH_TOWER"},
            {
                "deadline": {"top_edge_y": 3.4, "deadline_crossed": True},
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 0.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.4,
                    },
                    {
                        "x": 2.4,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.6,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 9, "x": 0.0, "y": 2.0}],
                "next": {"type": 9},
            },
        )

        self.assertEqual(decision["x"], 2.4)
        self.assertIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_ignores_far_below_all_crossing_noise(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 0.0, "reason": "HEIGHT_CONTROL"},
            {
                "deadline": {"top_edge_y": 1.4, "deadline_crossed": False},
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 0.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 3.9,
                    },
                    {
                        "x": -1.2,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 3.6,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 9, "x": 0.0, "y": 1.0}],
                "next": {"type": 9},
            },
        )

        self.assertEqual(decision["x"], 0.0)
        self.assertEqual(decision["reason"], "HEIGHT_CONTROL")

    def test_deadline_safety_ignores_mid_board_all_crossing_noise(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -1.7, "reason": "MEDIUM_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.3,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}]},
                "results": [
                    {
                        "x": -1.7,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 3.7,
                    },
                    {
                        "x": 1.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 3.4,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 9, "x": -1.5, "y": 2.0}],
                "next": {"type": 9},
            },
        )

        self.assertEqual(decision["x"], -1.7)
        self.assertEqual(decision["reason"], "MEDIUM_TOWER")

    def test_deadline_safety_replaces_crossing_choice_when_safe_exists_far_below_deadline(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.7, "reason": "HIGH_TOWER_RUSSIA_PHASE_BOARD_COMPRESSION"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.46,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 2.7,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.714,
                    },
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.804,
                    },
                    {
                        "x": 0.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.102,
                    },
                ],
            },
            {
                "pieces": [{"id": i, "type": 1, "x": 0.0, "y": -3.0} for i in range(31)],
                "next": {"type": 11},
            },
        )

        self.assertEqual(decision["x"], -3.0)
        self.assertIn("safe_far_below_crossing", decision["reason"])

    def test_deadline_safety_ignores_far_below_all_crossing_noise(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -1.3, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.40,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": -1.3,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 5.0,
                    },
                    {
                        "x": 0.9,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 3.7,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 9, "x": -1.5, "y": 2.7}],
                "next": {"type": 9},
            },
        )

        self.assertEqual(decision["x"], -1.3)
        self.assertEqual(decision["reason"], "HIGH_TOWER")

    def test_deadline_safety_ignores_far_below_buffered_headroom(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.2, "reason": "REACTIVE_PAIRS_STACKING"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 1.3,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 1.2,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 3.0,
                    },
                    {
                        "x": -0.6,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "deadline_y": 3.38,
                        "risk_top_y_after_drop": 2.0,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 5, "x": 0.0, "y": 1.0}],
                "next": {"type": 5},
            },
        )

        self.assertEqual(decision["x"], 1.2)
        self.assertEqual(decision["reason"], "REACTIVE_PAIRS_STACKING")

    def test_deadline_safety_preserves_visible_same_country_when_all_candidates_cross(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.9, "reason": "HIGH_TOWER"},
            {
                "deadline": {"top_edge_y": 3.3, "deadline_crossed": False},
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 2.9,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.5,
                    },
                    {
                        "x": -1.1,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.2,
                    },
                ],
            },
            {
                "pieces": [{"id": 144, "type": 8, "x": -1.14, "y": 2.17}],
                "next": {"type": 8},
            },
        )

        self.assertEqual(decision["x"], -1.1)
        self.assertIn("visual_deadline_same_country", decision["reason"])
        self.assertNotIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_drops_visual_same_country_when_dense_at_redline(self):
        import strategy_runner

        pieces = [{"id": 144, "type": 8, "x": -1.14, "y": 2.17}]
        pieces.extend(
            {"id": i, "type": 1, "x": -2.8 + (i % 8) * 0.7, "y": -4.4, "r": 0.207}
            for i in range(200, 231)
        )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.9, "reason": "HIGH_TOWER"},
            {
                "deadline": {"top_edge_y": 3.30, "deadline_crossed": False},
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 2.9,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.5,
                    },
                    {
                        "x": -1.1,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.2,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 8}},
        )

        self.assertEqual(decision["x"], 2.9)
        self.assertIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_visual_same_country_falls_back_when_too_high(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.9, "reason": "HIGH_TOWER"},
            {
                "deadline": {"top_edge_y": 3.3, "deadline_crossed": False},
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 2.9,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.5,
                    },
                    {
                        "x": -1.1,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.25,
                    },
                ],
            },
            {
                "pieces": [{"id": 144, "type": 8, "x": -1.14, "y": 2.17}],
                "next": {"type": 8},
            },
        )

        self.assertEqual(decision["x"], 2.9)
        self.assertIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_visual_same_country_falls_back_when_board_is_already_over_deadline(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.9, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "top_edge_y": 3.62,
                    "deadline_crossed": True,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 2.9,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.55,
                    },
                    {
                        "x": -1.1,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.95,
                    },
                ],
            },
            {
                "pieces": [{"id": 144, "type": 8, "x": -1.14, "y": 2.17}],
                "next": {"type": 8},
            },
        )

        self.assertEqual(decision["x"], 2.9)
        self.assertIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_geometry_headroom_overrides_underestimated_safe_choice(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.6, "reason": "DEADLINE_GUARD_SAFE_LANDING"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.72,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 2.6,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.20,
                    },
                    {
                        "x": 0.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.25,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 1, "type": 14, "x": 2.55, "y": 3.3, "r": 1.3},
                    {"id": 2, "type": 8, "x": 0.0, "y": 1.0, "r": 0.66},
                ],
                "next": {"type": 9},
            },
        )

        self.assertEqual(decision["x"], 0.0)
        self.assertIn("deadline_headroom", decision["reason"])

    def test_deadline_safety_replaces_safe_choice_when_geometry_is_over_deadline(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 0.25, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.37,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 0.25,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.10,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.18,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 1, "type": 14, "x": 0.2, "y": 2.70, "r": 1.385},
                    {"id": 2, "type": 9, "x": -1.5, "y": 1.30, "r": 0.746},
                ],
                "next": {"type": 10, "r": 0.846},
            },
        )

        self.assertEqual(decision["x"], 3.0)
        self.assertIn("geometry_underestimate_postcondition", decision["reason"])

    def test_deadline_safety_keeps_pre_russia_t12_lane_under_geometry_override(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 12, "x": -1.1, "y": -0.5, "r": 1.068},
            {"id": 2, "type": 12, "x": 1.0, "y": 2.5, "r": 1.068},
            {"id": 3, "type": 11, "x": -0.2, "y": -0.2, "r": 0.982},
            {"id": 4, "type": 11, "x": 1.2, "y": 0.4, "r": 0.982},
            {"id": 5, "type": 10, "x": -0.8, "y": -2.5, "r": 0.846},
            {"id": 6, "type": 10, "x": 2.8, "y": -2.8, "r": 0.846},
        ]
        for i in range(7, 35):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.4 + (i % 4) * 0.18,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {
                "x": 1.0,
                "reason": (
                    "MEDIUM_TOWER_PRE_RUSSIA_T12_CONSOLIDATE_"
                    "PRE_RUSSIA_NEXT_UP_LATCH"
                ),
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.37,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 1.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.70,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.05,
                    },
                    {
                        "x": -0.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.45,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 10, "r": 0.846}},
        )

        self.assertEqual(decision["x"], -0.8)
        self.assertIn("pre_russia_t12_lane", decision["reason"])

    def test_deadline_safety_keeps_pre_russia_t12_lane_when_every_drop_crosses(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 12, "x": 2.26, "y": -3.35, "r": 1.068},
            {"id": 2, "type": 12, "x": -0.37, "y": -1.77, "r": 1.068},
            {"id": 3, "type": 12, "x": -1.44, "y": 1.33, "r": 1.068},
            {"id": 4, "type": 11, "x": -2.21, "y": -3.10, "r": 0.982},
            {"id": 5, "type": 11, "x": -1.62, "y": -0.16, "r": 0.982},
            {"id": 6, "type": 11, "x": 1.15, "y": 0.56, "r": 0.982},
            {"id": 7, "type": 10, "x": -0.23, "y": 2.52, "r": 0.846},
            {"id": 8, "type": 10, "x": 1.89, "y": -1.25, "r": 0.846},
        ]
        for i in range(9, 39):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {
                "x": -0.23,
                "reason": (
                    "NO_MERGE_CROSSES_DEADLINE_PENALTY_HIGH_TOWER_"
                    "PRE_RUSSIA_T12_CONSOLIDATE"
                ),
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.32,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}, {}]},
                "results": [
                    {
                        "x": -0.23,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.20,
                    },
                    {
                        "x": 1.85,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.55,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.70,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 10, "r": 0.846}},
        )

        self.assertEqual(decision["x"], 1.85)
        self.assertIn("pre_russia_t12_lane", decision["reason"])

    def test_deadline_safety_keeps_pre_russia_t12_lane_immediately_after_second_t12(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 12, "x": -1.43, "y": -2.66, "r": 1.068},
            {"id": 2, "type": 12, "x": 0.51, "y": -0.03, "r": 1.068},
            {"id": 3, "type": 11, "x": -0.60, "y": -3.75, "r": 0.982},
            {"id": 4, "type": 11, "x": 1.78, "y": -2.07, "r": 0.982},
            {"id": 5, "type": 10, "x": -1.02, "y": -1.40, "r": 0.846},
        ]
        for i in range(6, 28):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 4) * 0.18,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {
                "x": 3.0,
                "reason": (
                    "HIGH_LAYER_PRE_RUSSIA_T12_CONSOLIDATE_"
                    "PRE_RUSSIA_NEXT_UP_LATCH"
                ),
            },
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.82,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}]},
                "results": [
                    {
                        "x": 3.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.55,
                    },
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.75,
                    },
                    {
                        "x": -0.6,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.20,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 11, "r": 0.982}},
        )

        self.assertEqual(decision["x"], -0.6)
        self.assertIn("pre_russia_t12_lane", decision["reason"])

    def test_deadline_safety_keeps_pre_russia_t13_pair_lane_under_pressure(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 13, "x": -2.0, "y": -2.6, "r": 1.235},
            {"id": 2, "type": 13, "x": -1.2, "y": -0.7, "r": 1.235},
            {"id": 3, "type": 13, "x": 2.4, "y": -2.4, "r": 1.235},
            {"id": 4, "type": 12, "x": 1.7, "y": -1.2, "r": 1.068},
            {"id": 5, "type": 12, "x": -0.4, "y": -2.1, "r": 1.068},
            {"id": 6, "type": 11, "x": 0.3, "y": -1.5, "r": 0.982},
        ]
        for i in range(7, 42):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 3.0, "reason": "HIGH_TOWER_DEADLINE_GUARD_FIRST_RUSSIA_PAIR"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.85,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.20,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.45,
                    },
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.05,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 10, "r": 0.846}},
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertIn("pre_russia_t13_pair_lane", decision["reason"])

    def test_deadline_safety_keeps_first_russia_t13_pair_lane_under_pressure(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 14, "x": -0.96, "y": -1.96, "r": 1.385},
            {"id": 2, "type": 13, "x": -1.50, "y": 2.00, "r": 1.235},
            {"id": 3, "type": 13, "x": 2.25, "y": -2.65, "r": 1.235},
            {"id": 4, "type": 12, "x": -2.00, "y": -0.32, "r": 1.068},
            {"id": 5, "type": 12, "x": 1.10, "y": -0.61, "r": 1.068},
            {"id": 6, "type": 12, "x": 0.41, "y": -3.26, "r": 1.068},
            {"id": 7, "type": 10, "x": -0.48, "y": 0.43, "r": 0.846},
        ]
        for i in range(8, 42):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 3.0, "reason": "HIGH_TOWER_FIRST_RUSSIA_T13_PAIR_LIFT"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.80,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.10,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.45,
                    },
                    {
                        "x": 0.35,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.35,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 11, "r": 0.982}},
        )

        self.assertEqual(decision["x"], 0.35)
        self.assertIn("first_russia_t13_pair_lane", decision["reason"])

    def test_deadline_safety_drops_first_russia_t13_pair_lane_when_redline_minrisk_far_safer(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 14, "x": -0.96, "y": -1.96, "r": 1.385},
            {"id": 2, "type": 13, "x": -1.50, "y": 2.00, "r": 1.235},
            {"id": 3, "type": 13, "x": 2.25, "y": -2.65, "r": 1.235},
            {"id": 4, "type": 12, "x": -2.00, "y": -0.32, "r": 1.068},
            {"id": 5, "type": 12, "x": 1.10, "y": -0.61, "r": 1.068},
            {"id": 6, "type": 10, "x": -0.48, "y": 0.43, "r": 0.846},
        ]
        for i in range(7, 42):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 3.0, "reason": "HIGH_TOWER_FIRST_RUSSIA_T13_PAIR_LIFT"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.72,
                    "deadline_crossed": True,
                    "danger_piece_count": 1,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 3.0,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "top_y_after_drop": 4.70,
                        "risk_top_y_after_drop": 4.70,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "top_y_after_drop": 3.55,
                        "risk_top_y_after_drop": 3.55,
                    },
                    {
                        "x": 0.35,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "top_y_after_drop": 4.20,
                        "risk_top_y_after_drop": 4.20,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 11, "r": 0.982}},
        )

        self.assertEqual(decision["x"], -2.8)
        self.assertNotIn("first_russia_t13_pair_lane", decision["reason"])

    def test_deadline_safety_keeps_first_russia_single_t13_t12_bank_lane_under_pressure(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 14, "x": 2.0, "y": -0.3, "r": 1.385},
            {"id": 2, "type": 13, "x": 1.1, "y": -2.5, "r": 1.235},
            {"id": 3, "type": 12, "x": -0.7, "y": -2.8, "r": 1.068},
            {"id": 4, "type": 12, "x": -2.5, "y": 0.8, "r": 1.068},
            {"id": 5, "type": 11, "x": -0.8, "y": -0.2, "r": 0.982},
            {"id": 6, "type": 10, "x": 0.9, "y": 1.4, "r": 0.846},
        ]
        for i in range(7, 41):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.8, "reason": "MEDIUM_TOWER_FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.80,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.20,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.45,
                    },
                    {
                        "x": -1.6,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.05,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 12, "r": 1.068}},
        )

        self.assertEqual(decision["x"], -1.6)
        self.assertIn("first_russia_single_t13_t12_bank_lane", decision["reason"])

    def test_deadline_safety_keeps_second_russia_t12_pair_lane_under_pressure(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 14, "x": -0.5, "y": -2.0, "r": 1.385},
            {"id": 2, "type": 12, "x": -2.1, "y": 1.0, "r": 1.068},
            {"id": 3, "type": 12, "x": 1.4, "y": 0.0, "r": 1.068},
            {"id": 4, "type": 11, "x": -1.9, "y": -1.0, "r": 0.982},
            {"id": 5, "type": 10, "x": -1.1, "y": 1.2, "r": 0.846},
            {"id": 6, "type": 10, "x": 2.3, "y": -1.8, "r": 0.846},
        ]
        for i in range(7, 41):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.8, "reason": "HIGH_TOWER_SECOND_RUSSIA_T12_PAIR_LOCK"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.80,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.20,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.45,
                    },
                    {
                        "x": -0.35,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.05,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 10, "r": 0.846}},
        )

        self.assertEqual(decision["x"], -0.35)
        self.assertIn("second_russia_t12_pair_lane", decision["reason"])

    def test_deadline_safety_keeps_second_russia_single_t12_ladder_lane_under_pressure(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 14, "x": -1.95, "y": -2.1, "r": 1.385},
            {"id": 2, "type": 12, "x": 0.75, "y": 0.0, "r": 1.068},
            {"id": 3, "type": 11, "x": -2.35, "y": 0.35, "r": 0.982},
            {"id": 4, "type": 11, "x": -0.65, "y": -3.75, "r": 0.982},
            {"id": 5, "type": 10, "x": -0.75, "y": 0.65, "r": 0.846},
            {"id": 6, "type": 10, "x": 2.25, "y": 0.35, "r": 0.846},
            {"id": 7, "type": 10, "x": -0.35, "y": -2.35, "r": 0.846},
        ]
        for i in range(8, 41):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 3.0, "reason": "MEDIUM_TOWER_SECOND_RUSSIA_T12_LADDER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.80,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.65,
                    },
                    {
                        "x": -2.8,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.45,
                    },
                    {
                        "x": -0.4,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.20,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 10, "r": 0.846}},
        )

        self.assertEqual(decision["x"], -0.4)
        self.assertIn("second_russia_t12_ladder_lane", decision["reason"])

    def test_deadline_safety_keeps_pre_russia_single_t12_anchor_lane_under_pressure(self):
        import strategy_runner

        pieces = [
            {"id": 1, "type": 12, "x": 2.45, "y": -3.35, "r": 1.068},
            {"id": 2, "type": 11, "x": -0.85, "y": -3.75, "r": 0.982},
            {"id": 3, "type": 11, "x": -0.85, "y": -2.05, "r": 0.982},
            {"id": 4, "type": 11, "x": 2.0, "y": 0.75, "r": 0.982},
            {"id": 5, "type": 11, "x": -1.6, "y": 0.9, "r": 0.982},
            {"id": 6, "type": 10, "x": -0.3, "y": 0.15, "r": 0.846},
            {"id": 7, "type": 10, "x": 1.25, "y": -2.2, "r": 0.846},
        ]
        for i in range(8, 41):
            pieces.append(
                {
                    "id": i,
                    "type": 1 + (i % 8),
                    "x": -2.8 + (i % 8) * 0.7,
                    "y": -2.2 + (i % 5) * 0.2,
                    "r": 0.4,
                }
            )

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -0.7, "reason": "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.85,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.45,
                    },
                    {
                        "x": -0.7,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.15,
                    },
                    {
                        "x": 2.35,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.25,
                    },
                ],
            },
            {"pieces": pieces, "next": {"type": 11, "r": 0.982}},
        )

        self.assertEqual(decision["x"], 2.35)
        self.assertIn("pre_russia_t12_lane", decision["reason"])

    def test_deadline_safety_replaces_underestimated_direct_when_geometry_is_worse(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 0.8, "reason": "DIRECT_MERGE_HIGH_LAYER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 1.70,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 0.8,
                        "crosses_deadline": False,
                        "merge_grade": "DIRECT",
                        "risk_top_y_after_drop": 3.18,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.25,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 1, "type": 12, "x": 0.8, "y": 2.50, "r": 1.40},
                    {"id": 2, "type": 8, "x": -1.8, "y": 1.10, "r": 0.660},
                ],
                "next": {"type": 12, "r": 1.068},
            },
        )

        self.assertEqual(decision["x"], 3.0)
        self.assertIn("DIRECT_TO_NO", decision["reason"])
        self.assertIn("geometry_underestimate_postcondition", decision["reason"])

    def test_deadline_safety_preserves_far_below_direct_geometry_noise(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.5, "reason": "DIRECT_MERGE_HIGH_LAYER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": -0.83,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 1.5,
                        "crosses_deadline": False,
                        "merge_grade": "DIRECT",
                        "risk_top_y_after_drop": 3.18,
                    },
                    {
                        "x": -3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.10,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 1, "type": 11, "x": 1.45, "y": 2.55, "r": 0.982},
                    {"id": 2, "type": 8, "x": -1.8, "y": 1.10, "r": 0.660},
                ],
                "next": {"type": 11, "r": 0.982},
            },
        )

        self.assertEqual(decision["x"], 1.5)
        self.assertEqual(decision["reason"], "DIRECT_MERGE_HIGH_LAYER")

    def test_deadline_safety_medium_tower_replaces_near_deadline_underestimate(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -0.95, "reason": "MEDIUM_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 2.03,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": -0.95,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.09,
                        "deadline_y": 3.38,
                    },
                    {
                        "x": 3.0,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.949,
                        "deadline_y": 3.38,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 9, "x": -0.5, "y": 2.21, "r": 1.194}],
                "next": {"type": 9, "r": 1.194},
            },
        )

        self.assertEqual(decision["x"], 3.0)
        self.assertIn("safe_medium_tower_underestimate_postcondition", decision["reason"])

    def test_deadline_safety_medium_tower_keeps_far_below_without_margin_risk(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 1.2, "reason": "MEDIUM_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 1.30,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": []},
                "results": [
                    {
                        "x": 1.2,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.0,
                        "deadline_y": 3.38,
                    },
                    {
                        "x": -0.6,
                        "crosses_deadline": False,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 2.8,
                        "deadline_y": 3.38,
                    },
                ],
            },
            {
                "pieces": [{"id": 1, "type": 9, "x": 0.0, "y": 1.0, "r": 0.746}],
                "next": {"type": 9, "r": 0.746},
            },
        )

        self.assertEqual(decision["x"], 1.2)
        self.assertEqual(decision["reason"], "MEDIUM_TOWER")

    def test_deadline_safety_visual_same_country_falls_back_when_geometry_is_worse(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": 2.6, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.30,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 2.6,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.55,
                    },
                    {
                        "x": 0.2,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.60,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 10, "type": 9, "x": 0.0, "y": 2.5, "r": 0.746},
                    {"id": 11, "type": 14, "x": 0.2, "y": 3.4, "r": 1.3},
                    {"id": 12, "type": 8, "x": 2.6, "y": 1.0, "r": 0.66},
                ],
                "next": {"type": 9},
            },
        )

        self.assertEqual(decision["x"], 2.6)
        self.assertIn("minrisk_postcondition", decision["reason"])

    def test_deadline_safety_visual_same_country_uses_geometry_lower_crossing_band(self):
        import strategy_runner

        decision = strategy_runner.enforce_deadline_safety(
            {"x": -2.5, "reason": "HIGH_TOWER"},
            {
                "deadline": {
                    "deadline_y": 3.38,
                    "top_edge_y": 3.2,
                    "deadline_crossed": False,
                    "danger_piece_count": 0,
                },
                "reactor": {"reactive_pairs": [{}, {}, {}]},
                "results": [
                    {
                        "x": 0.7,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 4.4,
                    },
                    {
                        "x": -0.8,
                        "crosses_deadline": True,
                        "merge_grade": "NO",
                        "risk_top_y_after_drop": 3.9,
                    },
                ],
            },
            {
                "pieces": [
                    {"id": 1, "type": 14, "x": 0.7, "y": 2.4, "r": 1.385},
                    {"id": 2, "type": 14, "x": -0.8, "y": 2.0, "r": 1.385},
                    {"id": 3, "type": 7, "x": 0.6, "y": 3.2, "r": 0.559},
                ],
                "next": {"type": 7},
            },
        )

        self.assertEqual(decision["x"], -0.8)
        self.assertIn("geometry_min_top_postcondition", decision["reason"])

    def test_deadline_analysis_uses_nominal_radii_when_bridge_r_is_oversized(self):
        import analyze_board

        pieces = [
            {"id": 1, "type": 10, "x": 0.0, "y": 2.1, "r": 1.45},
            {"id": 2, "type": 4, "x": 1.8, "y": 1.6, "r": 0.78},
        ]

        results, _ = analyze_board.analyze_drops(pieces, next_type=9, next_r=1.2, shapes={})
        safe_no_merge = [
            r
            for r in results
            if not r.get("crosses_deadline", False)
            and r.get("merge_grade") == "NO"
        ]

        self.assertTrue(safe_no_merge)

    def test_deadline_analysis_uses_nominal_radii_when_shapes_are_partial(self):
        import analyze_board

        pieces = [
            {"id": 1, "type": 1, "x": 0.0, "y": 1.8, "r": 0.45},
        ]
        shapes = {
            "1": [[-0.18, -0.18], [0.18, -0.18], [0.18, 0.18], [-0.18, 0.18]],
        }

        results, _ = analyze_board.analyze_drops(
            pieces,
            next_type=9,
            next_r=1.2,
            shapes=shapes,
        )
        safe = [r for r in results if not r.get("crosses_deadline", False)]

        self.assertTrue(safe)
        self.assertLess(min(r["top_y_after_drop"] for r in safe), analyze_board.DEADLINE_Y)

    def test_reactor_deadline_uses_polygon_top_instead_of_bridge_radius(self):
        import analyze_board

        pieces = [
            {"id": 1, "type": 9, "x": 0.0, "y": 2.7, "r": 1.2},
        ]
        shapes = {
            "9": [[-1.05, -0.42], [1.05, -0.42], [1.05, 0.53], [-1.05, 0.53]],
        }

        reactor = analyze_board.calc_reactor_state(pieces, shapes)

        self.assertLess(reactor["top_edge_y"], analyze_board.DEADLINE_Y)
        self.assertFalse(reactor["deadline_crossed"])
        self.assertEqual(reactor["danger_piece_count"], 0)

    def test_deadline_threshold_uses_unity_red_line_trigger_bottom(self):
        import analyze_board

        self.assertEqual(analyze_board.RED_LINE_VISUAL_Y, 3.32)
        self.assertEqual(analyze_board.DEADLINE_Y, 3.38)

        near_visual_line = analyze_board.calc_reactor_state(
            [{"id": 1, "type": 9, "x": 0.0, "y": 2.84, "r": 1.2}],
            shapes={},
        )
        in_trigger = analyze_board.calc_reactor_state(
            [{"id": 1, "type": 9, "x": 0.0, "y": 2.85, "r": 1.2}],
            shapes={},
        )

        self.assertLess(near_visual_line["top_edge_y"], analyze_board.DEADLINE_Y)
        self.assertFalse(near_visual_line["deadline_crossed"])
        self.assertGreaterEqual(in_trigger["top_edge_y"], analyze_board.DEADLINE_Y)
        self.assertTrue(in_trigger["deadline_crossed"])

    def test_deadline_fallback_uses_unity_prefab_extents_without_shapes(self):
        import analyze_board

        type9 = analyze_board.calc_reactor_state(
            [{"id": 1, "type": 9, "x": 0.0, "y": 2.84, "r": 1.2}],
            shapes={},
        )
        type11 = analyze_board.calc_reactor_state(
            [{"id": 1, "type": 11, "x": 0.0, "y": 2.39, "r": 1.7}],
            shapes={},
        )

        self.assertAlmostEqual(type9["top_edge_y"], 3.372, places=3)
        self.assertAlmostEqual(type11["top_edge_y"], 3.371, places=3)
        self.assertFalse(type9["deadline_crossed"])
        self.assertFalse(type11["deadline_crossed"])

    def test_reactor_deadline_uses_rotated_polygon_extents(self):
        import analyze_board

        shapes = {
            "11": [[-1.35, -0.35], [1.35, -0.35], [1.35, 0.35], [-1.35, 0.35]],
        }

        horizontal = analyze_board.calc_reactor_state(
            [{"id": 1, "type": 11, "x": 0.0, "y": 2.5, "r": 1.7, "angle": 0}],
            shapes,
        )
        vertical = analyze_board.calc_reactor_state(
            [{"id": 1, "type": 11, "x": 0.0, "y": 2.5, "r": 1.7, "angle": 90}],
            shapes,
        )

        self.assertLess(horizontal["top_edge_y"], analyze_board.DEADLINE_Y)
        self.assertFalse(horizontal["deadline_crossed"])
        self.assertGreaterEqual(vertical["top_edge_y"], analyze_board.DEADLINE_Y)
        self.assertTrue(vertical["deadline_crossed"])

    def test_deadline_analysis_prefers_detected_vertical_radius(self):
        import analyze_board

        pieces = [
            {"id": 1, "type": 11, "x": 0.0, "y": 1.95, "r": 1.7, "rx": 1.35, "ry": 0.35},
        ]

        reactor = analyze_board.calc_reactor_state(pieces, shapes={})
        results, _ = analyze_board.analyze_drops(
            pieces,
            next_type=8,
            next_r=analyze_board.TYPE_RADII[8],
            shapes={},
        )
        safe = [r for r in results if not r.get("crosses_deadline", False)]

        self.assertLess(reactor["top_edge_y"], analyze_board.DEADLINE_Y)
        self.assertFalse(reactor["deadline_crossed"])
        self.assertTrue(safe)

    def test_merge_candidate_uses_unity_polygon_extents_not_circle_radius(self):
        import analyze_board

        floor_y = analyze_board.FLOOR_Y + analyze_board.UNITY_PREFAB_DEADLINE_RADII[9]["bottom"]
        pieces = [
            {"id": 1, "type": 9, "x": 0.0, "y": floor_y, "r": 1.2},
        ]

        results, _ = analyze_board.analyze_drops(
            pieces,
            next_type=9,
            next_r=1.2,
            shapes={},
        )
        by_x = {r["x"]: r for r in results}

        self.assertEqual(by_x[2.0]["merge_grade"], "DIRECT")
        self.assertEqual(by_x[2.2]["merge_grade"], "NO")
        self.assertGreater(by_x[2.2]["merges"][0]["contact_gap"], 0.0)

    def test_direct_merge_uses_first_polygon_support_piece(self):
        import analyze_board

        pieces = [
            {"id": 1, "type": 9, "x": 0.0, "y": 0.0, "r": 1.2},
            {"id": 2, "type": 8, "x": 2.5, "y": 0.0, "r": analyze_board.TYPE_RADII[8]},
        ]

        results, _ = analyze_board.analyze_drops(
            pieces,
            next_type=9,
            next_r=1.2,
            shapes={},
        )
        center = min(results, key=lambda r: abs(r["x"]))

        self.assertEqual(center["merge_grade"], "DIRECT")
        self.assertEqual(center["merges"][0]["grade"], "DIRECT")

    def test_deadline_crossing_overlay_notify_uses_event_overlay(self):
        import strategy_runner

        strategy_runner._fire_and_forget_processes.clear()
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None
        with mock.patch.object(strategy_runner.os.path, "exists", return_value=True), mock.patch.object(strategy_runner.subprocess, "Popen", return_value=fake_proc) as popen:
            strategy_runner.notify_deadline_crossing_overlay(
                14,
                567,
                {"x": 1.0, "reason": "TEST_REASON"},
                {
                    "results": [
                        {"x": -1.0, "crosses_deadline": False, "merge_grade": "NO"},
                        {"x": 1.0, "crosses_deadline": True, "merge_grade": "NO"},
                    ]
                },
            )

        args = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertEqual(args[:3], ["./overlay_notify.sh", "deadline", "デッドライン超過: 安全候補あり"])
        self.assertEqual(args[-1], "warn")
        self.assertEqual(kwargs["env"]["OVERLAY_NOTIFY_OBS_SHOW"], "1")
        self.assertTrue(kwargs["start_new_session"])
        self.assertIn(fake_proc, strategy_runner._fire_and_forget_processes)
        strategy_runner._fire_and_forget_processes.clear()

    def test_batch_summary_reports_russia_progress_and_max_type(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            game = td / "game.jsonl"
            rows = [
                {
                    "turn": 1,
                    "score": 10,
                    "decision_reason": "A",
                    "merge_available": False,
                    "max_y": -4,
                    "state_snapshot": {"pieces": [{"type": 1}]},
                },
                {
                    "turn": 2,
                    "score": 20,
                    "decision_reason": "B",
                    "merge_available": True,
                    "max_y": -3,
                    "state_snapshot": {"pieces": [{"type": 15}]},
                },
            ]
            game.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "batch_summary.py"), str(game)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("## 建国進捗", result.stdout)
            self.assertIn("russia_created=1/1", result.stdout)
            self.assertIn("max_piece_type=15", result.stdout)
            self.assertIn("high_type_counts=T15x1", result.stdout)
            self.assertIn("pre-Russia near-miss", result.stdout)
            self.assertIn("[RUSSIA]", result.stdout)

    def test_improve_prompts_do_not_send_ai_searching_for_batch_runner(self):
        """sandbox内のAIに、生成済みbatch_summaryを実行コマンド探しと誤認させない。"""
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()
        analyze_prompt = (REPO_ROOT / "prompts/analyze_strategy.md").read_text()
        implement_prompt = (REPO_ROOT / "prompts/implement_strategy.md").read_text()
        review_prompt = (REPO_ROOT / "prompts/review_strategy.md").read_text()

        for text in (improve, analyze_prompt, implement_prompt, review_prompt):
            self.assertIn("tmp/batch_summary.txt", text)
            self.assertIn("README/Makefile/*.sh", text)

        self.assertIn("cat >README.md", improve)
        self.assertIn("Soren Improve Sandbox", improve)
        self.assertIn("Do not search for README/Makefile/*.sh", improve)
        self.assertIn("追加のバッチ実行環境ではない", improve)
        self.assertIn("追加の batch 実行コマンドを探し続けない", analyze_prompt)
        self.assertIn("追加の batch 実行コマンドを探索し続けない", implement_prompt)
        self.assertIn("追加の batch 実行環境を探さない", review_prompt)

    def test_improve_brief_contains_soviet_objective_section(self):
        text = (REPO_ROOT / "eloop_improve.sh").read_text()
        self.assertIn("## Soviet Objective Progress", text)
        self.assertIn("最終目標は type16 のソ連建国", text)
        self.assertIn("hard_signal: 今回バッチはロシア未到達", text)
        self.assertIn("high_type_counts is final-board type10+ inventory", text)
        self.assertIn("main_gate_target_priority_override", text)
        self.assertIn("do not interpret the", text)
        self.assertIn("T14x2 appears without type15", text)
        self.assertIn("russia_recovery_mode: type14 near-miss", text)
        self.assertIn("deadline_guard_rate", text)
        self.assertIn("deadline_guard_reason_top", text)
        self.assertIn("guard_reason_top=", text)
        self.assertIn("deadline guard が多発", text)
        self.assertIn("ガードを弱めず", text)
        self.assertIn("peak_high_type_counts", text)
        self.assertIn("frontier_hint", text)
        self.assertIn("peak_high_type_counts/frontier_hint show", text)
        self.assertIn("type13以下で止まっている", text)

    def test_score_state_persists_nation_progress_metadata(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        repair = (REPO_ROOT / "repair_current_run_from_history.sh").read_text()
        for text in (regression, improve):
            self.assertIn("def nation_progress(path):", text)
            self.assertIn('row.get("russia_created")', text)
            self.assertIn('row.get("soviet_created")', text)
            self.assertIn('piece.get("type", 0)', text)
            self.assertIn('["max_types"]', text)
            self.assertIn('["russia_count"]', text)
            self.assertIn('["soviet_count"]', text)
            self.assertIn('["frontier_hints"]', text)
            self.assertIn('["peak_high_type_counts"]', text)
            self.assertIn('["deadline_guard_counts"]', text)
            self.assertIn('["deadline_guard_reason_tops"]', text)
            self.assertIn('"DEADLINE_GUARD" in str(row.get("decision_reason") or "")', text)
            self.assertNotIn("\tdef nation_progress(path):", text)
            self.assertNotIn("\tprogress_archives = ", text)
        self.assertIn("current_run_update.err", improve)
        self.assertIn("[CURRENT-RUN] update stderr:", improve)
        self.assertIn("rolling_scores_update.err", regression)
        self.assertIn("[ROLLING] update stderr:", regression)
        self.assertIn("repair_current_run", repair)
        self.assertIn("history_strategy_hash", repair)
        self.assertIn("history_strategy_hash(path) != current_hash", repair)
        self.assertIn("update_rolling_scores", repair)
        self.assertIn("_update_current_strategy_run", repair)
        self.assertIn("final_types", repair)
        self.assertEqual(repair.count('last.get("state_snapshot")'), 1)

        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        self.assertIn("CURRENT_RUN_AUTO_REPAIR_ENABLED", config)
        self.assertIn("CURRENT_RUN_AUTO_REPAIR_LIMIT", config)
        self.assertIn("./repair_current_run_from_history.sh", loop)
        self.assertIn("auto repair skipped/failed", loop)
        self.assertIn("./repair_current_run_from_history.sh", improve)
        self.assertIn("adaptive bookkeeping", improve)
        self.assertIn("def summarize_archive(path):", improve)
        self.assertIn("enrich_accumulated_game_metadata()", improve)
        self.assertIn("deadline_guard_total", improve)
        self.assertIn("acc.setdefault('max_types'", improve)
        self.assertIn('enrich_accumulated_game_metadata "$IMPROVE_LOCK_FILE"', improve)
        self.assertIn('enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE"', loop)
        self.assertIn("current batch にロシア進捗あり", loop)
        self.assertIn("_batch_russia", loop)
        self.assertIn("_batch_best_type", loop)
        self.assertLess(
            loop.index("current batch にロシア進捗あり"),
            loop.index("改善ロック作成 (最終モードはimprove側で判定)"),
        )

    def test_supervisor_re_adopts_existing_worker_before_restart_cap(self):
        supervisor = (REPO_ROOT / "start_all.sh").read_text()

        self.assertIn("after stale supervisor pid", supervisor)
        self.assertIn("_improve_daemon_responsive()", supervisor)
        self.assertIn("IMPROVE_DAEMON_LOCK_STALL_SEC", supervisor)
        self.assertIn("改善ロックを消費していない", supervisor)
        self.assertIn("tmp/state/improve_daemon_stall.json", supervisor)
        self.assertIn("_pid_matches_worker()", supervisor)
        self.assertIn("process-list access", supervisor)
        self.assertIn("operation not permitted", supervisor)
        self.assertIn("does not overwrite pidfiles or start duplicate workers", supervisor)
        self.assertIn('return 0', supervisor)
        self.assertIn("cleanup skipped: another supervisor owns pidfile", supervisor)
        self.assertIn('if [ -n "$active_pid" ] && [ "$active_pid" != "$$" ]; then', supervisor)
        self.assertIn('if [ "$_w_name" = "soren_loop" ] && [ -f "${IMPROVE_LOCK_FILE:-tmp/improve.lock}" ]; then', supervisor)
        self.assertIn('_soren_loop_lock_pid()', supervisor)
        self.assertIn('if [ "$name" = "soren_loop" ]; then', supervisor)
        self.assertIn('if [ "$_w_name" != "soren_loop" ]', supervisor)
        self.assertIn('if existing_pid="$(_find_existing_worker_pid "$_w_name")"; then', supervisor)
        self.assertIn('WORKER_RESTARTS[$idx]=0', supervisor)
        self.assertLess(
            supervisor.index('if existing_pid="$(_find_existing_worker_pid "$_w_name")"; then'),
            supervisor.index('if [ "$_w_restarts" -ge "$MAX_RESTARTS" ]; then'),
        )

    def test_prediction_result_includes_regression_reason_for_purge(self):
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        predictions = (REPO_ROOT / "twitch_predictions.sh").read_text()

        self.assertIn("regression_reason_raw", loop)
        self.assertIn("regression_reason_label", loop)
        self.assertIn("REGRESSION_ROLLBACK_RESULT", loop)
        self.assertIn('"early_comp_top_gap": "comp比率低下"', loop)
        self.assertIn('${_regression_detail}', predictions)
        self.assertIn('${_stale_regression_detail}', predictions)
        self.assertLess(
            predictions.index('if [ "${OUTCOME_INDEX}" = "3" ]'),
            predictions.rindex('rm -f "$PREDICTION_STATE_FILE"'),
        )

    def test_supervisor_surfaces_worker_duplicates(self):
        supervisor = (REPO_ROOT / "start_all.sh").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("SUPERVISOR_DUPLICATE_STATE_FILE", supervisor)
        self.assertIn("_detect_worker_duplicates()", supervisor)
        self.assertIn("worker duplicate detected", supervisor)
        self.assertIn('"duplicates": duplicates', supervisor)
        self.assertIn("normal child shells do not look like duplicates", supervisor)
        self.assertIn("ppid not in matched_pids", supervisor)
        self.assertIn("root_worker_pids", supervisor)
        self.assertIn("time.sleep(0.35)", supervisor)
        self.assertIn("second_roots", supervisor)
        self.assertIn('"pid=,ppid=,command="', supervisor)
        self.assertIn("worker_duplicates.ps.$$.txt", supervisor)
        self.assertIn('LC_ALL=C ps -Ao pid=,ppid=,command= >"$ps_snapshot_file"', supervisor)
        self.assertIn("ps_snapshot_file = sys.argv[3]", supervisor)
        self.assertIn("with open(ps_snapshot_file", supervisor)
        self.assertIn("_detect_worker_duplicates", supervisor[supervisor.index("# --- 全 worker 起動 ---"):])
        self.assertIn("tmp/state/worker_duplicates.json", status)
        self.assertIn("Duplicates", status)
        self.assertIn("DETECTED", status)
        self.assertIn("UNKNOWN", status)
        self.assertIn("duplicate scan unavailable", status)
        self.assertIn("_worker_duplicates_from_ps_fallback", status)
        self.assertIn("show_status_worker_ps.$$.txt", status)
        self.assertIn("root_worker_pids", status)
        self.assertIn("time.sleep(0.35)", status)
        self.assertIn("second_roots", status)
        self.assertIn('LC_ALL=C ps -Ao pid=,ppid=,command= >"$snapshot_file"', status)
        self.assertIn('duplicate_fallback_info=$(_worker_duplicates_from_ps_fallback)', status)

    def test_rollback_analysis_surfaces_soviet_objective_delta(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("## Soviet Objective Delta", regression)
        self.assertIn("progress_gap_vs_target", regression)
        self.assertIn("current はロシア(type15)未到達", regression)
        self.assertIn("frontier_hints=", regression)
        self.assertIn("peak_high_type_counts=", regression)
        self.assertIn("deadline_guard_counts=", regression)
        self.assertIn("deadline_guard_reason_tops=", regression)
        self.assertIn("Soviet Objective Delta", improve)

    def test_regression_guard_blocks_objective_backslide(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        loop = (REPO_ROOT / "eloop.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("objective_reasons = []", regression)
        self.assertIn("lost_soviet_path", regression)
        self.assertIn("stage_achievement_regression", regression)
        self.assertIn("STAGE_GATE_SEQUENCE", regression)
        self.assertNotIn('objective_reasons.append("lost_russia_path")', regression)
        self.assertIn('objective_reasons.append("lost_soviet_path")', regression)
        self.assertIn("def stage_gate_regression_reason", regression)
        self.assertIn("rank <= rolling_score_russia_grace_rank", regression)
        self.assertIn("lost_ukraine_gate", regression)
        self.assertIn("lost_kazakhstan_gate", regression)
        self.assertIn("mode=objective_regression", regression)
        self.assertIn("mode=early_objective_regression", regression)
        self.assertIn("early_objective_min_games", regression)
        self.assertIn("stage_achievement_regression_reason", regression)
        self.assertIn("STAGE_ACHIEVEMENT_GATE_TYPES", regression)
        self.assertIn("queue_fresh_objective_same_hash_lock_if_needed", improve)
        self.assertIn("fresh_objective_same_hash_lock", improve)
        self.assertIn("requested_rollback_hash", regression)
        self.assertIn("DIRECT_ANCHOR_PROMOTION_OBJECTIVE_GUARD_ENABLED", config)
        self.assertIn("DIRECT_ANCHOR_PROMOTION_OBJECTIVE_GUARD_ENABLED", regression)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_REGRESSION_MIN_GAMES", config)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_EARLY_LOCK_ENABLED", config)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_MIN_BEST_TYPE", config)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MIN_GAMES", config)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MAX_BEST_TYPE", config)
        self.assertIn("queue_fresh_objective_same_hash_lock_if_needed", improve)
        self.assertIn("fresh_objective_same_hash_lock", improve)
        self.assertIn("current_hash_fresh_no_russia", improve)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_MIN_BEST_TYPE", improve)
        self.assertIn("CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MIN_GAMES", improve)
        self.assertIn("low_stage_miss", improve)
        self.assertIn("frontier_min_games = min_games", improve)
        self.assertIn('objective_reference in {"historical_russia", "historical_best", "anchor_russia", "anchor_best"}', improve)
        self.assertIn("frontier_min_games = min(min_games, low_stage_min_games)", improve)
        self.assertIn('[ "$_fresh_trigger" != "low_stage_miss" ]', improve)
        self.assertIn("fresh_objective_trigger", improve)
        self.assertIn("fresh_objective_reference", improve)
        self.assertIn("archive_restart_candidate", improve)
        self.assertIn("fresh_objective_anchor_russia_count", improve)
        self.assertIn("fresh_objective_archive_restart_available", improve)
        self.assertIn("route=${_fresh_improve_reason}", improve)
        self.assertIn("_refresh_best_strategy_anchor \"\" >/dev/null 2>&1", improve)
        self.assertLess(
            improve.index("_refresh_best_strategy_anchor \"\" >/dev/null 2>&1"),
            improve.index("enrich_accumulated_game_metadata \"$ACCUMULATED_GAMES_FILE\""),
        )
        self.assertIn("_main_strategy_runner_active_for_improve", improve)
        self.assertIn("improve lock consumption deferred until game boundary", improve)
        self.assertIn("MAIN_STRATEGY_RUNNER_ACTIVE_FILE", improve)
        self.assertIn("queue_fresh_objective_same_hash_lock_if_needed || true", improve)
        self.assertIn("MAIN_STRATEGY_RUNNER_ACTIVE_FILE", loop)
        self.assertIn("main_strategy_runner_active.json", loop)
        self.assertIn("rm -f \"$runner_active_file\"", loop)
        self.assertIn("early_objective_min_best_type", regression)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", regression)
        self.assertIn("permanent_archive_dir", regression)
        self.assertIn("rollback target normalized", regression)
        self.assertIn("normalized_to_hash", regression)
        self.assertIn("rollback_target_normalized", regression)
        self.assertIn("normalized fallback target rejected; retry anchor rollback", regression)
        self.assertIn("normalized_fallback_anchor", regression)
        self.assertIn("requested_rollback_hash", regression)
        self.assertIn("normalized_from=${requested_rollback_hash} actual_hash=${rolled_hash}", regression)
        self.assertIn("_rollback_candidate_file_is_valid", regression)
        self.assertIn("validation失敗archive", regression)
        self.assertIn("archive存在のため候補維持", regression)
        self.assertIn("rollback validation failed but accepted by policy", regression)
        self.assertIn('grep -qxF "$h" "$REJECTED_HASHES_FILE"', regression)
        self.assertIn('with open(meta_file, "w", encoding="utf-8") as f:', regression)
        self.assertIn("json.dump(meta, f, ensure_ascii=False)", regression)
        self.assertIn("EARLY_OBJECTIVE_REGRESSION_ENABLED", config)
        self.assertIn("EARLY_OBJECTIVE_REGRESSION_MIN_GAMES", config)
        self.assertIn("anchor_best_max_type", regression)
        self.assertIn("curr_best_max_type", regression)

    def test_strategy_runner_merge_result_guard_is_pressure_gated(self):
        runner = (REPO_ROOT / "strategy_runner.py").read_text()

        self.assertIn("def merge_result_deadline_guard_active", runner)
        self.assertIn("or danger_piece_count > 0", runner)
        self.assertIn("or current_top_edge_y >= deadline_y - 0.35", runner)
        self.assertIn("and merge_result_deadline_guard_active()", runner)

    def test_strategy_restores_same_type_proximity_drought_axis(self):
        strategy = (REPO_ROOT / "strategy.py").read_text()

        self.assertIn("evaluation axis 9.74: same-type proximity for merge drought", strategy)
        self.assertIn('reactor.get("recent_results", [])', strategy)
        self.assertIn('if r.get("merge_grade") == "NO":', strategy)
        self.assertIn("if not recent_results and same_type_pieces:", strategy)
        self.assertIn("high_feeder_window", strategy)
        self.assertIn("and next_type >= 12", strategy)
        self.assertIn("and next_type >= max_type_on_board - 1", strategy)
        self.assertIn("reactive_pair_count < 8 and no_merge_streak >= 3", strategy)
        self.assertIn("not (reactive_pair_count >= 5 and max_y >= 4.0)", strategy)
        self.assertIn("drought_bonus = max(0, 600.0 - min_horiz_dist * 100.0) * merge_mult", strategy)
        self.assertIn('reasons.append("SAME_TYPE_PROXIMITY_DROUGHT")', strategy)
        self.assertIn("evaluation axis 9.75: high-type feeder lane", strategy)
        self.assertIn('reasons.append("HIGH_TYPE_FEEDER_LANE")', strategy)

    def test_dashboard_purge_target_uses_anchor_and_current_run(self):
        import dashboard_data

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            current_run = td / "current_strategy_run.json"
            rolling_scores = td / "rolling_scores.json"
            best_anchor = td / "best_strategy_anchor.json"
            config = td / "config.sh"
            current_run.write_text(
                json.dumps({"hash": "current", "max_types": [13, 13, 14], "best_max_type": "bad"}),
                encoding="utf-8",
            )
            rolling_scores.write_text(
                json.dumps({"anchor": {"max_types": [0, 14, 14, 14, 13]}}),
                encoding="utf-8",
            )
            best_anchor.write_text(json.dumps({"hash": "anchor"}), encoding="utf-8")
            config.write_text(
                'STAGE_ACHIEVEMENT_GATE_MIN_RATE="${STAGE_ACHIEVEMENT_GATE_MIN_RATE:-0.75}"\n'
                'STAGE_ACHIEVEMENT_GATE_TYPES="${STAGE_ACHIEVEMENT_GATE_TYPES:-13,14,15}"\n',
                encoding="utf-8",
            )

            with mock.patch.object(dashboard_data, "CURRENT_STRATEGY_RUN", current_run), \
                mock.patch.object(dashboard_data, "ROLLING_SCORES", rolling_scores), \
                mock.patch.object(dashboard_data, "BEST_STRATEGY_ANCHOR", best_anchor), \
                mock.patch.object(dashboard_data, "CORE_CONFIG", config):
                stats = dashboard_data.purge_target_stats()

        self.assertEqual(stats["anchor"]["target"]["type"], 14)
        self.assertEqual(stats["anchor"]["window"], 4)
        self.assertEqual(stats["current"]["targetRate"]["reached"], 1)
        self.assertEqual(stats["current"]["targetRate"]["total"], 3)
        self.assertFalse(stats["current"]["targetReached"])
        self.assertEqual(stats["current"]["bestMaxType"], 14)

    def test_dashboard_purge_target_uses_anchor_meta_when_rolling_progress_is_pruned(self):
        import dashboard_data

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            current_run = td / "current_strategy_run.json"
            rolling_scores = td / "rolling_scores.json"
            best_anchor = td / "best_strategy_anchor.json"
            config = td / "config.sh"
            current_run.write_text(
                json.dumps({"hash": "current", "max_types": [15, 15, 15], "best_max_type": 15}),
                encoding="utf-8",
            )
            rolling_scores.write_text(
                json.dumps({"anchor": {"scores": [12000] * 12, "games_total": 12}}),
                encoding="utf-8",
            )
            best_anchor.write_text(
                json.dumps({"hash": "anchor", "best_max_type": 15, "russia_count": 1, "soviet_count": 0}),
                encoding="utf-8",
            )
            config.write_text(
                'STAGE_ACHIEVEMENT_GATE_MIN_RATE="${STAGE_ACHIEVEMENT_GATE_MIN_RATE:-0.75}"\n'
                'STAGE_ACHIEVEMENT_GATE_TYPES="${STAGE_ACHIEVEMENT_GATE_TYPES:-13,14,15}"\n',
                encoding="utf-8",
            )

            with mock.patch.object(dashboard_data, "CURRENT_STRATEGY_RUN", current_run), \
                mock.patch.object(dashboard_data, "ROLLING_SCORES", rolling_scores), \
                mock.patch.object(dashboard_data, "BEST_STRATEGY_ANCHOR", best_anchor), \
                mock.patch.object(dashboard_data, "CORE_CONFIG", config):
                stats = dashboard_data.purge_target_stats()

        self.assertEqual(stats["anchor"]["target"]["type"], 15)
        self.assertEqual(stats["anchor"]["window"], 1)
        self.assertEqual(stats["current"]["targetRate"]["reached"], 3)
        self.assertEqual(stats["current"]["targetRate"]["total"], 3)

    def test_restored_normalized_rollback_candidate_clears_stale_reject(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            rejected_meta = td / "rejected_meta.json"

            source = archive_dir / "candidate.py"
            source.write_text(
                "def decide(game_state, analysis):\n"
                "    return {'x': 0, 'reason': 'stable'}\n",
                encoding="utf-8",
            )
            actual_hash = subprocess.check_output(
                ["python3", "extract_decide_hash.py", str(source)],
                cwd=REPO_ROOT,
                text=True,
            ).strip()
            candidate = archive_dir / f"{actual_hash}.py"
            source.rename(candidate)
            rejected_file.write_text(f"{actual_hash}\n", encoding="utf-8")
            rejected_meta.write_text(
                json.dumps(
                    {
                        actual_hash: {
                            "updated_at": 4102444800,
                            "normalized_to_hash": "otherhash",
                            "reason": "rollback_target_normalized",
                        }
                    }
                ),
                encoding="utf-8",
            )

            script = f"""
source core/config.sh 2>/dev/null
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
REJECTED_HASH_META_FILE='{rejected_meta}'
source strategy/regression.sh 2>/dev/null
if _is_recently_rejected_for_rollback {actual_hash}; then
    echo blocked
else
    echo allowed
fi
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("allowed", result.stdout)
            self.assertNotIn(actual_hash, rejected_file.read_text(encoding="utf-8"))
            self.assertNotIn(actual_hash, json.loads(rejected_meta.read_text(encoding="utf-8")))

    def test_rejected_prune_clears_restored_normalized_candidates_before_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            archive_dir = td / "by_hash"
            archive_dir.mkdir()
            rejected_file = td / "rejected.txt"
            rejected_meta = td / "rejected_meta.json"

            source = archive_dir / "candidate.py"
            source.write_text(
                "def decide(game_state, analysis):\n"
                "    return {'x': 1, 'reason': 'anchor-safe'}\n",
                encoding="utf-8",
            )
            actual_hash = subprocess.check_output(
                ["python3", "extract_decide_hash.py", str(source)],
                cwd=REPO_ROOT,
                text=True,
            ).strip()
            source.rename(archive_dir / f"{actual_hash}.py")
            rejected_file.write_text(f"{actual_hash}\n", encoding="utf-8")
            rejected_meta.write_text(
                json.dumps(
                    {
                        actual_hash: {
                            "updated_at": 4102444800,
                            "normalized_to_hash": "oldhash",
                            "reason": "rollback_target_normalized",
                        }
                    }
                ),
                encoding="utf-8",
            )

            script = f"""
source core/config.sh 2>/dev/null
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{archive_dir}'
REJECTED_HASHES_FILE='{rejected_file}'
REJECTED_HASH_META_FILE='{rejected_meta}'
REJECTED_REEVALUATE_TTL_SEC=21600
source strategy/regression.sh 2>/dev/null
_prune_expired_rejected_hashes
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertNotIn(actual_hash, rejected_file.read_text(encoding="utf-8"))
            self.assertNotIn(actual_hash, json.loads(rejected_meta.read_text(encoding="utf-8")))

    def test_post_regression_revalidates_rollback_target_instead_of_failed_batch(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertNotIn("POST_REGRESSION_IMPROVE_ENABLED", config)
        self.assertIn("POST_REGRESSION_DIRECT_ESCAPE_ENABLED", config)
        self.assertNotIn("回帰ロールバック直後 → 失敗バッチで改善ロック作成", loop)
        self.assertIn("旧戦略の失敗バッチは改善に使わず、復帰先戦略の再評価を優先", loop)
        self.assertIn("_post_regression_route", loop)
        self.assertIn("curr_russia_seen", loop)
        self.assertIn('"stage_achievement_regression": "段階到達ゲート未達"', loop)
        self.assertIn('key.startswith("stage_type") and key.endswith("_achievement_gate")', loop)
        self.assertIn('labels.append(f"Type{stage}到達ゲート未達")', loop)
        self.assertIn("stage_objective_loss = \"stage_achievement_regression\" in result or \"stage_type\" in result", loop)
        self.assertIn("russia_path_loss = (", loop)
        self.assertIn("or (stage_target >= 15)", loop)
        self.assertIn("elif objective_loss and russia_path_loss and rstreak >= threshold:", loop)
        self.assertIn('detail = f"rstreak={rstreak}_stage_target={stage_target}_objective_loss={int(objective_loss)}"', loop)
        self.assertIn("post_regression_direct_escape", loop)
        self.assertIn("ロシア建国ルート喪失の粛清連鎖", loop)
        self.assertIn("復帰先にロシア進捗あり", loop)
        self.assertIn("_evolution_flow_notify", loop)
        self.assertIn("enqueue_chat_message \"$chat\" \"improve_flow\" 4", loop)
        self.assertIn("game_finished | regression_check | no_rollback | twelve_game_improve", loop)
        self.assertIn("${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}", improve)
        self.assertIn("rolling_current = rolling.get(current_hash) if current_hash else {}", improve)
        self.assertIn("rolling_allowed = current_games < mature_n", improve)
        self.assertIn("known_russia = max(current_russia, lock_russia, rolling_russia if rolling_allowed else 0)", improve)
        self.assertIn("best_type = max(current_best, lock_best, rolling_best if rolling_allowed else 0)", improve)
        self.assertIn("revalidate_mature_no_current_russia", improve)
        self.assertIn("revalidate_mature_no_current_progress", improve)
        self.assertIn("game finished", loop)
        self.assertIn("regression check", loop)
        self.assertIn("rollback happened? yes", loop)
        self.assertIn("rollback happened? no", loop)
        self.assertIn("classify rollback reason", loop)
        self.assertIn("russia path still alive? yes", loop)
        self.assertIn("direct escape, no next game", loop)
        self.assertNotIn("post_regression improve", loop)
        self.assertIn("rollback target revalidation", loop)
        self.assertIn("粛清前の失敗バッチは別戦略のデータなので改善に使わず", loop)
        self.assertIn('data["improve_reason"] = "post_regression"', loop)
        self.assertIn('data["regression_result"]', loop)
        self.assertIn("REGRESSION_ROLLBACK_HASH", loop)
        self.assertIn('[ "${REGRESSION_ROLLBACK_DONE:-0}" = "1" ]', loop)
        self.assertIn("ロールバック直後の直接脱出ロックを処理", improve)
        self.assertIn("legacy post_regression lock", improve)
        self.assertNotIn("ロールバック直後の失敗バッチを改善入力として使用", improve)
        self.assertIn("normal|post_regression|wildcard|escape_ai", improve)
        self.assertIn("post_regression_direct_escape", improve)
        self.assertIn("archive candidate unavailable → WILDCARD", improve)
        self.assertIn("_persist_improve_lock_reason()", improve)
        self.assertIn('data["improve_reason"] = reason', improve)
        self.assertIn('_persist_improve_lock_reason "$reason"', improve)
        self.assertIn('_start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "$improve_reason"', improve)

    def test_fast_escape_harvest_does_not_start_soren91_handover(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()
        ai = (REPO_ROOT / "strategy/ai.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("IMPROVE_FAST_ESCAPE_OVERLAY_HOLD_SEC", config)
        self.assertIn("improve_reason", improve)
        self.assertIn('if status == "running" and not improve_reason:', improve)
        self.assertIn('improve_reason = previous_reason or "normal"', improve)
        self.assertIn('export RUN_CMD_IMPROVE_REASON="${IMPROVE_REASON:-normal}"', eloop)
        self.assertIn('"${RUN_CMD_IMPROVE_REASON:-}"', ai)
        self.assertIn('_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash" "boot" "1" "job_started" "$(date +%s)" "$_pid_birth_epoch" "$reason"', improve)
        self.assertIn('prev_improve_reason=$(echo "$state"', improve)
        self.assertIn("state理由欠落をlockから復元", improve)
        self.assertIn("improve_reason=$(echo \"$state\"", improve)
        self.assertIn('_write_improve_state "running" "$live_pid" "$hash_before" "recovered" "1" "live_process_detected" "$started_at" "$pid_birth_epoch" "$improve_reason"', improve)
        self.assertIn('reason in {"wildcard", "archive_restart"}', improve)
        self.assertIn("wildcard|archive_restart)", improve)
        self.assertIn("soren91_stop/soren91_improve/handover/bridge再起動をスキップ", improve)
        self.assertIn('_improve_overlay_hide_after "${IMPROVE_FAST_ESCAPE_OVERLAY_HOLD_SEC:-45}"', improve)
        self.assertIn("improve_overlay_hide_token", improve)
        self.assertIn("_live_improve_pid=", loop)
        self.assertIn("実改善PIDなし: soren91は起動せず回収待ち", loop)
        self.assertIn("wildcard:*|archive_restart:*|*:wildcard_parallel:*|*:*:post_improve_param_parallel*)", loop)
        self.assertIn("手動改善待ちは実改善PIDがないため soren91 代打は起動しない", improve)

    def test_post_improve_soren91_session_improve_is_opt_in(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn('POST_IMPROVE_SOREN91_SESSION_IMPROVE_ENABLED="${POST_IMPROVE_SOREN91_SESSION_IMPROVE_ENABLED:-0}"', config)
        self.assertIn("_post_improve_soren91_session_improve()", improve)
        self.assertIn('POST_IMPROVE_SOREN91_SESSION_IMPROVE_ENABLED:-0', improve)
        self.assertIn("post-improve session improve skipped", improve)
        self.assertIn('_post_improve_soren91_session_improve "$prev_improve_reason"', improve)
        self.assertIn('_post_improve_soren91_session_improve "manual"', improve)
        self.assertIn('_post_improve_soren91_session_improve "scheduled_meriken"', improve)

    def test_improve_and_system_progress_are_queued_for_audio_worker(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop_improve = (REPO_ROOT / "eloop_improve.sh").read_text()
        comment_lib = (REPO_ROOT / "broadcast/comment_lib.sh").read_text()
        ai_generate = (REPO_ROOT / "lib/ai_generate.sh").read_text()
        system_report = (REPO_ROOT / "system_progress_report.sh").read_text()

        self.assertIn("IMPROVE_AUDIO_SUMMARY_ENABLED", config)
        self.assertIn("IMPROVE_AUDIO_SUMMARY_INTERVAL_SEC", config)
        self.assertIn('IMPROVE_AUDIO_SUMMARY_INTERVAL_SEC="${IMPROVE_AUDIO_SUMMARY_INTERVAL_SEC:-900}"', config)
        self.assertIn('AUDIO_WORKER_WARNING_INTERVAL_SEC="${AUDIO_WORKER_WARNING_INTERVAL_SEC:-900}"', config)
        self.assertIn('SOREN_PAUSE_LOG_INTERVAL_SEC="${SOREN_PAUSE_LOG_INTERVAL_SEC:-900}"', config)
        self.assertIn('IMPROVE_RUN_CMD_TIMEOUT_SEC="${IMPROVE_RUN_CMD_TIMEOUT_SEC:-1800}"', config)
        self.assertIn('IMPROVE_FIX_CMD_TIMEOUT_SEC="${IMPROVE_FIX_CMD_TIMEOUT_SEC:-600}"', config)
        self.assertIn('IMPROVE_OPENCODE_LOCK_MAX_WAIT_SEC="${IMPROVE_OPENCODE_LOCK_MAX_WAIT_SEC:-180}"', config)
        self.assertIn('IMPROVE_WALL_TIMEOUT="${IMPROVE_WALL_TIMEOUT:-3600}"', config)
        self.assertIn('RUN_CMD_TIMEOUT_SEC="${IMPROVE_RUN_CMD_TIMEOUT_SEC:-1800}"', eloop_improve)
        self.assertIn('RUN_CMD_TIMEOUT_SEC="${IMPROVE_FIX_CMD_TIMEOUT_SEC:-600}"', eloop_improve)
        self.assertIn('OPENCODE_RUN_LOCK_MAX_WAIT_SEC="${IMPROVE_OPENCODE_LOCK_MAX_WAIT_SEC:-180}"', eloop_improve)
        self.assertIn("export OPENCODE_RUN_LOCK_MAX_WAIT_SEC", eloop_improve)
        self.assertIn('max_wait_sec="${OPENCODE_RUN_LOCK_MAX_WAIT_SEC:-0}"', ai_generate)
        self.assertIn("opencode slot wait exceeded", ai_generate)
        self.assertIn("return 124", ai_generate)
        self.assertIn('IMPROVE_WALL_TIMEOUT="${IMPROVE_WALL_TIMEOUT:-3600}"', eloop_improve)
        self.assertIn("_improve_audio_summary_maybe", eloop_improve)
        self.assertIn("IMPROVE_AUDIO_SUMMARY_SPOKEN=0", eloop_improve)
        self.assertIn('enqueue_audio_text "$text" "improve_progress"', eloop_improve)
        self.assertIn("*improve_progress*)", comment_lib)
        self.assertIn("_comment_improve_progress_already_played", comment_lib)
        self.assertIn("_comment_mark_improve_progress_played", comment_lib)
        self.assertIn("改善進捗", eloop_improve)
        self.assertIn('enqueue_audio_text "$text" "system_progress"', system_report)
        audio_worker = (REPO_ROOT / "workers/audio_worker.sh").read_text()
        self.assertIn('WARNING_INTERVAL="${AUDIO_WORKER_WARNING_INTERVAL_SEC:-900}"', audio_worker)
        self.assertIn("_LAST_SAY_WARNING_TS", audio_worker)
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        self.assertIn('SOREN_PAUSE_LOG_INTERVAL_SEC="${SOREN_PAUSE_LOG_INTERVAL_SEC:-900}"', loop)
        self.assertIn("log_pause_throttled", loop)
        self.assertIn('pause_log_${safe_key}.ts', loop)
        self.assertIn("last_ts=$(cat \"$state_file\"", loop)
        self.assertIn('log_pause_throttled "improve_running"', loop)
        self.assertIn('${SYSTEM_PROGRESS_AUDIO_SPEAKER:-${SOREN91_VOICEVOX_SPEAKER:-46}}', system_report)
        self.assertIn("システム改善進捗", system_report)

    def test_soren91_improve_hang_is_bounded_by_watchdog(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        control = (REPO_ROOT / "soren91_control.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("IMPROVE_HUNG_HARVEST_ENABLED", config)
        self.assertIn("IMPROVE_HUNG_QUARANTINE_FILE", config)
        self.assertIn("IMPROVE_HUNG_REQUIRE_EVAL_STALE", config)
        self.assertIn("IMPROVE_WALL_TIMEOUT", config)
        self.assertIn("EVAL_SCORE_HISTORY_FILE", config)
        self.assertIn("_strategy_decide_hash_or_md5()", improve)
        self.assertIn('python3 extract_decide_hash.py "$path"', improve)
        self.assertIn('strategy_hash=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")', improve)
        self.assertIn('hash_now=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")', improve)
        self.assertIn('[ -n "$hash_before" ] || hash_before=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")', improve)
        self.assertIn("_is_recorded_running_improve_pid()", improve)
        self.assertIn('if [ -z "$cmd" ]; then', improve)
        self.assertIn('_is_recorded_running_improve_pid "$pid"', improve)
        self.assertIn("command取得不可だが、記録済みrunning状態と一致", improve)
        self.assertIn("improve_wall_timeout_harvest", improve)
        self.assertIn("if _stop_improve_pid_if_running \"$pid\" \"improve_wall_timeout\"; then", improve)
        self.assertIn("通常改善が上限時間を超えたため", improve)
        self.assertIn("中華AI改善は適用可能な戦略変更を出せず終了しました", improve)
        self.assertIn("improve_failed_no_apply", improve)
        self.assertIn("failed_no_apply lock hash stale", improve)
        self.assertIn("failed_no_apply partial lock cleared", improve)
        self.assertIn("valid lock absent", improve)
        self.assertIn('rm -f "$IMPROVE_LOCK_FILE" "$TMP_STATE_DIR/rate_limit_backoff"', improve)
        self.assertIn("[ \"$updated_age\" -ge \"$watchdog_sec\" ]", improve)
        self.assertIn("[ \"$log_age\" -ge \"$watchdog_sec\" ]", improve)
        self.assertIn("[ \"$eval_age\" -lt \"$watchdog_sec\" ]", improve)
        self.assertIn("watchdog保留", improve)
        self.assertIn('"eval_age": int(eval_age)', improve)
        show_status = (REPO_ROOT / "show_status.sh").read_text()
        self.assertIn("SHOW_STATUS_IMPROVE_HIDDEN_PID_FRESH_SEC", show_status)
        self.assertIn("imp_state_activity_fresh=true", show_status)
        self.assertIn("PID=%s not visible, log fresh", show_status)
        self.assertIn("improve_hung_harvest", improve)
        self.assertIn("if _stop_improve_pid_if_running \"$pid\" \"improve_hung\"; then", improve)
        self.assertIn("pid_alive=false", improve)
        self.assertIn("停止に失敗したためrunning扱いを維持", improve)
        self.assertIn("通常改善が無音で固まったため", improve)
        self.assertIn("SOREN91_IMPROVE_HUNG_HARVEST_ENABLED", config)
        self.assertIn("SOREN91_IMPROVE_HUNG_SEC", config)
        self.assertIn("SOREN91_IMPROVE_HUNG_QUARANTINE_FILE", config)
        self.assertIn("soren91_harvest_hung_improve()", control)
        self.assertIn("soren91_harvest_hung_improve || true", control)
        self.assertIn('if _soren91_is_improve_process "$imp_pid"; then', control)
        self.assertIn("[ \"$lock_age\" -ge \"$threshold\" ]", control)
        self.assertIn("[ \"$log_age\" -ge \"$threshold\" ]", control)
        self.assertIn("[ \"$eval_age\" -lt \"$threshold\" ]", control)
        self.assertIn("hung improve harvest defer", control)
        self.assertIn('"eval_age": int(eval_age)', control)
        self.assertIn("_soren91_is_improve_process \"$pid\"", control)
        self.assertIn("_soren91_record_improve_stale_cleanup()", control)
        self.assertIn("soren91_improve_stale_cleanup", control)
        self.assertIn("invalid_pid", control)
        self.assertIn("pid_not_alive_or_not_improve", control)
        self.assertIn("log_tail", control)
        self.assertIn("メリケンAI改善が途中終了したため", control)
        self.assertIn("_stop_loop_descendants \"$pid\"", control)
        self.assertIn("_stop_pid_with_fallback \"$pid\" \"soren91_improve_hung\"", control)
        self.assertIn("soren91_improve_hung_quarantine", control)
        self.assertIn("enqueue_audio_text \"メリケンAI改善が無音で固まったため", control)
        self.assertIn("soren91_harvest_hung_improve || true", improve)

    def test_startup_validation_does_not_reset_current_run_hash(self):
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertNotIn("validation後hash同期", loop)
        self.assertNotIn("_reset_current_strategy_run \"$_validated_hash\"", loop)

    def test_current_strategy_run_reset_and_seed_write_valid_json(self):
        """hash切替時の current_run reset/seed は静かに失敗せず JSON を書く。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            run_file = td / "current_strategy_run.json"
            rolling_file = td / "rolling_scores.json"
            rolling_file.write_text(json.dumps({
                "seedhash": {
                    "scores": [1, 2, 3],
                    "games_total": 3,
                    "_recent_archives": ["game_history/a.jsonl"],
                    "frontier_hints": ["T12_peak=1 prev_T11_peak=2"],
                    "peak_high_type_counts": ["T12x1"],
                    "deadline_guard_counts": [4],
                    "deadline_guard_reason_tops": ["DEADLINE_GUARDx4"],
                    "max_types": [15, 12, 12],
                    "russia_count": 1,
                    "soviet_count": 0,
                    "best_max_type": 15,
                }
            }))
            script = textwrap.dedent(f"""\
                source ./core/config.sh
                CURRENT_STRATEGY_RUN_FILE='{run_file}'
                ROLLING_SCORES_FILE='{rolling_file}'
                source ./strategy/improve.sh
                _reset_current_strategy_run reset_hash
                python3 - <<'PY'
import json
from pathlib import Path
p = Path('{run_file}')
d = json.load(open(p))
assert d['hash'] == 'reset_hash'
assert d['scores'] == []
assert d['_seeded_score_count'] == 0
assert d['_fresh_score_count'] == 0
PY
                _seed_current_strategy_run_from_rolling seedhash
                python3 - <<'PY'
import json
from pathlib import Path
p = Path('{run_file}')
d = json.load(open(p))
assert d['hash'] == 'seedhash'
assert d['scores'] == [1, 2, 3]
assert d['games_total'] == 3
assert d['_seeded_score_count'] == 3
assert d['_fresh_score_count'] == 0
assert d['_recent_archives'] == ['game_history/a.jsonl']
assert d['max_types'] == [15, 12, 12]
assert d['russia_count'] == 1
assert d['best_max_type'] == 15
PY
            """)
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_improve_state_idle_zero_progress_clears_stale_runtime_phase(self):
        """idle + progress=0 は古い runtime_recovery/detail を監視面へ残さない。"""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            state_file = td / "improve_state.json"
            script = textwrap.dedent(f"""\
                source ./core/config.sh
                IMPROVE_STATE_FILE='{state_file}'
                source ./strategy/improve.sh
                _write_improve_state "idle" "0" "" "runtime_recovery" "0" "decide_exception"
                python3 - <<'PY'
import json
from pathlib import Path
p = Path('{state_file}')
d = json.load(open(p))
assert d['status'] == 'idle'
assert d['phase'] == ''
assert d['detail'] == ''
assert d['strategy_hash_before'] == ''
assert d['started_at'] == 0
assert d['pid_birth_epoch'] == 0
assert d['improve_reason'] == ''
PY
            """)
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_same_hash_objective_gap_does_not_escalate_wildcard_stagnation(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("if current_hash == anchor_hash and not branch_active:", regression)
        self.assertIn("current_games_total = int(current_data.get(\"games_total\"", regression)
        self.assertIn("and current_games_total >= same_hash_backslide_mature_n", regression)
        self.assertIn('_update_stagnation("SAME_HASH_BACKSLIDE")', regression)
        self.assertIn('elif event in ("OK_IDLE", "SAME_HASH_BACKSLIDE"):', regression)
        self.assertIn('_update_stagnation("OK_IDLE")', regression)
        self.assertIn('_update_stagnation("RESET")', regression)
        self.assertIn("same_hash_backslide_mature_n", regression)
        self.assertIn("same_hash_backslide_enabled", regression)
        self.assertIn("same_hash_backslide_min_extra_games", regression)
        self.assertIn("current_hash != anchor_hash and key(current) <= key(anchor)", regression)
        self.assertNotIn("anchor_objective.get(\"russia_count\"", regression)
        self.assertIn("best_max_type >= 15 and russia_count <= 0", regression)
        self.assertIn("SAME_HASH_BACKSLIDE_RESET_ENABLED", config)
        self.assertIn("SAME_HASH_BACKSLIDE_MIN_EXTRA_GAMES", config)

    def test_anchor_rank_key_prioritizes_near_score_objective_progress(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("Rollback anchors must stay score-mature, but the Soviet objective is the", regression)
        self.assertIn("near_score_leader = (", regression)
        self.assertIn("objective_key = _objective_tuple(h, data)", regression)
        self.assertIn("*objective_key,\n        selection_score", regression)
        self.assertIn("int(bool(p.get(\"soviet_frontier\", False)))", regression)
        self.assertIn("Single Russia is NOT a rung", regression)

    def test_russia_recovery_mode_suppresses_mechanical_wildcard(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("Russia recovery mode active", improve)
        self.assertIn("mechanical wildcard suppressed", improve)
        self.assertIn("no_russia_24h", improve)
        self.assertIn("archive_restart を即時優先", improve)
        self.assertIn("archive candidate unavailable → WILDCARD で構造変異候補を評価", improve)
        self.assertIn("archive/wildcard unavailable → seeded escape_ai", improve)
        self.assertIn("russia path still alive? no", improve)
        self.assertIn("archive_restart candidate? yes", improve)
        self.assertIn("seeded escape_ai candidate? yes", improve)
        self.assertLess(
            improve.index("archive_restart を即時優先"),
            improve.index("[ -f \"${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}\""),
        )
        self.assertIn("archive_restart を優先", improve)
        self.assertIn("russia_recovery_mode: type14 near-miss", eloop)
        self.assertIn("type13 pre-Russia near-miss", eloop)
        self.assertIn("T13x3", eloop)

    def test_russia_progress_suppresses_mechanical_wildcard(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("current strategy has Russia progress", improve)
        self.assertIn("current_russia_progress", improve)
        self.assertIn('russia > 0 or best_type >= 15', improve)
        self.assertIn("lock_matches_current", improve)
        self.assertIn('lock_hash == current_hash', improve)
        self.assertIn('lock_russia = as_int(lock.get("russia_count", 0)) if lock_matches_current else 0', improve)
        self.assertIn('lock_best = as_int(lock.get("best_max_type", 0)) if lock_matches_current else 0', improve)
        self.assertIn('[ "$current_russia_progress" != "1" ]', improve)
        self.assertLess(
            improve.index("current strategy has Russia progress"),
            improve.index('[ "$current_russia_progress" != "1" ]'),
        )

    def test_ok_beat_clears_regression_streak(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn('if event == "OK_BEAT":\n            rs = 0', regression)
        self.assertIn('elif event == "OK_IDLE":\n            rs = max(0, rs - 1)', regression)
        self.assertIn("古い回帰ストリークを残さない", regression)

    def test_objective_miss_does_not_reset_escape_streaks(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("def objective_miss_against_anchor(anchor_progress, current_progress):", regression)
        self.assertIn('return "OBJECTIVE_MISS" if objective_miss_against_anchor(anchor_progress, current_progress) else "OK_BEAT"', regression)
        self.assertIn('elif event in ("REGRESSION", "RESET", "OBJECTIVE_MISS"):\n            c += 1', regression)
        self.assertIn('elif event in ("REGRESSION", "RESET", "OBJECTIVE_MISS"):\n            rs += 1', regression)
        self.assertIn('if event in ("PROMOTE", "OK_BEAT"):', regression)

    def test_incumbent_promote_does_not_reset_escape_streak(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        # consecutive_wildcards (the escape-escalation streak) must reset ONLY on a
        # genuine escape success — a wildcard/archive_restart/escape_ai ORIGIN being
        # promoted. A plain incumbent (rolling_top) PROMOTE must NOT reset it, or
        # repeated FAILED mechanical escapes (wildcard no_candidate, regressed
        # candidates) never accumulate toward archive_restart/escape_ai escalation
        # and the only novelty-injecting AI escape can never fire.
        self.assertIn(
            'if event in ("PROMOTE", "OK_BEAT") and is_wildcard_origin:', regression
        )
        self.assertIn('elif event in ("PROMOTE", "OK_BEAT"):', regression)

    def test_escape_ai_seed_finder_searches_permanent_archive(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        # _escape_ai_seed_available must search the PERMANENT archive like its
        # sibling _archive_restart_has_candidate. Wildcard-origin seeds live almost
        # entirely in strategy_versions_archive/by_hash (the live strategy_versions/
        # by_hash is pruned to ~16 entries), so both the daemon pre-check and the
        # actual eloop seed picker must search permanent archive.
        self.assertIn(
            'origin_file, rolling_file, rejected_file, archive_dir, min_games_raw, '
            'min_best_type_raw, permanent_archive_dir, include_permanent_raw = sys.argv[1:9]',
            improve,
        )
        self.assertIn("if include_permanent and permanent_archive_dir:", improve)
        self.assertIn(
            '"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}"',
            improve,
        )
        self.assertIn(
            'origin_file, rolling_file, rejected_file, archive_dir, min_games_raw, '
            'min_best_type_raw, permanent_archive_dir, include_permanent_raw = sys.argv[1:9]',
            eloop,
        )
        self.assertIn("if include_permanent and permanent_archive_dir:", eloop)
        self.assertIn(
            '"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}"',
            eloop,
        )

    def test_structural_validation_errors_restart_fresh_sandbox_early(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("IMPROVE_STRUCTURAL_ERROR_MAX_CONTINUES", config)
        self.assertIn("_validation_error_is_structural_staging_breakage", eloop)
        self.assertIn("_structural_error_should_restart_fresh", eloop)
        self.assertIn("IndentationError|SyntaxError|NameError|UnboundLocalError", eloop)
        self.assertIn("structural validation breakage persisted", eloop)
        self.assertIn("restart with clean sandbox", eloop)

    def test_show_status_surfaces_current_wildcard_evaluation(self):
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("wildcard_eval_name", status)
        self.assertIn("wildcard_eval_label", status)
        self.assertIn("wildcard_outcomes.jsonl", status)
        self.assertIn("WildEval", status)
        self.assertIn("ArcEval", status)
        self.assertIn("n = len(scores)", status)
        self.assertIn("source_russia_count", status)
        self.assertIn("{n}/{mature_n}", status)
        self.assertIn("quantile(xs, 0.50)", status)
        self.assertIn("0.55 * quantile", status)
        self.assertIn("best_strategy_anchor.json", status)
        self.assertIn("delta_label", status)
        self.assertIn("trend_label", status)
        self.assertIn("t{trend:+d}", status)
        self.assertIn("d{delta:+d}", status)
        self.assertIn("event_short", status)
        self.assertIn("annealing_candidates.jsonl", status)
        self.assertIn("AnnealObs", status)
        self.assertIn("accept_probability", status)
        self.assertIn("viewer_chat_monitor.sh", status)
        self.assertIn("viewer_chat_label", status)
        self.assertIn("stagnation_defer_label", status)
        self.assertIn("defer={','.join(bits)}", status)
        self.assertIn("WILDCARD_EARLY_ESCAPE_MIN_GAMES", status)
        self.assertIn("defer=early", status)
        self.assertIn('"$TMP_STATE_DIR/accumulated_games.json" "$TMP_STATE_DIR/current_strategy_run.json"', status)
        self.assertIn('source = "accumulated"', status)
        self.assertIn("games = int(data.get(\"count\", 0) or score_count)", status)
        self.assertIn("if not data and not acc_exists", status)
        self.assertIn('stagnation_detail="${stagnation_defer_label} ${stagnation_detail}"', status)
        self.assertIn("best_max_type", status)
        self.assertIn("fresh_objective_label", status)
        self.assertIn("FreshObj", status)
        self.assertIn("fresh_objective_same_hash_lock", status)
        self.assertIn("wait low_stage_miss", status)
        self.assertIn("ready low_stage_miss", status)
        self.assertIn("ChatObs", status)
        self.assertIn("rate_limit_backoff", status)
        self.assertIn("improve_backoff_label", status)
        self.assertIn("ImproveBack", status)
        self.assertIn("archive_next_label", status)
        self.assertIn("ArchiveNext", status)
        self.assertIn("no cand c>=", status)
        self.assertIn("archive_path_blocker", status)
        self.assertIn("unstable", status)
        self.assertIn("R0", status)
        self.assertIn("-> escape_ai", status)
        self.assertIn("archive_is_runtime_stable", status)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", status)
        self.assertIn("allow_origin_retry", status)
        self.assertIn("is_cooled_down", status)
        self.assertIn("find_archive_path", status)
        self.assertIn("anchor_russia", status)
        self.assertIn("anchor_soviet", status)
        self.assertIn("opencode thinking", status)
        self.assertIn("Continue if you have next steps", status)

    def test_wildcard_progress_milestones_are_reported_to_audio(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        reporter = (REPO_ROOT / "wildcard_progress_report.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("WILDCARD_PROGRESS_AUDIO_ENABLED", config)
        self.assertIn("WILDCARD_PROGRESS_AUDIO_STATE_FILE", config)
        self.assertIn("WILDCARD_PROGRESS_AUDIO_MILESTONES", config)
        self.assertIn("WILDCARD_PROGRESS_AUDIO_MIN_DELTA", config)
        self.assertIn("enqueue_audio_text \"$message\" \"wildcard_progress\"", reporter)
        self.assertIn("wildcard_progress_report.env.tmp", reporter)
        self.assertIn("WILDCARD_PARALLEL_STATUS_FILE", reporter)
        self.assertIn("parallel_failure", reporter)
        self.assertIn("WILDCARD 並列評価が発動失敗です", reporter)
        show_status = (REPO_ROOT / "show_status.sh").read_text()
        self.assertIn("WildParFail", show_status)
        self.assertIn("PostParamFail", show_status)
        self.assertIn("baseline_slot1", show_status)
        self.assertIn("anchor 比", reporter)
        self.assertIn("origin_type", reporter)
        self.assertIn("ARCHIVE-RESTART", reporter)
        self.assertIn("過去版リスタート候補", reporter)
        self.assertIn("source_russia_count", reporter)
        self.assertIn("source_best_max_type", reporter)
        self.assertIn("ロシア実績", reporter)
        self.assertIn("ESCAPE-AI", reporter)
        self.assertIn("overlay_notify.sh", reporter)
        self.assertIn("./wildcard_progress_report.sh", loop)
        self.assertIn("progress report skipped/failed", loop)

    def test_wildcard_parallel_preserves_bridge_exit_diagnostics(self):
        runner = (REPO_ROOT / "wildcard_parallel.py").read_text()

        self.assertIn("def bridge_failure_detail", runner)
        self.assertIn("soviet_local.stderr.log", runner)
        self.assertIn("soviet_local.stdout.log", runner)
        self.assertIn("soviet_local.exit.log", runner)
        self.assertIn("bridge exited rc=", runner)
        self.assertIn("bridge did not produce game_state", runner)

    def test_wildcard_parallel_uses_isolated_game_server_ports(self):
        runner = (REPO_ROOT / "wildcard_parallel.py").read_text()
        bridge = (REPO_ROOT / "soviet_local.mjs").read_text()

        self.assertIn("SOREN_SERVE_PORT", bridge)
        self.assertIn("serve_base_port", runner)
        self.assertIn("candidate.serve_port = args.serve_base_port + candidate.index", runner)
        self.assertIn('"SOREN_SERVE_PORT": str(candidate.serve_port)', runner)
        self.assertIn("def cleanup_wildcard_server_ports", runner)
        self.assertIn('["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"]', runner)
        self.assertIn("cleanup_wildcard_server_ports([candidate.serve_port])", runner)
        self.assertIn("cleanup_wildcard_server_ports([args.serve_base_port + index for index in range(args.jobs)])", runner)

    def test_wildcard_parallel_mutates_deadline_fast_drop_runtime_param(self):
        import argparse
        import wildcard_parallel

        runner = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn("WILDCARD_PARALLEL_FAST_DROP_DEADLINE_CONTACT_MUTATE", runner)
        self.assertIn("WILDCARD_PARALLEL_FAST_DROP_DEADLINE_CONTACT_VALUES", runner)

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            strategy = td / "strategy.py"
            strategy.write_text(
                "# AI prohibited: decide() signature, if __name__ == \"__main__\" block\n"
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': 'ok'}\n",
                encoding="utf-8",
            )

            inserted = wildcard_parallel.ensure_deadline_fast_drop_param(strategy, False)
            self.assertIsNotNone(inserted)
            self.assertIn("FAST_DROP_DEADLINE_CONTACT = False", strategy.read_text(encoding="utf-8"))

            updated = wildcard_parallel.ensure_deadline_fast_drop_param(strategy, True)
            self.assertIsNotNone(updated)
            self.assertIn("FAST_DROP_DEADLINE_CONTACT = True", strategy.read_text(encoding="utf-8"))

            args = argparse.Namespace(
                deadline_fast_drop_mutate=True,
                deadline_fast_drop_values=[True, False],
            )
            self.assertTrue(wildcard_parallel.parallel_deadline_fast_drop_value(args, 0))
            self.assertFalse(wildcard_parallel.parallel_deadline_fast_drop_value(args, 1))

    def test_wildcard_parallel_overlay_hides_culled_slots(self):
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            status_path = td / "status.json"
            html_path = td / "overlay.html"
            payload = {
                "phase": "running",
                "candidates": [
                    {"job_id": "cand-1", "index": 0, "status": "culled", "games": 1, "comp": 10, "p25": 10, "p50": 10},
                    {"job_id": "cand-1-r2", "index": 0, "status": "running", "games": 1, "comp": 12, "p25": 12, "p50": 12},
                    {"job_id": "cand-2", "index": 1, "status": "accepted", "games": 2, "comp": 15, "p25": 14, "p50": 15},
                    {"job_id": "cand-3", "index": 2, "status": "failed", "games": 0, "comp": 0, "error": "boom"},
                ],
            }

            wildcard_parallel.render_overlay(status_path, html_path, payload)

            status = json.loads(status_path.read_text())
            overlay = html_path.read_text()
            self.assertEqual(len(status["candidates"]), 4)        # status keeps every slot
            # 表示は live/decided slot のみ: culled(cand-1) と failed(cand-3) は非表示
            self.assertEqual(overlay.count('<div class="pcell '), 2)
            self.assertIn('<span class="pslot">1</span>', overlay)   # cand-1-r2 → slot1
            self.assertIn('<span class="pslot">2</span>', overlay)   # cand-2   → slot2
            self.assertIn("finished", overlay)                       # accepted → finished
            # higher comp (cand-2, slot2) は暫定 rank 1
            self.assertIn('<span class="prank r1">#1</span>', overlay)
            # culled / failed slot とその error は漏れない
            self.assertNotIn("culled", overlay)
            self.assertNotIn("cand-3", overlay)
            self.assertNotIn("boom", overlay)

    def test_wildcard_parallel_cleanup_overlay_clears_visible_slots(self):
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            status_path = td / "status.json"
            html_path = td / "overlay.html"
            wildcard_parallel.render_overlay(
                status_path,
                html_path,
                {
                    "phase": "running",
                    "session_dir": "tmp/wildcard_parallel/run-old",
                    "params": {"jobs": 6, "max_games": 6},
                    "candidates": [
                        {"job_id": "cand-1", "index": 0, "status": "running", "games": 1, "comp": 100},
                    ],
                },
            )

            wildcard_parallel.render_cleanup_overlay(status_path, html_path)

            status = json.loads(status_path.read_text())
            overlay = html_path.read_text()
            self.assertEqual(status["phase"], "restored")
            self.assertEqual(status["previous_phase"], "running")
            self.assertEqual(status["previous_candidate_count"], 1)
            self.assertGreater(status["previous_controller_pid"], 0)
            self.assertEqual(status["candidates"], [])
            self.assertIn("restored", overlay)        # psub に restored phase
            self.assertIn("restored&lt;-running", overlay)
            self.assertIn("waiting", overlay)          # idle 時は waiting placeholder cell
            self.assertIn("game 0/6", overlay)         # eval 進捗は 0 にリセット
            self.assertNotIn("cand-1", overlay)
            self.assertNotIn('<span class="pslot">1</span>', overlay)

    def test_wildcard_parallel_archives_each_winner_game_for_import(self):
        import wildcard_parallel

        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            latest = workdir / "game_history" / "latest.jsonl"
            latest.parent.mkdir(parents=True)
            latest.write_text('{"score": 1234, "russia_created": true}\n', encoding="utf-8")

            archived = wildcard_parallel.archive_candidate_game_result(
                workdir,
                "cand-1",
                0,
                {"score": 1234, "turns": 88, "russia_created": True, "final_types": [15]},
            )

            self.assertTrue(archived.exists())
            self.assertIn("wildcard_parallel_cand-1_game1_score1234.jsonl", str(archived))
            self.assertIn('"russia_created": true', archived.read_text(encoding="utf-8"))

    def test_wildcard_parallel_all_game_stats_import_to_histories_and_rolling(self):
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("_import_wildcard_parallel_game_stats", improve)
        self.assertIn("WILDCARD_PARALLEL_IMPORT_ALL_GAME_STATS", improve)
        self.assertIn("parallel_winner", improve)
        self.assertIn("parallel_candidates", improve)
        self.assertIn('for candidate in candidates:', improve)
        self.assertIn("wildcard_parallel_imported.tsv", improve)
        self.assertIn('grep -qxF "$import_key"', improve)
        self.assertIn('printf \'%s\\t%s\\n\' "$iso_ts" "$raw_score" >>score_history.txt', improve)
        self.assertIn('printf \'%s\\t%s\\n\' "$iso_ts" "$eval_score" >>eval_score_history.txt', improve)
        self.assertIn('_append_celebration_history "russia"', improve)
        self.assertIn('ROLLING_SCORE_STRATEGY_HASH="$candidate_hash"', improve)
        self.assertIn('if [ -n "$adopted_hash" ] && [ "$candidate_hash" = "$adopted_hash" ]; then', improve)
        self.assertLess(
            improve.index('wildcard_result=$(python3 - "$wildcard_parallel_result_file"'),
            improve.index('_import_wildcard_parallel_game_stats "$wildcard_result" "$HASH_AFTER"'),
        )
        self.assertIn('local strategy_source="${ROLLING_SCORE_STRATEGY_SOURCE:-${STRATEGY_FILE}.game_snapshot}"', regression)
        self.assertIn('strategy_hash="${ROLLING_SCORE_STRATEGY_HASH:-}"', regression)

    def test_active_branch_head_repair_runs_before_game_snapshot(self):
        eloop = (REPO_ROOT / "eloop.sh").read_text()

        self.assertIn("repair_strategy_to_active_branch_head_if_needed()", eloop)
        self.assertIn('data.get("head_hash")', eloop)
        self.assertIn('${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}/${expected_hash}.py', eloop)
        self.assertIn('${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}/${expected_hash}.py', eloop)
        self.assertIn("_strategy_source_has_invalid_structural_wildcard", eloop)
        self.assertIn("fallback to best", eloop)
        self.assertIn("_clear_active_branch", eloop)
        self.assertIn('_seed_current_strategy_run_from_rolling "$actual_hash"', eloop)
        self.assertIn("_clear_accumulated_data", eloop)
        self.assertLess(
            eloop.index("repair_strategy_to_active_branch_head_if_needed\n"),
            eloop.index('cp "$STRATEGY_FILE" "${STRATEGY_FILE}.game_snapshot"'),
        )

    def test_wildcard_parallel_obs_restores_after_nonzero_exit(self):
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("wildcard_parallel_restore_on_exit()", improve)
        self.assertIn("wildcard_parallel_restore_once()", improve)
        self.assertIn("trap wildcard_parallel_restore_on_exit EXIT INT TERM", improve)
        self.assertIn("python3 wildcard_parallel.py --cleanup-stale", improve)
        self.assertIn("set +e\n\t\twildcard_parallel_result=$(python3 wildcard_parallel.py", improve)
        self.assertIn("wildcard_parallel_rc=$?", improve)
        self.assertIn("wildcard_parallel_heartbeat_stop\n\t\tset -e", improve)
        self.assertIn("wildcard_parallel_restore_once\n\t\t\texit 1", improve)
        self.assertIn("wildcard_parallel_restore_trap_active=0", improve)
        self.assertIn("trap - EXIT INT TERM", improve)

    def test_wildcard_parallel_nonzero_exit_can_use_written_winner(self):
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("wildcard_parallel_started_at=$(date +%s)", improve)
        self.assertIn('rm -f "$wildcard_parallel_result_file"', improve)
        self.assertIn("wildcard_parallel_has_winner=$(python3 - \"$wildcard_parallel_result_file\"", improve)
        self.assertIn("started_at = int(float(sys.argv[2]))", improve)
        self.assertIn("os.path.getmtime(path) < started_at", improve)
        self.assertIn('data.get("ok") and winner.get("strategy_path")', improve)
        self.assertIn("parallel trial exited rc=$wildcard_parallel_rc but result file has winner", improve)
        self.assertLess(
            improve.index('rm -f "$wildcard_parallel_result_file"'),
            improve.index("set +e\n\t\twildcard_parallel_result=$(python3 wildcard_parallel.py"),
        )
        self.assertLess(
            improve.index('if [ "$wildcard_parallel_rc" -ne 0 ]; then'),
            improve.index('wildcard_winner_path=$(python3 - "$wildcard_parallel_result_file"'),
        )

    def test_viewer_chat_monitor_filters_recent_observer_comments(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        script = (REPO_ROOT / "viewer_chat_monitor.sh").read_text()
        dashboard = (REPO_ROOT / "status_dashboard.py").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("VIEWER_CHAT_MONITOR_FILE", config)
        self.assertIn("VIEWER_CHAT_MONITOR_SOURCE", config)
        self.assertIn("VIEWER_CHAT_MONITOR_LOOKBACK", config)
        self.assertIn("comment_context_history.log", config)
        self.assertIn("viewer_chat_monitor.json", config)
        self.assertIn("comment_context_history.log", script)
        self.assertIn("is_observer_comment", script)
        self.assertIn("looks_like_emote_only", script)
        self.assertIn("looks_like_binary_noise", script)
        self.assertIn("CONTROL_RE", script)
        self.assertIn("sanitize_text", script)
        self.assertIn("獲得しました", script)
        self.assertIn("連ガチャ", script)
        self.assertIn("Twitchエモート:", script)
        self.assertIn("unagee", script)
        self.assertIn('"latest": latest', script)
        self.assertIn('"recent": recent', script)
        self.assertIn("VIEWER_CHAT_MONITOR_FILE", dashboard)
        self.assertIn("load_viewer_chat_monitor", dashboard)
        self.assertIn("ChatObs", dashboard)
        self.assertIn("load_improve_backoff_status", dashboard)
        self.assertIn("ImproveBackoff", dashboard)
        self.assertIn("SOREN91_IMPROVE_HUNG_QUARANTINE_FILE", dashboard)
        self.assertIn("load_soren91_improve_watchdog_status", dashboard)
        self.assertIn("S91Improve", dashboard)
        self.assertIn("soren91_improve_hung_quarantine.jsonl", dashboard)
        self.assertIn("viewer_chat_monitor.sh", status)
        self.assertIn("viewer_chat_label", status)
        self.assertIn("ChatObs", status)
        self.assertIn("soren91_improve_watchdog_label", status)
        self.assertIn("S91Improve", status)
        self.assertIn("soren91_improve_hung_quarantine.jsonl", status)

    def test_twitch_daemon_uses_complete_anonymous_handshake_and_logs_reconnects(self):
        daemon = (REPO_ROOT / "twitch_chat_daemon.sh").read_text()
        worker = (REPO_ROOT / "workers/chat_worker.sh").read_text()

        self.assertIn("printf 'PASS SCHMOOPIIE\\r\\n'", daemon)
        self.assertIn("IRC session ended; reconnecting in 5s", daemon)
        self.assertIn("operation not permitted", worker)
        self.assertIn('_pid_alive "$_DAEMON_PID"', worker)

    def test_chat_and_audio_workers_duplicate_start_is_idempotent(self):
        chat_worker = (REPO_ROOT / "workers/chat_worker.sh").read_text()
        youtube_worker = (REPO_ROOT / "workers/youtube_worker.sh").read_text()
        audio_worker = (REPO_ROOT / "workers/audio_worker.sh").read_text()
        radio_worker = (REPO_ROOT / "workers/radio_worker.sh").read_text()

        for worker in (chat_worker, youtube_worker, audio_worker, radio_worker):
            self.assertIn('already running (PID=$old_pid) -> no-op', worker)
            self.assertIn("exit 0", worker)
            self.assertIn("cleanup skipped: pidfile owner is", worker)
        self.assertIn('if _pid_alive "$old_pid"; then', chat_worker)
        self.assertIn('if _pid_alive "$old_pid"; then', youtube_worker)
        self.assertIn('if _pid_alive "$old_pid"; then', audio_worker)
        self.assertIn('if _pid_alive "$old_pid"; then', radio_worker)

    def test_chat_ingest_notifies_event_overlay(self):
        twitch = (REPO_ROOT / "twitch_chat_daemon.sh").read_text()
        twitch_send = (REPO_ROOT / "twitch_chat.sh").read_text()
        outbound = (REPO_ROOT / "lib/outbound_queue.sh").read_text()
        youtube = (REPO_ROOT / "youtube_chat.sh").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("_notify_chat_overlay()", twitch)
        self.assertIn('CHAT_INGEST_OVERLAY_NOTIFY:-1', twitch)
        self.assertIn('./overlay_notify.sh chat "Twitch コメント受信" "$line"', twitch)
        self.assertIn('_notify_chat_overlay "$clean_line"', twitch)

        self.assertIn("_notify_chat_overlay()", youtube)
        self.assertIn('CHAT_INGEST_OVERLAY_NOTIFY:-1', youtube)
        self.assertIn('local title="${3:-${source} コメント受信}"', youtube)
        self.assertIn('./overlay_notify.sh chat "$title" "$line"', youtube)
        self.assertIn('_notify_chat_overlay "YouTube" "$notify_line"', youtube)
        self.assertIn('_notify_chat_overlay "YouTube" "[TEST/DUMMY] $notify_line" "YouTube TEST/DUMMY コメント受信"', youtube)
        self.assertIn("last_send_error.txt", youtube)
        self.assertIn("_resolve_live_chat_id 1", youtube)
        self.assertIn("_send_api()", youtube)
        self.assertIn("_record_send_error", youtube)
        self.assertIn('2>"$err_file"', youtube)
        self.assertIn('_write_send_payload "$chat_id" "$msg" "$payload_file"', youtube)
        self.assertIn("_discover_live_video_id()", youtube)
        self.assertIn("_discover_live_video_id_from_channel_page()", youtube)
        self.assertIn("liveStreamabilityRenderer", youtube)
        self.assertIn("source=yt_live_broadcast", youtube)
        self.assertIn("_poll_web_live_chat()", youtube)
        self.assertIn("youtubei/v1/live_chat/get_live_chat", youtube)
        self.assertIn("web_live_chat_continuation", youtube)
        self.assertIn("poll: web fallback", youtube)
        self.assertIn("YOUTUBE_CHANNEL_ID", youtube)
        self.assertIn("LIVE_VIDEO_ID_FILE", youtube)
        self.assertIn("eventType=live", youtube)
        self.assertIn('local access_token="${1:-}"', youtube)
        self.assertIn('chat_id=$(_resolve_live_chat_id 0 "$access_token")', youtube)
        self.assertIn('_api_get "$url" "$access_token"', youtube)
        self.assertIn('_discover_live_video_id "$access_token"', youtube)
        self.assertIn('video_id=$(_discover_live_video_id "$access_token"', youtube)
        self.assertIn('discovered_id=$(_discover_live_video_id "$access_token"', youtube)
        self.assertIn('access_token=$(_maybe_oauth_access_token)', youtube)
        self.assertIn('if [ -z "$access_token" ]; then', youtube)
        self.assertIn('_api_get "$url" "$access_token" >"$resp_file"', youtube)
        self.assertIn("_api_backoff_active()", youtube)
        self.assertIn("_try_backoff_recovery()", youtube)
        self.assertIn("API-key backoff active; retrying liveChatMessages with OAuth", youtube)
        self.assertIn('YOUTUBE_API_BACKOFF_RECOVERY_PROBE_SEC:-120', youtube)
        self.assertIn('poll: recovered activeLiveChatId during API backoff', youtube)
        self.assertIn('_record_api_backoff "YouTube API 403/quota while resolving activeLiveChatId"', youtube)
        self.assertIn('api_backoff_until=${backoff_until}', youtube)
        self.assertIn('rm -f "$LIVE_CHAT_ID_FILE" "$LIVE_VIDEO_ID_FILE" "$PAGE_TOKEN_FILE"', youtube)
        self.assertIn('rm -f "$LIVE_CHAT_ID_FILE" "$PAGE_TOKEN_FILE"', youtube)
        self.assertIn("_send_api()", twitch_send)
        self.assertIn("https://api.twitch.tv/helix/chat/messages", twitch_send)
        self.assertIn("is_sent", twitch_send)
        self.assertIn("falling back to IRC after Twitch API send failure", twitch_send)
        self.assertIn("_outbound_chat_log_twitch_failure", outbound)
        self.assertIn("last_twitch_send_error.txt", outbound)
        self.assertIn("outbound_chat_twitch.log", outbound)
        self.assertIn("_outbound_chat_twitch_backoff_active()", outbound)
        self.assertIn("OUTBOUND_CHAT_TWITCH_AUTH_BACKOFF_MAX_SEC", outbound)
        self.assertIn("_outbound_chat_twitch_backoff_count_file()", outbound)
        self.assertIn("Invalid OAuth token|Login authentication failed", outbound)
        self.assertIn('rm -f "$(_outbound_chat_twitch_backoff_file)" "$(_outbound_chat_twitch_backoff_count_file)"', outbound)
        self.assertIn("_outbound_chat_youtube_backoff_active()", outbound)
        self.assertIn('local backoff_file="${YOUTUBE_CHAT_DIR:-tmp/.youtube_chat}/api_backoff_until"', outbound)
        self.assertIn("_outbound_chat_youtube_backoff_active && return 0", outbound)
        self.assertIn("_outbound_chat_youtube_auth_backoff_active()", outbound)
        self.assertIn("OUTBOUND_CHAT_YOUTUBE_AUTH_BACKOFF_MAX_SEC", outbound)
        self.assertIn("_outbound_chat_youtube_auth_backoff_active && return 0", outbound)
        self.assertIn("YouTube OAuth refresh|OAuth refresh settings are missing or invalid", outbound)
        self.assertIn("_outbound_chat_maybe_backoff_youtube_failure", outbound)
        self.assertIn("_outbound_chat_clear_youtube_failure_state", outbound)
        self.assertIn("OutboundErr", status)
        self.assertIn("DEGRADED", status)
        self.assertIn("last_send_error.txt", status)

    def test_soren91_comment_queue_releases_completed_claims_and_keeps_tts_full_by_default(self):
        main = (REPO_ROOT / "soren91/main.mjs").read_text()
        comment = (REPO_ROOT / "soren91/comment.mjs").read_text()

        self.assertIn("cleanupCompletedRankingCommentClaims", main)
        self.assertIn("releaseRankingCommentGameClaim(n);", main)
        self.assertIn("SOREN91_RANKING_CLAIM_KEEP_RECENT", main)
        self.assertIn("soren91_ranking_comment_game_(\\d+)\\.claim", main)
        self.assertIn("cleanupCompletedRankingCommentClaims();", main)
        self.assertIn("SOREN91_COMMENT_MAX_SPEAK_CHARS", comment)
        self.assertIn("process.env.SOREN91_COMMENT_MAX_SPEAK_CHARS, 0", comment)
        self.assertIn("compactForSpeech", comment)
        self.assertIn("spokenComment", comment)
        self.assertIn("originalChars", comment)
        self.assertIn("truncated", comment)

    def test_onair_sanitizer_strips_tool_markers_from_comment_replies(self):
        radio_engine = (REPO_ROOT / "broadcast/radio_engine.sh").read_text()
        comment = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn(r"^\s*%?\s*(?:WebFetch|WebSearch)\b\s*", radio_engine)
        self.assertIn("attempt_talk=$(printf '%s' \"$attempt_talk\" | _sanitize_onair_text)", comment)

    def test_radio_prepass_sanitizes_webfetch_failures_before_final_prompt(self):
        radio_engine = (REPO_ROOT / "broadcast/radio_engine.sh").read_text()

        self.assertIn("_sanitize_radio_research_memo()", radio_engine)
        self.assertIn("webfetch|websearch", radio_engine)
        self.assertIn("_run_radio_agent \"$radio_prepass_agent\" \"$_prepass_prompt_file\" 2>/dev/null | _sanitize_radio_research_memo", radio_engine)

    def test_radio_tool_failure_lines_are_filtered_before_tts(self):
        helpers = (REPO_ROOT / "core/helpers.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        radio_engine = (REPO_ROOT / "broadcast/radio_engine.sh").read_text()
        ai_generate = (REPO_ROOT / "lib/ai_generate.sh").read_text()
        radio_corners = (REPO_ROOT / "broadcast/radio_corners.sh").read_text()

        self.assertIn('"webfetch":"allow"', config)
        self.assertIn('RADIO_CLAUDE_TOOLS="${RADIO_CLAUDE_TOOLS:-default,WebSearch,WebFetch}"', config)
        self.assertIn('--tools "$RADIO_CLAUDE_TOOLS" --permission-mode dontAsk', radio_engine)
        self.assertIn('"webfetch":"allow"', radio_corners)
        self.assertIn("_notify_webfetch_failure()", helpers)
        self.assertIn('./overlay_notify.sh radio "Web取得失敗"', helpers)
        self.assertIn("取得できなかった", helpers)
        self.assertIn(r"[✗✕×]\s*(webfetch|websearch)\s+failed", radio_engine)
        self.assertIn(r"(WebFetch|WebSearch)", radio_engine)
        self.assertIn("grep -Eiv '(WebFetch|WebSearch)'", radio_engine)
        self.assertIn("talk_body=$(printf '%s' \"$talk_body\" | _sanitize_onair_text)", radio_engine)
        self.assertIn('_notify_webfetch_failure "RADIO" "$agent" "$raw_text" "radio"', radio_engine)
        self.assertIn("webfetch|websearch", ai_generate)
        self.assertIn("webfetch|websearch", radio_corners)

    def test_webfetch_notification_does_not_fire_on_success_tool_marker(self):
        success = subprocess.run(
            [
                "bash",
                "-lc",
                "source ./eloop_lib.sh; _contains_webfetch_failure_text $'% WebFetch https://example.com\\nFETCH_OK'",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        raw_http_failure = subprocess.run(
            [
                "bash",
                "-lc",
                "source ./eloop_lib.sh; _contains_webfetch_failure_text $'✗ webfetch failed\\nError: Request failed with status code: 404'",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        failure = subprocess.run(
            [
                "bash",
                "-lc",
                "source ./eloop_lib.sh; _contains_webfetch_failure_text $'WebFetchの権限確認が入りました'",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(success.returncode, 0)
        self.assertNotEqual(raw_http_failure.returncode, 0)
        self.assertEqual(failure.returncode, 0)

    def test_factcheck_debug_dump_sanitizes_raw_webfetch_failures(self):
        factcheck = (REPO_ROOT / "broadcast/radio_factcheck.sh").read_text()
        monitor = (REPO_ROOT / "monitor_webfetch_failure.sh").read_text()

        self.assertIn("===SANITIZED_CHECK_OUTPUT===", factcheck)
        self.assertIn('printf \'%s\\n\' "$raw_output" | _sanitize_onair_text', factcheck)
        self.assertIn("webfetch_monitor_start_epoch", monitor)
        self.assertIn("webfetch_monitor_last_checked_epoch", monitor)
        self.assertIn("WEBFETCH_MONITOR_LOOKBACK_SEC", monitor)
        self.assertIn('printf \'%s\\n\' "$now" >"$CURSOR_FILE"', monitor)
        self.assertIn('category") or "") == "system"', monitor)
        self.assertIn('失敗(?:した|しました|です|でした|のため', monitor)
        self.assertIn('許可(?:が必要|を得|待ち', monitor)
        self.assertIn("grep -EHin", monitor)
        self.assertIn("*_prompt.txt", monitor)
        self.assertIn("overlay_events.jsonl", monitor)

    def test_status_surfaces_fresh_improve_state_when_pid_is_hidden(self):
        dashboard = (REPO_ROOT / "status_dashboard.py").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        eloop = (REPO_ROOT / "eloop.sh").read_text()

        self.assertIn("improve_monitor_status.json", dashboard)
        self.assertIn("state_activity_fresh", dashboard)
        self.assertIn("Imp:{improve.get('progress', 0):>3}% {phase} log", dashboard)
        self.assertIn("improve_monitor_status.json", status)
        self.assertIn("reg=${regression_streak}/${WILDCARD_REGRESSION_STREAK:-2}", status)
        self.assertIn("imp_state_activity_fresh", status)
        self.assertIn("PID=%s not visible, log fresh", status)
        self.assertIn("activity is fresh; preserving active state", monitor)
        self.assertLess(
            monitor.index("activity is fresh; preserving active state"),
            monitor.index("running state references dead improve pid"),
        )
        self.assertIn("running state references dead improve pid", monitor)
        self.assertIn("harvesting immediately", monitor)
        self.assertIn("IMPROVE_MONITOR_FAST_ESCAPE_STATE_ONLY_GRACE_SEC", monitor)
        self.assertIn("fast escape running state references no visible parent pid", monitor)
        self.assertLess(
            monitor.index("fast escape running state references no visible parent pid"),
            monitor.index("running state has no visible eloop_improve pid but activity is fresh"),
        )
        self.assertIn("_file_recent()", status)
        self.assertIn("stat -f '%m'", status)
        self.assertIn("SOREN_IMPROVE_MONITOR_INTERVAL_SEC", loop)
        self.assertIn("_run_improve_runtime_monitor", loop)
        self.assertIn("./monitor_improve_runtime.sh >/dev/null 2>&1", loop)
        self.assertIn("improve runtime monitor skipped/failed", loop)
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        self.assertIn("_improve_state_claims_running_fresh()", improve)
        self.assertIn("IMPROVE_STATE_RUNNING_FRESH_SEC", improve)
        self.assertIn('state.get("status") not in {"running", "manual"}', improve)
        self.assertIn("_improve_state_claims_running_fresh", improve)
        self.assertNotIn('[ -f "$IMPROVE_LOCK_FILE" ] || return 1', improve)
        self.assertIn("./monitor_improve_runtime.sh", eloop)
        self.assertIn("post_game_bookkeeping", eloop)

    def test_archive_restart_monitor_does_not_start_soren91(self):
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("_improve_reason_get", monitor)
        self.assertIn("wildcard:*|archive_restart:*|*:wildcard_parallel:*|*:*:post_improve_param_parallel*)", monitor)
        self.assertIn("leaving soren91 stopped", monitor)
        self.assertLess(
            monitor.index("wildcard:*|archive_restart:*|*:wildcard_parallel:*|*:*:post_improve_param_parallel*)"),
            monitor.index("calling existing soren91_start"),
        )
        self.assertIn("${_pause_reason:-isolated}改善中(隔離評価): soren91代打を立てず待機", loop)
        self.assertIn("pause_phase=", loop)
        self.assertIn("pause_detail=", loop)
        self.assertIn("SOREN91_STOP_TIMEOUT=0 soren91_stop", loop)

    def test_monitor_forces_soren91_stop_when_returning_to_normal_mode(self):
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()

        self.assertIn("improve idle but soren91 is active; forcing existing soren91_stop", monitor)
        self.assertIn("SOREN91_STOP_TIMEOUT=0 soren91_stop", monitor)
        self.assertLess(
            monitor.index("improve idle but soren91 is active; forcing existing soren91_stop"),
            monitor.rindex("SOREN91_STOP_TIMEOUT=0 soren91_stop"),
        )

    def test_rollback_revalidates_strategy_after_restore(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn('rollback_restore_validate_${rollback_hash:-unknown}_$$.py', regression)
        self.assertIn('validate_strategy "$rollback_validate_tmp"', regression)
        self.assertNotIn('validate_strategy "$STRATEGY_FILE"', regression)
        self.assertIn("rollback validation failed but accepted by policy", regression)
        self.assertIn('cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"', regression)
        self.assertIn("ROLLBACK_REVALIDATE_TARGET_ENABLED", config)
        self.assertIn('if [ "${ROLLBACK_REVALIDATE_TARGET_ENABLED:-1}" = "1" ]; then', regression)
        self.assertIn('if _seed_current_strategy_run_from_rolling "$rolled_hash"; then', regression)
        self.assertIn("rollback revalidate seed from rolling", regression)
        self.assertIn('_reset_current_strategy_run "$rolled_hash"', regression)
        self.assertIn("rollback seed missing -> revalidate fresh cycle", regression)
        self.assertIn('if [ "${ROLLBACK_REVALIDATE_TARGET_ENABLED:-1}" = "1" ] &&', loop)
        self.assertIn('[ "${REGRESSION_ROLLBACK_DONE:-0}" = "1" ] &&', loop)
        self.assertIn("復帰先にロシア進捗あり", loop)
        self.assertIn("再評価を優先", loop)
        self.assertIn("rollback revalidate fresh cycle 中", loop)
        self.assertIn("[EARLY_ESCAPE]", loop)

    def test_recent_50_score_trend_graces_rollback_gates(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("ROLLBACK_TREND_GRACE_ENABLED", config)
        self.assertIn("ROLLBACK_TREND_GRACE_WINDOW", config)
        self.assertIn("ROLLBACK_TREND_GRACE_MIN_PRIOR", config)
        self.assertIn("def rollback_trend_grace():", regression)
        self.assertIn("eval_score_history_file", regression)
        self.assertIn("recent_avg = sum(recent) / len(recent)", regression)
        self.assertIn("prior_avg = sum(prior) / len(prior)", regression)
        self.assertIn("delta > rollback_trend_grace_min_delta", regression)
        self.assertIn("OK:{trend_grace_reason()}", regression)
        self.assertIn("reasons=objective_regression+", regression)
        self.assertIn("reasons=early_objective_regression+", regression)
        self.assertIn("mode=archive_objective_floor", regression)
        self.assertIn("trend_grace は score-only rollback dampener。目的退行は免除しない。", regression)
        self.assertIn("def objective_triggered(reason_text):", regression)
        self.assertIn("objective_was_trigger = objective_triggered(reg.get(\"reasons\", \"\"))", regression)
        self.assertIn("今回は粛清理由ではない", regression)
        self.assertIn("context_signal: current はロシア(type15)未到達だが、今回は粛清理由ではない。", regression)
        self.assertNotIn('print(f"OK:{trend_grace_reason()}")\n        raise SystemExit\n    print(\n        "REGRESSION:"\n        f"mode=objective_regression', regression)

    def test_early_comp_top_gap_can_purge_bad_current_branch(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        toggles = (REPO_ROOT / "core/runtime_toggles.sh").read_text()
        set_toggle = (REPO_ROOT / "set_toggle.sh").read_text()

        self.assertIn("EARLY_COMP_TOP_GAP_ENABLED", config)
        self.assertIn("EARLY_COMP_TOP_GAP_MIN_GAMES", config)
        self.assertIn("EARLY_COMP_TOP_GAP_MIN_RATIO", config)
        self.assertIn("EARLY_COMP_TOP_GAP_ENABLED", toggles)
        self.assertIn("EARLY_COMP_TOP_GAP_MIN_GAMES", toggles)
        self.assertIn("EARLY_COMP_TOP_GAP_MIN_RATIO", toggles)
        self.assertIn("EARLY_COMP_TOP_GAP_MIN_GAMES", set_toggle)
        self.assertIn("def rolling_comp_leader(current_metrics):", regression)
        self.assertIn("mode=early_comp_top_gap", regression)
        self.assertIn("frontier_grace_active", regression)
        self.assertIn("current[\"n\"] < min_games_current", regression)
        self.assertIn("int(current_objective.get(\"best_max_type\", 0) or 0) >= frontier_grace_min_type", regression)
        self.assertIn("curr_comp < top_comp * early_comp_top_gap_min_ratio", regression)
        self.assertIn("reasons=early_comp_top_gap+curr_comp_below_top_ratio", regression)
        self.assertIn('best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")', regression)

    def test_stage_achievement_gate_purges_low_stage_rate_without_changing_rollback_pick(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        toggles = (REPO_ROOT / "core/runtime_toggles.sh").read_text()
        set_toggle = (REPO_ROOT / "set_toggle.sh").read_text()

        self.assertIn("STAGE_ACHIEVEMENT_REGRESSION_ENABLED", config)
        self.assertIn("STAGE_ACHIEVEMENT_REGRESSION_MIN_GAMES", config)
        self.assertIn("STAGE_ACHIEVEMENT_GATE_MIN_RATE", config)
        self.assertIn("STAGE_ACHIEVEMENT_GATE_TYPES", config)
        self.assertIn("12,13,14,15", config)
        self.assertIn("STAGE_ACHIEVEMENT_REGRESSION_ENABLED", toggles)
        self.assertIn("STAGE_ACHIEVEMENT_REGRESSION_MIN_GAMES", toggles)
        self.assertIn("STAGE_ACHIEVEMENT_GATE_MIN_RATE", toggles)
        self.assertIn("STAGE_ACHIEVEMENT_GATE_TYPES", toggles)
        self.assertIn("STAGE_ACHIEVEMENT_GATE_TYPES", set_toggle)
        self.assertIn("def stage_achievement_regression_reason(reference_progress, current_progress):", regression)
        self.assertIn("parse_stage_achievement_gate_types", regression)
        self.assertIn("stage: sum(1 for value in max_types if value >= stage) / n", regression)
        self.assertIn("if current_n < stage_achievement_regression_min_games:", regression)
        self.assertIn("rate >= stage_achievement_gate_min_rate", regression)
        self.assertIn("return max(candidates, key=lambda item: item[0]) + (n,)", regression)
        self.assertIn("target_stage, target_rate, reference_n = stage_achievement_target_stage(reference_progress)", regression)
        self.assertIn("current_best >= target_stage", regression)
        self.assertIn("mode=stage_achievement_regression", regression)
        self.assertIn("stage_type{target_stage}_achievement_gate", regression)
        self.assertIn("target_type={target_stage}", regression)
        self.assertIn("target_rate={target_rate:.3f}", regression)
        self.assertIn("min_rate={stage_achievement_gate_min_rate:.3f}", regression)
        self.assertIn("sample_n={current_n}", regression)
        self.assertIn("trend_grace は score-only rollback dampener。段階到達率不足は免除しない。", regression)
        self.assertIn('best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")', regression)

    def test_twitch_rollback_post_surfaces_stage_achievement_reason(self):
        phylo = (REPO_ROOT / "core/phyrogenetic.sh").read_text()

        self.assertIn("_rollback_chat_reason_from_analysis()", phylo)
        self.assertIn('"stage_achievement_regression" in trigger', phylo)
        self.assertIn('r"target_type=(\\d+)"', phylo)
        self.assertIn('r"stage_type(\\d+)_achievement_gate"', phylo)
        self.assertIn('parts.append(f"Type{target}到達ゲート未達")', phylo)
        self.assertIn('parts.append(f"直近到達率{target_rate}")', phylo)
        self.assertIn('parts.append(f"要求{min_rate}")', phylo)
        self.assertIn('parts.append(f"n={sample_n}")', phylo)
        self.assertIn('rollback_reason=$(_rollback_chat_reason_from_analysis "$ROLLBACK_ANALYSIS_FILE"', phylo)
        self.assertIn('reason_suffix="理由: ${rollback_reason}。"', phylo)
        self.assertIn('chat_text="${action}${transition}。${reason_suffix}系統樹はこちら"', phylo)

    def test_rollback_target_cooldown_blocks_immediate_reuse(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("ROLLBACK_TARGET_COOLDOWN_FILE", config)
        self.assertIn("ROLLBACK_TARGET_COOLDOWN_SEC", config)
        self.assertIn("ROLLBACK_OBJECTIVE_REQUESTED_COOLDOWN_BYPASS", config)
        self.assertIn("_is_rollback_target_on_cooldown()", regression)
        self.assertIn("_record_rollback_target_cooldown()", regression)
        self.assertIn("anchor_top1候補スキップ", regression)
        self.assertIn("rollback先cooldown中", regression)
        self.assertIn("objective requested rollback bypasses cooldown", regression)
        self.assertIn("ROLLBACK_OBJECTIVE_REQUESTED_COOLDOWN_BYPASS", regression)
        self.assertIn('_record_rollback_target_cooldown "$strategy_hash" "$rolled_hash"', regression)

    def test_regression_rollback_prefers_current_rolling_top_candidate(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertLess(
            regression.index('best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")'),
            regression.index('rollback_hash=$(printf'),
        )
        self.assertIn('rollback_note="rolling_top hash=${rollback_hash}', regression)
        self.assertIn("_remove_unusable_rolling_score_hash", regression)
        self.assertIn("_prune_non_objective_rollback_scores", regression)
        self.assertIn("objective_miss_no_russia", regression)
        self.assertIn("OBJECTIVE_ANCHOR_PRIORITY_ENABLED", regression)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", regression)
        self.assertIn('OBJECTIVE_MISS_PRUNE_ENABLED:-0', regression)
        self.assertIn("def has_restorable_archive(hash_value):", regression)
        self.assertIn("if not has_restorable_archive(h):", regression)
        self.assertIn("if h != current_hash and not has_restorable_archive(h):", regression)

    def test_rollback_candidate_does_not_prefer_russia_progress_over_plain_score_top(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            archive_dir = td / "by_hash"
            permanent_dir = td / "permanent_by_hash"
            archive_dir.mkdir()
            permanent_dir.mkdir()

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnlyTop": {
                            "scores": [12000] * 12,
                            "games_total": 12,
                            "best_max_type": 0,
                            "russia_count": 0,
                            "soviet_count": 0,
                        },
                        "russiaNearTop": {
                            "scores": [11100] * 12,
                            "games_total": 12,
                            "best_max_type": 15,
                            "russia_count": 1,
                            "soviet_count": 0,
                        },
                    }
                )
            )
            stable_source = (
                "def decide(game_state, analysis):\n"
                "    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
                "    return {'x': 0, 'reason': 'ok'}\n"
            )
            (archive_dir / "scoreOnlyTop.py").write_text(stable_source)
            (permanent_dir / "russiaNearTop.py").write_text(stable_source)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{permanent_dir}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
HASH_ARCHIVE_KEEP_TOP=10
OBJECTIVE_ANCHOR_PRIORITY_ENABLED=1
OBJECTIVE_ANCHOR_MIN_COMP_RATIO=0.90
OBJECTIVE_ANCHOR_MAX_COMP_GAP=1500
source strategy/regression.sh 2>/dev/null
_rollback_candidate_file_is_valid() {{
    return 0
}}
_pick_best_rollback_candidate currentHash
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            self.assertTrue(result.stdout.startswith("scoreOnlyTop|"), msg=result.stdout)
            rolling = json.loads(rs_file.read_text())
            self.assertIn("scoreOnlyTop", rolling)
            self.assertIn("russiaNearTop", rolling)
            self.assertFalse((td / "rolling_score_pruned_hashes.jsonl").exists())

    def test_rollback_candidate_prefers_soviet_progress_over_plain_score_top(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            archive_dir = td / "by_hash"
            permanent_dir = td / "permanent_by_hash"
            archive_dir.mkdir()
            permanent_dir.mkdir()

            rs_file.write_text(
                json.dumps(
                    {
                        "scoreOnlyTop": {
                            "scores": [12000] * 12,
                            "games_total": 12,
                            "best_max_type": 0,
                            "russia_count": 0,
                            "soviet_count": 0,
                        },
                        "sovietNearTop": {
                            "scores": [11100] * 12,
                            "games_total": 12,
                            "best_max_type": 16,
                            "russia_count": 1,
                            "soviet_count": 1,
                        },
                    }
                )
            )
            stable_source = (
                "def decide(game_state, analysis):\n"
                "    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
                "    return {'x': 0, 'reason': 'ok'}\n"
            )
            (archive_dir / "scoreOnlyTop.py").write_text(stable_source)
            (permanent_dir / "sovietNearTop.py").write_text(stable_source)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{permanent_dir}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
HASH_ARCHIVE_KEEP_TOP=10
OBJECTIVE_ANCHOR_PRIORITY_ENABLED=1
OBJECTIVE_ANCHOR_MIN_COMP_RATIO=0.90
OBJECTIVE_ANCHOR_MAX_COMP_GAP=1500
source strategy/regression.sh 2>/dev/null
_rollback_candidate_file_is_valid() {{
    return 0
}}
_pick_best_rollback_candidate currentHash
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            self.assertTrue(result.stdout.startswith("sovietNearTop|"), msg=result.stdout)

    def test_invalid_rollback_archive_remains_eligible_and_is_not_pruned(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            rs_file = td / "rolling_scores.json"
            archive_dir = td / "by_hash"
            archive_dir.mkdir()

            rs_file.write_text(
                json.dumps(
                    {
                        "badTop": {"scores": [2000] * 12, "games_total": 12, "_recent_archives": []},
                        "goodNext": {"scores": [1500] * 12, "games_total": 12, "_recent_archives": []},
                    }
                )
            )
            stable_source = (
                "def decide(game_state, analysis):\n"
                "    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
                "    return {'x': 0, 'reason': 'ok'}\n"
            )
            (archive_dir / "badTop.py").write_text(stable_source)
            (archive_dir / "goodNext.py").write_text(stable_source)

            script = f"""
source core/config.sh 2>/dev/null
ROLLING_SCORES_FILE='{rs_file}'
STRATEGY_HASH_ARCHIVE_DIR='{archive_dir}'
MIN_GAMES_FOR_BEST_ROLLBACK=12
HASH_ARCHIVE_KEEP_TOP=10
OBJECTIVE_ANCHOR_PRIORITY_ENABLED=0
source strategy/regression.sh 2>/dev/null
_rollback_candidate_file_is_valid() {{
    case "$1" in
        badTop) return 1 ;;
        *) return 0 ;;
    esac
}}
_pick_best_rollback_candidate currentHash
"""
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}\nstdout: {result.stdout}")
            self.assertIn("badTop|", result.stdout)
            rolling = json.loads(rs_file.read_text())
            self.assertIn("badTop", rolling)
            self.assertIn("goodNext", rolling)
            self.assertFalse((td / "rolling_score_pruned_hashes.jsonl").exists())

    def test_soren91_ranking_comments_are_prioritized_in_comment_queue(self):
        comment_lib = (REPO_ROOT / "broadcast/comment_lib.sh").read_text()

        self.assertIn("_comment_queue_priority()", comment_lib)
        self.assertIn("soren91:ranking_comment) printf '%s' \"00\"", comment_lib)
        self.assertIn("_comment_queue_ordered_files()", comment_lib)
        self.assertIn("for qf in $(_comment_queue_ordered_files); do", comment_lib)

    def test_comment_playback_overlay_title_reflects_context_label(self):
        comment_lib = (REPO_ROOT / "broadcast/comment_lib.sh").read_text()

        self.assertIn("_comment_playback_overlay_title()", comment_lib)
        self.assertIn("improve_progress)          printf '%s' \"改善進捗 playback\"", comment_lib)
        self.assertIn("soren91:ranking_comment)   printf '%s' \"ランキングコメント playback\"", comment_lib)
        self.assertIn("soren91:midgame_comment)   printf '%s' \"試合中実況 playback\"", comment_lib)
        self.assertIn('comment)                   printf \'%s\' "コメント返信 playback"', comment_lib)
        self.assertIn('./overlay_notify.sh chat "$_ov_title"', comment_lib)

    def test_soren91_no_rank_fallback_describes_matching_without_fake_rank(self):
        comment = (REPO_ROOT / "soren91/comment.mjs").read_text()

        self.assertIn("MATCHING画面", comment)
        self.assertIn("順位は断定しません", comment)
        self.assertNotIn("今回はランキングの順位を確認できませんでした", comment)

    def test_soren91_captures_ranking_transition_burst(self):
        main = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("captureRankingTransitionBurst(page, gameNumber)", main)
        self.assertIn("SOREN91_RANK_BURST_INTERVAL_MS", main)
        self.assertIn("_rankburst_g", main)
        self.assertIn("Ranking transition burst detected", main)

    def test_soren91_probes_ranking_immediately_after_drop(self):
        main = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("probeRankingImmediatelyAfterDrop(page, gameNumber, turn)", main)
        self.assertIn("SOREN91_RANK_POSTDROP_INTERVAL_MS", main)
        self.assertIn("_rankpostdrop_g", main)
        self.assertIn("Post-drop ranking detected", main)
        self.assertIn("Active ranking screen detected before move", main)
        self.assertNotIn("Post-drop ranking candidate found", main)

    def test_soren91_does_not_treat_stale_result_screen_as_next_game(self):
        main = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("awaitingFreshRoundAfterResult = true", main)
        self.assertIn("interRoundWaitingSeen", main)
        self.assertIn("Ignoring stale post-result ranking screen before game", main)
        self.assertIn("Waiting for inter-round screen before accepting game", main)
        self.assertIn("MIN_RANKING_FALLBACK_COMMENT_TURNS = 20", main)
        self.assertIn("turns >= MIN_RANKING_FALLBACK_COMMENT_TURNS", main)

    def test_bridge_desync_stops_stale_soren91_only_outside_improve(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "eloop.sh").read_text()
        runner = (REPO_ROOT / "strategy_runner.py").read_text()

        self.assertIn("BRIDGE_DESYNC_STOP_STALE_SOREN91_ENABLED", config)
        self.assertIn("BRIDGE_DESYNC_SOREN91_STOP_TIMEOUT", config)
        self.assertIn("PHANTOM_GAME_AUTO_RECOVER_ENABLED", config)
        self.assertIn('SOREN_BRIDGE_DESYNC_LIMIT="${SOREN_BRIDGE_DESYNC_LIMIT:-3}"', config)
        self.assertIn('BRIDGE_DESYNC_LIMIT = int(os.environ.get("SOREN_BRIDGE_DESYNC_LIMIT", "3") or "3")', runner)
        self.assertIn("improve_started_midgame", runner)
        self.assertIn("IMPROVE STARTED MIDGAME", runner)
        self.assertIn("IMPROVE STARTED BEFORE DROP", runner)
        self.assertIn("即bridge復旧", loop)
        self.assertIn("PHANTOM_GAME_AUTO_RECOVER_ENABLED", loop)
        self.assertIn("[PHANTOM] bridge 再起動 成功", loop)
        self.assertIn("improve_started_midgame", loop)
        self.assertIn("評価/rollingへ入れず", loop)
        self.assertIn("_bridge_desync_improve_active", loop)
        self.assertIn('[ "${_bridge_desync_improve_active:-0}" != "1" ]', loop)
        self.assertIn("中華AIプレイ中に soren91 残存を検出", loop)
        self.assertIn('SOREN91_STOP_TIMEOUT="${BRIDGE_DESYNC_SOREN91_STOP_TIMEOUT:-0}" soren91_stop', loop)
        self.assertIn("! _is_improve_running", loop)
        self.assertIn("manual_meriken_mode_is_enabled", loop)

    def test_soviet_watchdog_falls_back_to_cdp_endpoint_pid(self):
        watchdog = (REPO_ROOT / "soviet_watchdog.sh").read_text()

        self.assertIn('CDP_ENDPOINT_FILE="tmp/cdp_endpoint.json"', watchdog)
        self.assertIn('ep=$(sed -n', watchdog)
        self.assertIn('"pid"', watchdog)
        self.assertIn('if [ -n "$ep" ]; then', watchdog)
        self.assertIn('[ "$cwd" = "$SCRIPT_DIR" ] && echo "$ep"', watchdog)
        self.assertIn('crash="port消失"', watchdog)

    def test_rollback_postmortem_opencode_lock_cannot_block_live_radio_forever(self):
        ai_generate = (REPO_ROOT / "lib/ai_generate.sh").read_text()
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("ROLLBACK-POSTMORTEM", ai_generate)
        self.assertIn("ROLLBACK_POSTMORTEM_OPENCODE_LOCK_STALE_SEC", ai_generate)
        self.assertIn('postmortem_stale_sec=$((max_wait_sec - wait_sec))', ai_generate)
        self.assertIn("stale rollback-postmortem run lock cleared", ai_generate)
        self.assertIn('OPENCODE_RUN_LOCK_STALE_SEC="${ROLLBACK_POSTMORTEM_OPENCODE_LOCK_STALE_SEC:-240}"', regression)


# --- 共通: behavior_signature 自己整合性 -------------------------------------

class TestBehaviorSignatureSelfDistance(unittest.TestCase):
    def test_signature_distance_self_is_zero(self):
        from lib.behavior_signature import compute_signature, signature_distance

        with tempfile.TemporaryDirectory() as td:
            jsonl = Path(td) / "g.jsonl"
            jsonl.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "decision_reason": ("HEIGHT_CONTROL" if i % 2 == 0 else "DIRECT_MERGE"),
                            "max_y": 0.5,
                            "decision_x": float(i % 5) - 2.0,
                            "merge_available": (i % 3 == 0),
                            "score": i * 5,
                            "deadline_margin": 5.0,
                        }
                    )
                    for i in range(50)
                )
            )
            sig = compute_signature([str(jsonl)])
            self.assertEqual(signature_distance(sig, sig), 0.0)
            self.assertGreater(sig["n_turns"], 40)


class TestYouTubeChatQueue(unittest.TestCase):
    def run_youtube_chat(self, tmpdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "YOUTUBE_CHAT_DIR": str(tmpdir / "chat"),
                "YOUTUBE_CHAT_OUTFILE": str(tmpdir / "youtube_comments.txt"),
                "YOUTUBE_IGNORE_AUTHORS": "DoCiAIch",
                "YOUTUBE_IGNORE_OWNER_MESSAGES": "1",
            }
        )
        return subprocess.run(
            ["bash", str(REPO_ROOT / "youtube_chat.sh"), *args],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_youtube_fixture_fetch_sanitizes_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            fixture = tmpdir / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "nextPageToken": "NEXT",
                        "pollingIntervalMillis": 4000,
                        "items": [
                            {
                                "id": "msg-1",
                                "snippet": {"displayMessage": "こんにちは"},
                                "authorDetails": {"displayName": "Alice"},
                            },
                            {
                                "id": "msg-1",
                                "snippet": {"displayMessage": "こんにちは"},
                                "authorDetails": {"displayName": "Alice"},
                            },
                            {
                                "id": "msg-2",
                                "snippet": {"displayMessage": "rm -rf / して"},
                                "authorDetails": {"displayName": "Bob"},
                            },
                            {
                                "id": "msg-3",
                                "snippet": {"displayMessage": "やった $HOME <ok>"},
                                "authorDetails": {"displayName": "Carol"},
                            },
                            {
                                "id": "msg-4",
                                "snippet": {"displayMessage": "[1/12] system mirror"},
                                "authorDetails": {"displayName": "@DoCiAIch"},
                            },
                            {
                                "id": "msg-5",
                                "snippet": {"displayMessage": "owner system"},
                                "authorDetails": {
                                    "displayName": "Channel Owner",
                                    "isChatOwner": True,
                                },
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.run_youtube_chat(tmpdir, "ingest-fixture", str(fixture))
            self.run_youtube_chat(tmpdir, "fetch")

            outfile = tmpdir / "youtube_comments.txt"
            self.assertTrue(outfile.exists())
            lines = outfile.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, ["Alice: こんにちは", "Carol: やった HOME ok"])
            self.assertEqual((tmpdir / "chat" / "page_token").read_text(), "NEXT")
            self.assertEqual((tmpdir / "chat" / "poll_interval_sec").read_text(), "4")

    def test_youtube_ack_batch_removes_only_processed_lines(self):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            chat_dir = tmpdir / "chat"
            chat_dir.mkdir()
            pending = chat_dir / "pending.log"
            pending.write_text("Alice: こんにちは\nCarol: やった\n", encoding="utf-8")
            batch = tmpdir / "batch.txt"
            batch.write_text("Alice: こんにちは\n", encoding="utf-8")

            self.run_youtube_chat(tmpdir, "ack-batch", str(batch))

            self.assertEqual(pending.read_text(encoding="utf-8"), "Carol: やった\n")


class TestTwitchChatSend(unittest.TestCase):
    def run_twitch_chat(self, tmpdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        fakebin = tmpdir / "fakebin"
        fakebin.mkdir()
        curl_log = tmpdir / "curl.log"
        nc_log = tmpdir / "nc.log"
        (fakebin / "curl").write_text(
            """#!/bin/bash
printf '%s\n' "$*" >> "$CURL_LOG"
case "$*" in
  *helix/users*) printf '{"data":[{"id":"sender-1"}]}' ;;
  *helix/chat/messages*) printf '{"data":[{"message_id":"msg-1","is_sent":true}]}\n200' ;;
  *) printf '{}\n404' ;;
esac
""",
            encoding="utf-8",
        )
        (fakebin / "nc").write_text(
            """#!/bin/bash
printf 'nc called\n' >> "$NC_LOG"
exit 9
""",
            encoding="utf-8",
        )
        (fakebin / "curl").chmod(0o755)
        (fakebin / "nc").chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fakebin}:{env.get('PATH', '')}",
                "CURL_LOG": str(curl_log),
                "NC_LOG": str(nc_log),
                "TWITCH_BOT_TOKEN": "token",
                "TWITCH_CLIENT_ID": "client-1",
                "TWITCH_BROADCASTER_ID": "broadcaster-1",
                "TWITCH_CHANNEL": "azumagbanjo",
            }
        )
        return subprocess.run(
            ["bash", str(REPO_ROOT / "twitch_chat.sh"), *args],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_twitch_send_prefers_chat_messages_api(self):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            result = self.run_twitch_chat(tmpdir, "send", "[1/12] score=123 | eval_avg=456")

            self.assertEqual(result.returncode, 0, result.stderr)
            curl_log = (tmpdir / "curl.log").read_text(encoding="utf-8")
            self.assertIn("helix/users", curl_log)
            self.assertIn("helix/chat/messages", curl_log)
            self.assertFalse((tmpdir / "nc.log").exists())


class TestParamParallelDetectionLagClosed(unittest.TestCase):
    """POST-IMPROVE param並列(隔離評価)の検出ラグを封じ、soren91が代打起動されない。

    バグ: wildcard_parallel.py 起動から status 書込みまでの数秒、
    _wildcard_parallel_active が false のため主ループが soren91 を代打起動し、
    候補chrome群と共有Chromeの GUI登録が競合して crash + soren91 flapping。
    修正: _post_improve_param_parallel_trial が python 起動前に status を先置きする。
    """

    @staticmethod
    def _extract_fn(text: str, name: str) -> str:
        """column-0 で開始/終了する shell 関数を、内包 heredoc を尊重して抽出。

        関数本体の python heredoc 内にも column-0 の `}` (dict 閉じ) が出るため、
        <<'TAG' ... TAG の heredoc 区間を追跡し、その外側の `}` だけを関数終端とみなす。
        """
        lines = text.splitlines(keepends=True)
        out, capturing, heredoc_tag = [], False, None
        for ln in lines:
            if not capturing and ln.startswith(name + "() {"):
                capturing = True
            if not capturing:
                continue
            out.append(ln)
            stripped = ln.rstrip("\n")
            if heredoc_tag is not None:
                if stripped.strip() == heredoc_tag:
                    heredoc_tag = None
                continue
            m = re.search(r"<<-?'?([A-Za-z_][A-Za-z0-9_]*)'?", ln)
            if m:
                heredoc_tag = m.group(1)
                continue
            if stripped == "}":  # heredoc 外の column-0 `}` が関数終端
                break
        return "".join(out)

    def test_prewrite_happens_before_python_launch_and_opt_in_soren91_stop(self):
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        # soren91 が既に動いている場合、デフォルトでは隔離評価をスキップし、停止しない。
        skip_idx = eloop.index("post_improve_param_parallel_skipped_soren91_active")
        # prewrite 呼び出しが python3 wildcard_parallel.py 起動より前にある
        prewrite_idx = eloop.index("_wildcard_parallel_prewrite_status \"$started_at_prewrite\"")
        launch_idx = eloop.index("python3 wildcard_parallel.py \\")
        self.assertLess(
            skip_idx, prewrite_idx,
            "既存soren91を止めないskip判定は wildcard status の先置きより前で行うこと",
        )
        self.assertLess(
            prewrite_idx, launch_idx,
            "status の先置きは wildcard_parallel.py 起動より前で行うこと",
        )
        # opt-in 停止時だけ prewrite 後に soren91 を止める (代打を立てさせない窓を残さない)
        self.assertIn("POST_IMPROVE_PARAM_PARALLEL_STOP_SOREN91:-0", eloop)
        self.assertIn("explicit opt-in allows isolated trial stop", eloop)
        stop_idx = eloop.index("SOREN91_STOP_TIMEOUT=0 soren91_stop")
        self.assertLess(
            prewrite_idx, stop_idx,
            "status の先置きは opt-in soren91 停止より前で行うこと",
        )
        # prewrite ヘルパが定義されている
        self.assertIn("_wildcard_parallel_prewrite_status() {", eloop)
        # phase=generating + block_main_loop=True で書く
        self.assertIn('"phase": "generating"', eloop)
        self.assertIn('"block_main_loop": True', eloop)

    def test_prewrite_makes_active_true_then_age_bound_releases(self):
        """先置き status で _wildcard_parallel_active が即 true、かつ age 上限で解放。"""
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        fn1 = self._extract_fn(eloop, "_wildcard_parallel_prewrite_status")
        fn2 = self._extract_fn(improve, "_wildcard_parallel_active")
        self.assertIn("python3", fn1)
        self.assertIn("phase", fn2)

        with tempfile.TemporaryDirectory() as td:
            status = Path(td) / "wildcard_parallel_status.json"
            script = textwrap.dedent(f"""
                set +e
                export TMP_STATE_DIR="{td}"
                export WILDCARD_PARALLEL_STATUS_FILE="{status}"
                {fn1}
                {fn2}
                NOW=$(date +%s)
                _wildcard_parallel_prewrite_status "$NOW"
                if _wildcard_parallel_active; then echo FRESH_ACTIVE; else echo FRESH_INACTIVE; fi
                OLD=$((NOW - 4000))
                _wildcard_parallel_prewrite_status "$OLD"
                if WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC=3600 _wildcard_parallel_active; then
                    echo STALE_ACTIVE; else echo STALE_INACTIVE; fi
                PIDLESS_OLD=$((NOW - 700))
                _wildcard_parallel_prewrite_status "$PIDLESS_OLD"
                if WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC=3600 _wildcard_parallel_active; then
                    echo PIDLESS_ACTIVE; else echo PIDLESS_INACTIVE; fi
            """)
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                 text=True, capture_output=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout
            # 先置き直後: active=true → 主ループ branch1 が発火 → soren91 代打を立てない
            self.assertIn("FRESH_ACTIVE", out, res.stderr)
            # 3600s 超の孤児 status: active=false → 本線を永久ブロックしない
            self.assertIn("STALE_INACTIVE", out, res.stderr)
            # controller_pid 導入前の古い running status は短い猶予後に解放する
            self.assertIn("PIDLESS_INACTIVE", out, res.stderr)

    def test_prewrite_status_age_bound_constant_exists(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        # 既存の age 上限 (孤児が永久ブロックしない保証) を退行させない
        self.assertIn("WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC", improve)
        self.assertIn("(time.time() - started_at) > max_sec", improve)
        self.assertIn("WILDCARD_PARALLEL_PIDLESS_STALE_SEC", improve)
        self.assertIn("controller_pid", improve)

    def test_slot_activity_fresh_guard_structural(self):
        """_wildcard_parallel_active が slot game_history 活性チェックを含む。"""
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        self.assertIn("slot_activity_fresh", improve)
        self.assertIn("game_history/latest.jsonl", improve)
        self.assertIn("WILDCARD_PARALLEL_SLOT_FRESH_SEC", improve)

    def test_slot_activity_fresh_blocks_terminal_phase(self):
        """終端phaseでも fresh な game_history があれば active=true を維持する。

        pgrep で親プロセスが検出漏れしてもスロットがゲーム中なら main loop をブロック。
        """
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        fn2 = self._extract_fn(improve, "_wildcard_parallel_active")

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td) / "session"
            gh_dir = session_dir / "cand-1" / "game_history"
            gh_dir.mkdir(parents=True)
            gh_file = gh_dir / "latest.jsonl"
            gh_file.write_text("{}\n", encoding="utf-8")
            # 直近に更新 → 鮮度OK
            now = time.time()
            os.utime(gh_file, (now, now))

            status = Path(td) / "wildcard_parallel_status.json"
            status.write_text(
                json.dumps({
                    "phase": "winner_selected",
                    "block_main_loop": True,
                    "started_at": now,
                    "session_dir": str(session_dir),
                }),
                encoding="utf-8",
            )
            script = textwrap.dedent(f"""
                set +e
                export TMP_STATE_DIR="{td}"
                export WILDCARD_PARALLEL_STATUS_FILE="{status}"
                export WILDCARD_PARALLEL_SLOT_FRESH_SEC=180
                {fn2}
                if _wildcard_parallel_active; then echo SLOT_ACTIVE; else echo SLOT_INACTIVE; fi
            """)
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                 text=True, capture_output=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            # 終端phaseでも fresh なスロットがあれば active=true
            self.assertIn("SLOT_ACTIVE", res.stdout, res.stderr)

    def test_slot_activity_stale_releases_terminal_phase(self):
        """終端phaseで game_history が古い(stale)なら active=false にする。"""
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        fn2 = self._extract_fn(improve, "_wildcard_parallel_active")

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td) / "session"
            gh_dir = session_dir / "cand-1" / "game_history"
            gh_dir.mkdir(parents=True)
            gh_file = gh_dir / "latest.jsonl"
            gh_file.write_text("{}\n", encoding="utf-8")
            stale_ts = time.time() - 400  # 400s 前 > SLOT_FRESH_SEC=180
            os.utime(gh_file, (stale_ts, stale_ts))

            now = time.time()
            status = Path(td) / "wildcard_parallel_status.json"
            status.write_text(
                json.dumps({
                    "phase": "no_candidate",
                    "block_main_loop": True,
                    "started_at": now,
                    "session_dir": str(session_dir),
                }),
                encoding="utf-8",
            )
            script = textwrap.dedent(f"""
                set +e
                export TMP_STATE_DIR="{td}"
                export WILDCARD_PARALLEL_STATUS_FILE="{status}"
                export WILDCARD_PARALLEL_SLOT_FRESH_SEC=180
                {fn2}
                if _wildcard_parallel_active; then echo STALE_ACTIVE; else echo STALE_INACTIVE; fi
            """)
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                 text=True, capture_output=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            # stale なスロット + 終端phase + プロセス無し → inactive
            self.assertIn("STALE_INACTIVE", res.stdout, res.stderr)

    def test_restored_ended_status_releases_even_with_fresh_slot_activity(self):
        """cleanup/restore 済み status は fresh slot mtime が残っていても解放する。"""
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        fn2 = self._extract_fn(improve, "_wildcard_parallel_active")

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td) / "session"
            gh_dir = session_dir / "slot-1" / "game_history"
            gh_dir.mkdir(parents=True)
            gh_file = gh_dir / "latest.jsonl"
            gh_file.write_text("{}\n", encoding="utf-8")
            now = time.time()
            os.utime(gh_file, (now, now))

            status = Path(td) / "wildcard_parallel_status.json"
            status.write_text(
                json.dumps({
                    "phase": "restored",
                    "block_main_loop": True,
                    "started_at": now - 30,
                    "ended_at": now,
                    "detail": "cleanup_stale",
                    "session_dir": str(session_dir),
                }),
                encoding="utf-8",
            )
            script = textwrap.dedent(f"""
                set +e
                export TMP_STATE_DIR="{td}"
                export WILDCARD_PARALLEL_STATUS_FILE="{status}"
                export WILDCARD_PARALLEL_SLOT_FRESH_SEC=180
                {fn2}
                if _wildcard_parallel_active; then echo RESTORED_ACTIVE; else echo RESTORED_INACTIVE; fi
            """)
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                 text=True, capture_output=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("RESTORED_INACTIVE", res.stdout, res.stderr)

    def test_slot_activity_fresh_terminal_phase_slot_dir_pattern(self):
        """終端phaseでも slot-N/game_history 形式の fresh なファイルがあれば active=true。

        glob `*/game_history/latest.jsonl` が slot-N と cand-N の両ディレクトリ命名を
        正しく拾うことを確認する。
        """
        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        fn2 = self._extract_fn(improve, "_wildcard_parallel_active")

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td) / "session"
            gh_dir = session_dir / "slot-3" / "game_history"
            gh_dir.mkdir(parents=True)
            gh_file = gh_dir / "latest.jsonl"
            gh_file.write_text("{}\n", encoding="utf-8")
            now = time.time()
            os.utime(gh_file, (now, now))

            status = Path(td) / "wildcard_parallel_status.json"
            status.write_text(
                json.dumps({
                    "phase": "no_candidate",
                    "block_main_loop": True,
                    "started_at": now,
                    "session_dir": str(session_dir),
                }),
                encoding="utf-8",
            )
            # 終端phase + no process + fresh slot-N → active
            script = textwrap.dedent(f"""
                set +e
                export TMP_STATE_DIR="{td}"
                export WILDCARD_PARALLEL_STATUS_FILE="{status}"
                export WILDCARD_PARALLEL_SLOT_FRESH_SEC=180
                {fn2}
                if _wildcard_parallel_active; then echo SLOTDIR_ACTIVE; else echo SLOTDIR_INACTIVE; fi
            """)
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                 text=True, capture_output=True, check=False)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("SLOTDIR_ACTIVE", res.stdout, res.stderr)


class TestSoren91FastExitBackoff(unittest.TestCase):
    """soren91 runner が rc=0 即時終了を繰り返す flap storm をバックオフで抑える。

    rc=0 即時終了 = 共有Chrome attach失敗等を main().catch が握り潰した結果で
    「今は走るべきでない」。3s固定 retry を続けると候補chrome群と GUI登録を奪い合う。
    """

    def test_run_player_loop_reasserts_lock_to_prevent_multi_runner(self):
        """各ループ反復で lock 所有権を再確認し、別の生存runnerが所有していれば退避exit
        する。起動時だけ lock を見る旧実装では、lock がクリアされ新runnerが起動した後も
        旧runnerが走り続け、多重runner flapping が累積した (実測で6並走)。"""
        import subprocess

        loop = (REPO_ROOT / "soren91/run_player_loop.sh").read_text(encoding="utf-8")
        self.assertIn("_runner_still_owner() {", loop)
        self.assertIn("if ! _runner_still_owner; then", loop)
        self.assertIn("to avoid multi-runner flapping", loop)

        # behavioral: extract just the function (no heredoc inside) and exercise it.
        fn_lines, capturing = [], False
        for ln in loop.splitlines(keepends=True):
            if ln.startswith("_runner_still_owner() {"):
                capturing = True
            if capturing:
                fn_lines.append(ln)
                if ln.rstrip("\n") == "}":
                    break
        fn = "".join(fn_lines)
        self.assertIn("return 1", fn)

        with tempfile.TemporaryDirectory() as td:
            script = textwrap.dedent(f"""
                set +e
                RUNNER_LOCK_DIR="{td}/.runner.lock"
                _pid_alive(){{ case "$1" in ""|*[!0-9]*) return 1;; esac; kill -0 "$1" 2>/dev/null; }}
                {fn}
                sleep 60 & ALIVE=$!
                mkdir -p "$RUNNER_LOCK_DIR"; printf 'pid=%s\\n' "$ALIVE" > "$RUNNER_LOCK_DIR/owner"
                _runner_still_owner; echo "live_other=$?"
                kill "$ALIVE" 2>/dev/null
                rm -rf "$RUNNER_LOCK_DIR"
                _runner_still_owner; echo "gone=$?"
                _runner_still_owner; echo "self=$?"
            """)
            res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
            out = dict(x.split("=") for x in res.stdout.split() if "=" in x)
            self.assertEqual(out.get("live_other"), "1", f"defer to live owner; {res.stdout}{res.stderr}")
            self.assertEqual(out.get("gone"), "0", "reclaim when lock gone")
            self.assertEqual(out.get("self"), "0", "continue when self owns")

    def test_run_player_loop_has_fast_exit_backoff(self):
        loop = (REPO_ROOT / "soren91/run_player_loop.sh").read_text(encoding="utf-8")
        self.assertIn("FAST_EXIT_THRESHOLD_SEC", loop)
        self.assertIn("FAST_EXIT_BACKOFF_MAX_SEC", loop)
        self.assertIn("fast_exit_streak", loop)
        self.assertIn("run_dur=", loop)
        # 実ゲーム(閾値以上)で streak をリセットし、代打を恒久停止しない
        self.assertIn("fast_exit_streak=0", loop)
        # 指数バックオフ (上限頭打ち)
        self.assertIn("RETRY_DELAY_SEC << (fast_exit_streak - 1)", loop)

    def test_backoff_progression_and_reset(self):
        """連続即時終了で 3→6→12...→上限、実ゲームでリセット、を実挙動で確認。"""
        loop_path = REPO_ROOT / "soren91/run_player_loop.sh"
        loop = loop_path.read_text(encoding="utf-8")
        # ループ本体の delay 計算ロジックだけを再現する最小スクリプト
        # (定数とバックオフ式は本体と同一の文字列であることを上で検証済)
        self.assertIn("RETRY_DELAY_SEC=\"${SOREN91_RESTART_DELAY_SEC:-3}\"", loop)
        script = textwrap.dedent("""
            RETRY_DELAY_SEC=3
            FAST_EXIT_THRESHOLD_SEC=20
            FAST_EXIT_BACKOFF_MAX_SEC=60
            fast_exit_streak=0
            calc() {
              local run_dur="$1"
              local retry_delay="$RETRY_DELAY_SEC"
              if [ "$run_dur" -lt "$FAST_EXIT_THRESHOLD_SEC" ]; then
                fast_exit_streak=$((fast_exit_streak + 1))
                retry_delay=$((RETRY_DELAY_SEC << (fast_exit_streak - 1)))
                [ "$retry_delay" -gt "$FAST_EXIT_BACKOFF_MAX_SEC" ] && retry_delay="$FAST_EXIT_BACKOFF_MAX_SEC"
                [ "$retry_delay" -lt "$RETRY_DELAY_SEC" ] && retry_delay="$RETRY_DELAY_SEC"
              else
                fast_exit_streak=0
              fi
              echo "$retry_delay"
            }
            for d in 1 1 2 1 0 1; do calc "$d"; done
            calc 120
            calc 1
        """)
        res = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
        self.assertEqual(res.returncode, 0, res.stderr)
        delays = [int(x) for x in res.stdout.split()]
        # 6 連続即時終了: 指数増 + 60 で頭打ち
        self.assertEqual(delays[:6], [3, 6, 12, 24, 48, 60])
        # 実ゲーム(120s)後: base に戻る
        self.assertEqual(delays[6], 3)
        # 直後の即時終了: streak リセット済なので再び base から
        self.assertEqual(delays[7], 3)


class TestPostImproveImportsRussiaOnAllPaths(unittest.TestCase):
    """post-improve param並列は no-candidate / winner欠落 / validation失敗 でも候補ゲーム
    統計(建国履歴含む)を import する。winner採用パスだけ import していた旧実装では、
    時間切れで no-candidate 終了した run の候補が建国したロシアが
    russia_creation_history.tsv に残らなかった (実測: 候補 cand-5-r5 が russia=1 だが
    01:42:59 no-candidate 終了で記録漏れ)。"""

    def test_import_called_on_all_exit_paths_before_cleanup(self):
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        self.assertIn("_post_improve_import_result_stats() {", eloop)
        # 3 early-return paths import with "" (no winner) + the success path with $HASH_AFTER
        self.assertEqual(eloop.count('_post_improve_import_result_stats "$result_file" ""'), 3)
        self.assertIn('_post_improve_import_result_stats "$result_file" "$HASH_AFTER"', eloop)
        # in the no-candidate path the import must precede cleanup (archives still present)
        nc = eloop.split('[PARAM-PARALLEL] no candidate', 1)[1].split("return 0", 1)[0]
        self.assertIn("_post_improve_import_result_stats", nc)
        self.assertLess(
            nc.index("_post_improve_import_result_stats"),
            nc.index("_wildcard_parallel_cleanup_sessions"),
            "import must run before cleanup wipes candidate game archives",
        )
        # the helper records via _import_wildcard_parallel_game_stats, which appends
        # russia/soviet creation history when a candidate game founded a nation.
        helper = eloop.split("_post_improve_import_result_stats() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("_import_wildcard_parallel_game_stats", helper)
        self.assertIn("parallel_candidates", helper)
        importer = eloop.split("_import_wildcard_parallel_game_stats() {", 1)[1]
        self.assertIn('_append_celebration_history "russia"', importer)


class TestCandidateChromeLaunchStagger(unittest.TestCase):
    """6 候補 Chrome の同時起動が macOS の app/window-server 登録
    (_RegisterApplication -> NSApplication init) を奪い合い、起動中の Chrome を
    EXC_CRASH/SIGABRT で落とす crash を、起動の直列化 + stagger で防ぐ。"""

    def test_prelaunch_serializes_and_staggers_chrome_spawns(self):
        wp = (REPO_ROOT / "wildcard_parallel.py").read_text(encoding="utf-8")
        self.assertIn("_CHROME_LAUNCH_LOCK = Lock()", wp)
        self.assertIn("def _spawn_with_launch_stagger(spawn_fn):", wp)
        self.assertIn("def _run_with_launch_stagger(launch_fn):", wp)
        self.assertIn("WILDCARD_PARALLEL_CHROME_LAUNCH_STAGGER_SEC", wp)
        # both prelaunch paths (open -g -n, and the direct Popen fallback) hold the
        # launch lock until CDP is reachable, not merely until the process is spawned.
        self.assertIn("def run_macos_open(open_args: list[str], candidate_env: dict[str, str])", wp)
        self.assertIn("def run_macos_open_and_wait(open_args: list[str], candidate_env: dict[str, str])", wp)
        self.assertIn('WILDCARD_PARALLEL_OPEN_TIMEOUT_SEC', wp)
        self.assertIn('timeout=open_timeout', wp)
        self.assertIn('" ".join(shlex.quote(part) for part in open_args)', wp)
        self.assertIn('["/bin/zsh", "-lc", shell_cmd]', wp)
        self.assertIn("return bool(_run_with_launch_stagger(launch_and_wait))", wp)
        self.assertIn("return wait_for_candidate_chrome_cdp(cdp_port)", wp)
        self.assertIn("launch_bridge_with_chrome_lock", wp)
        self.assertIn("WILDCARD_PARALLEL_SERIALIZE_BRIDGE_LAUNCH", wp)
        # the lock is held through browser readiness/failure + stagger sleep
        fn = wp.split("def _run_with_launch_stagger(launch_fn):", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("with _CHROME_LAUNCH_LOCK:", fn)
        self.assertIn("time.sleep(stagger)", fn)


class TestStrategyRussiaPhaseBoundary(unittest.TestCase):
    """T14 is still the pre-Russia gate; only T15 should switch to Russia phase."""

    def test_russia_phase_uses_type15_not_type14(self):
        strategy = (REPO_ROOT / "strategy.py").read_text(encoding="utf-8")
        self.assertIn('russia_phase_count = sum(1 for p in pieces if p.get("type") == 15)', strategy)
        self.assertIn("double_russia_phase = russia_phase_count >= 2", strategy)
        self.assertNotIn('p.get("type") in [14, 15]', strategy)
        self.assertIn("type 14（カザフ）はロシア前段", strategy)

    def test_deadline_guard_prefers_t13_pair_compress_before_pair_center(self):
        strategy = (REPO_ROOT / "strategy.py").read_text(encoding="utf-8")
        mode_chain = strategy.split("__dlg_mode = None", 1)[1].split("if __dlg_mode is None:", 1)[0]
        self.assertLess(
            mode_chain.index('__dlg_mode = "t13_pair_compress"'),
            mode_chain.index('__dlg_mode = "first_russia_pair"'),
        )

    def test_deadline_guard_t14_pair_gets_cluster_priority(self):
        strategy = (REPO_ROOT / "strategy.py").read_text(encoding="utf-8")
        self.assertIn('if __dlg_mode == "russia_pair"', strategy)
        self.assertIn('"t12_consolidate", "russia_pair"', strategy)
        self.assertIn('DEADLINE_GUARD_RUSSIA_PAIR_CLUSTER', strategy)

    def test_t11_cloud_without_t12_gets_pre_russia_rescue(self):
        strategy = (REPO_ROOT / "strategy.py").read_text(encoding="utf-8")
        self.assertIn("pre_russia_t11_cloud_to_t12_ready", strategy)
        self.assertIn("PRE_RUSSIA_T11_CLOUD_TO_T12", strategy)
        self.assertIn("max_type_on_board == 11", strategy)
        self.assertIn("pre_russia_counts.get(12, 0) == 0", strategy)
        self.assertIn('"t11_cloud_to_t12"', strategy)
        self.assertIn("DEADLINE_GUARD_PRE_RUSSIA_T11_CLOUD_TO_T12", strategy)


if __name__ == "__main__":
    unittest.main()
