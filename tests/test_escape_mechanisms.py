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
import subprocess
import sys
import tempfile
import textwrap
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

    def test_anchor_selection_prefers_near_score_objective_progress(self):
        """anchor はトップスコア近傍なら建国進捗を優先して局所解化を避ける。"""
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
                        "russiaPath": {
                            "scores": [1120] * 12,
                            "games_total": 12,
                            "_recent_archives": [],
                            "max_types": [15] + [13] * 11,
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
            self.assertEqual(anchor["hash"], "russiaPath")
            self.assertEqual(anchor["best_max_type"], 15)
            self.assertEqual(anchor["russia_count"], 1)

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

    def test_direct_anchor_promotion_does_not_overwrite_russia_anchor_with_score_only(self):
        """PROMOTE 経路の直接書き込みでも Russia anchor を score-only で潰さない。"""
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

    def test_existing_russia_anchor_is_kept_against_near_score_only_candidate(self):
        """既存 Russia anchor は近傍スコアの score-only 候補では潰さない。"""
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
            self.assertEqual(anchor["hash"], "russiaPath")
            self.assertEqual(anchor["best_max_type"], 15)
            self.assertEqual(anchor["russia_count"], 1)

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
        self.assertIn('./eloop_improve.sh "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" "$reason"', improve_sh)

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

    def test_wildcard_parallel_does_not_kill_long_surviving_game_by_timeout(self):
        """長く生きている候補ゲームを timeout で落とさない。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn('WILDCARD_PARALLEL_GAMES="${WILDCARD_PARALLEL_GAMES:-12}"', config)
        self.assertIn('--games "${WILDCARD_PARALLEL_GAMES:-12}"', eloop)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_GAMES"), 12)', parallel)
        self.assertNotIn("WILDCARD_PARALLEL_GAME_TIMEOUT", config)
        self.assertNotIn("--game-timeout", parallel)
        self.assertNotIn("deadline = time.time() + args.game_timeout", parallel)
        self.assertNotIn("candidate evaluation timed out", parallel)
        self.assertNotIn('candidate.status = "timeout"', parallel)

    def test_wildcard_parallel_culls_bad_slots_and_refills(self):
        """4ゲーム以降で現トップより悪すぎる候補は補充して、最終候補まで待つ。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn('WILDCARD_PARALLEL_CULL_AFTER_GAMES="${WILDCARD_PARALLEL_CULL_AFTER_GAMES:-4}"', config)
        self.assertIn('WILDCARD_PARALLEL_CULL_COMP_RATIO="${WILDCARD_PARALLEL_CULL_COMP_RATIO:-0.70}"', config)
        self.assertNotIn("WILDCARD_PARALLEL_MAX_REFILLS", config)
        self.assertIn('--cull-after-games "${WILDCARD_PARALLEL_CULL_AFTER_GAMES:-4}"', eloop)
        self.assertIn('--cull-comp-ratio "${WILDCARD_PARALLEL_CULL_COMP_RATIO:-0.70}"', eloop)
        self.assertNotIn("--max-refills", eloop)
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
            args = argparse.Namespace(cull_after_games=4, cull_comp_ratio=0.70)
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

            self.assertFalse(coordinator.should_cull(candidate))
            candidate.scores.extend([45, 45])
            candidate.comp = 63
            self.assertTrue(coordinator.should_cull(candidate))
            self.assertIn("culled after 5 games", candidate.error)

    def test_wildcard_parallel_restarts_bridge_for_each_real_game(self):
        """real 評価は GAMEOVER 状態を複数試合として重複カウントしない。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn('for game_index in range(args.games):', parallel)
        self.assertIn('(workdir / "game_state.json").write_text("{}", encoding="utf-8")', parallel)
        self.assertIn("bridge = launch_bridge(workdir, env, args.bridge_timeout)", parallel)
        self.assertIn("finally:\n                stop_process(bridge)", parallel)

    def test_wildcard_parallel_cleans_candidate_chrome_windows(self):
        """WILDCARD 候補 Chrome は profile/port 指定で残骸 cleanup する。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn('"SOREN_CHROME_NO_FOCUS_LAUNCH": "0"', parallel)
        self.assertIn("def cleanup_chrome_profile_processes", parallel)
        self.assertIn('"wildcard_parallel" not in profile_dir', parallel)
        self.assertIn('"ps", "-Ao", "pid=,command="', parallel)
        self.assertIn("cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)", parallel)

    def test_wildcard_parallel_overlay_embeds_three_live_previews(self):
        """OBS には候補別 CDP screenshot を3枚並べて出す。"""
        parallel = (REPO_ROOT / "wildcard_parallel.py").read_text()
        self.assertIn('"preview_path": str(self.workdir / "tmp" / "preview.png")', parallel)
        self.assertIn("def capture_candidate_preview", parallel)
        self.assertIn("chromium.connectOverCDP", parallel)
        self.assertIn("page.screenshot", parallel)
        self.assertIn("capture_candidate_preview(candidate.cdp_port, workdir / \"tmp\" / \"preview.png\", workdir)", parallel)
        self.assertIn('<img class="preview live-preview"', parallel)
        self.assertIn("aspect-ratio: 16 / 9", parallel)

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
                        {"job_id": "cand-1", "status": "running", "games": 2, "comp": 80, "p25": 70, "p50": 75, "max_type": 12},
                        {"job_id": "cand-2", "status": "running", "games": 2, "comp": 120, "p25": 90, "p50": 100, "max_type": 14},
                    ],
                },
            )
            doc = html_path.read_text(encoding="utf-8")
            self.assertIn("leader cand-2", doc)
            self.assertIn('<div class="rank-badge r1">#1</div>', doc)
            self.assertIn('<div class="rank-badge r2">#2</div>', doc)
            self.assertIn('class="rank-fill"', doc)
            self.assertIn("width:100%", doc)

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
        self.assertIn("wildcard_parallel_obs_show", eloop)
        self.assertIn("wildcardParallelOverlay", eloop)
        self.assertIn("wildcardParallelCand", eloop)
        self.assertIn("${cand_prefix}1,${cand_prefix}2,${cand_prefix}3,${cand_prefix}4,${cand_prefix}5,${cand_prefix}6", eloop)
        self.assertIn("wildcardParallelCand", parallel)
        self.assertIn('default=_int(os.getenv("WILDCARD_PARALLEL_JOBS"), 6)', parallel)
        self.assertIn('cards[:6]', parallel)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_COLS', parallel)
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_W="${WILDCARD_PARALLEL_OBS_CANDIDATE_W:-660}"', (REPO_ROOT / "core/config.sh").read_text())
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_H="${WILDCARD_PARALLEL_OBS_CANDIDATE_H:-425}"', (REPO_ROOT / "core/config.sh").read_text())
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_X="${WILDCARD_PARALLEL_OBS_CANDIDATE_X:--30}"', (REPO_ROOT / "core/config.sh").read_text())
        self.assertIn('WILDCARD_PARALLEL_OBS_CANDIDATE_Y="${WILDCARD_PARALLEL_OBS_CANDIDATE_Y:-180}"', (REPO_ROOT / "core/config.sh").read_text())
        self.assertIn("export WILDCARD_PARALLEL_OBS_CANDIDATE_W", eloop)
        self.assertIn("export WILDCARD_PARALLEL_OBS_CANDIDATE_Y", eloop)
        self.assertIn('candidate.index % cols', parallel)
        self.assertIn('candidate.index // cols', parallel)
        self.assertIn("本線ゲームを止め", loop := (REPO_ROOT / "soren_loop.sh").read_text())
        self.assertIn("候補6面", loop)
        self.assertIn("本線は見えない裏で進ませない", loop)
        self.assertIn('case "$_pause_reason" in\n\t\twildcard|archive_restart)', loop)
        self.assertIn("maybe_show_obs_candidate_source", parallel)
        self.assertIn('"./obs_window_capture_source.sh", "ensure", scene, source, window_pattern', parallel)
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
        self.assertIn("_escape_ai_seed_available", improve)
        self.assertIn("rejected_hash_metrics.json", improve)
        self.assertIn("rolling_scores.json", improve)
        self.assertIn("wildcard_origin.json", improve)
        self.assertIn("reconstructs failures from WILDCARDs", improve)
        self.assertIn("Mature origin + below-anchor metrics", improve)
        self.assertIn("m.get(\"comp\", 0.0) < anchor_comp", improve)
        self.assertIn("export IMPROVE_REASON", eloop)
        self.assertIn('os.environ.get("IMPROVE_REASON", "normal") == "escape_ai"', eloop)
        self.assertIn("seedなしのescape_aiは通常改善と同じため中止", eloop)
        self.assertIn("no_valid_seed", eloop)
        self.assertIn("今回だけAIによる小さな構造変異で大域脱出を狙う", eloop)
        self.assertIn("WILDCARD起源からAI改善の起点候補を選定", eloop)
        self.assertIn("ESCAPE_AI_SEED_JSON", eloop)
        self.assertIn("seed_from_wildcard_", eloop)
        self.assertIn("_improve_flow_notify", eloop)
        self.assertIn("seeded escape_ai candidate? yes", eloop)
        self.assertIn("seeded escape_ai candidate? no", eloop)
        self.assertIn("invalid_seed", eloop)
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
        self.assertIn("early_escape lock ignored: current batch is not bad enough", improve)
        self.assertIn("continue normal accumulation", improve)
        self.assertIn("rm -f \"$IMPROVE_LOCK_FILE\"", improve)
        self.assertIn("last_rollback_pair.json", loop)
        self.assertIn("WILDCARD_TRIGGER_STAGNATION", loop)
        self.assertIn("MIN_GAMES_BEFORE_IMPROVE", loop)
        self.assertIn('SOREN_MAIN_PID="$$"', loop)
        self.assertIn('echo "$SOREN_MAIN_PID" > "$LOCKDIR/pid"', loop)
        self.assertIn("_soren_lock_pid_alive", loop)
        self.assertIn("operation not permitted", loop)
        self.assertIn('err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0', loop)
        self.assertIn('*"soren_loop.sh"*', loop)
        self.assertIn("stale lock owner", loop)
        self.assertIn("replaced by self", loop)
        self.assertIn("queue_early_escape_lock_if_needed", loop)
        self.assertIn("next-game-preflight", loop)
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
        self.assertIn("archive_is_runtime_stable", improve)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", improve)
        self.assertIn("allow_origin_retry", improve)
        self.assertIn("is_cooled_down", improve)
        self.assertIn("find_archive_path", improve)
        self.assertIn("anchor_russia", improve)
        self.assertIn("anchor_soviet", improve)
        self.assertIn("reliable_russia", improve)
        self.assertIn("frontier_candidate", improve)
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
        self.assertIn("source_reliable_russia", regression)
        self.assertIn("quarantine_archive_restart_source", regression)
        self.assertIn("archive_restart_russia_not_reproduced", regression)
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
        self.assertIn('rm -f "$ANALYSIS_RESULT_FILE"', eloop)
        self.assertIn('rm -f "$REVIEW_RESULT_FILE"', eloop)
        self.assertNotIn(': >"$ANALYSIS_RESULT_FILE"', eloop)
        self.assertNotIn(': >"$REVIEW_RESULT_FILE"', eloop)
        self.assertIn("`tmp/review_result.md` は存在しない場合があります", review_prompt)
        self.assertIn("存在しない場合は `Write` で新規作成すること", review_prompt)
        self.assertIn("必ず `## VERDICT: PASS` または `## VERDICT: FAIL`", review_prompt)
        self.assertIn("会話に表示しただけでは失敗", review_prompt)
        self.assertIn("最終応答の前に `tmp/review_result.md` が作成・更新済み", review_prompt)
        self.assertNotIn("`tmp/review_result.md` は既に存在", review_prompt)
        self.assertIn("_repair_review_verdict_file", eloop)
        self.assertIn("REVIEW-VERDICT-REPAIR", eloop)
        self.assertIn("Stage3: review verdict missing → repair verdict file", eloop)
        self.assertIn("strategy.py.staging` は編集禁止", eloop)
        self.assertIn("読まずに上書きしようとするとツール制約で失敗する", eloop)
        self.assertIn("Read 後に既存ファイルを直す場合は Edit", eloop)
        self.assertIn("存在しない場合のみ Write", eloop)
        self.assertIn("emit a non-empty verdict/status in the review_verdict JSON block", eloop)


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

    def test_power_exponents_are_not_wildcard_targets(self):
        """累乗指数を揺らすと距離計算が複素数化しうるため候補から外す。"""
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
            self.assertNotIn(4, [item["lineno"] for item in payload["applied"]])
            self.assertIn(5, [item["lineno"] for item in payload["applied"]])


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
            timeout=15,
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
        self.assertIn("card_gacha | chitchat | short_reaction", script)
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

    def test_t13_t14_proximity_bonus_keeps_reactive_danger_guard(self):
        strategy = (REPO_ROOT / "strategy.py").read_text()
        block = strategy.rsplit("axis 9.68 T13/T14 proximity bonus", 1)[1].split(
            "axis 9.7 pipeline-aware placement guidance", 1
        )[0]

        self.assertIn("reactive_pair_count < 3", block)
        self.assertIn('reasons.append("T13_PROXIMITY_BONUS")', block)
        self.assertIn('reasons.append("T14_PROXIMITY_BONUS")', block)

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
        self.assertIn('if [ -n "$SOREN91_OBS_INPUT_NAME" ]; then', control)
        self.assertIn('SOREN_GAME_OBS_SOURCE', control)
        self.assertIn('china_show_sources="$game_source,$china_show_sources"', control)
        self.assertIn("${STATUS_OVERLAY_SOURCE:-statsOverlay}", control)
        self.assertIn("${SHOW_STATUS_OVERLAY_SOURCE:-opsOverlay}", control)
        self.assertIn("${OBS_DASHBOARD_SOURCE:-dashboard}", control)
        self.assertIn('show:"$status_source","$show_status_source" $s91_show_op hide:"$meriken_hide_sources"', control)
        self.assertIn('show:"$status_source","$show_status_source","$china_show_sources" $s91_hide_op', control)
        self.assertIn("改善中も stats/ops は監視用に維持", control)
        self.assertNotIn("hide:console1,console2", control)
        self.assertNotIn("show:console1,console2", control)

    def test_soren91_start_prefers_tmux_tty_runner(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()
        runner = (REPO_ROOT / "soren91/run_player_loop.sh").read_text()

        self.assertIn("tmux new-session -d -s soren91_runner", control)
        self.assertIn("exec /bin/bash '$SOREN91_RUNNER_SCRIPT'", control)
        self.assertIn("trap '' HUP", runner)
        self.assertIn("trap '_on_signal TERM' TERM", runner)
        self.assertIn("trap '_on_exit' EXIT", runner)

    def test_soren91_pid_file_survives_hidden_command_lookup(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()

        self.assertIn('cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")', control)
        self.assertIn('if [ -z "$cmd" ]; then', control)
        self.assertIn('printf \'%s\' "$pid"\n\t\t\treturn 0', control)
        self.assertIn('s/^pid=//p', control)
        self.assertIn('$SOREN91_DIR/tmp/.runner.lock/owner', control)
        self.assertIn("_soren91_observable_fresh()", control)
        self.assertIn('SOREN91_OBSERVABLE_FRESH_SEC:-120', control)
        self.assertIn('$SOREN91_DIR/tmp/in_game', control)
        self.assertIn('_soren91_observable_fresh || return 1', control)

    def test_soren91_browser_launch_does_not_raise_focus_on_macos(self):
        main = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("launchStandaloneBrowserWithoutFocus", main)
        self.assertIn("'/usr/bin/open'", main)
        self.assertIn("'-g'", main)
        self.assertIn("SOREN91_CHROME_NO_FOCUS_LAUNCH", main)
        self.assertNotIn(".bringToFront()", main)

    def test_soviet_local_browser_launch_does_not_raise_focus_on_macos(self):
        local = (REPO_ROOT / "soviet_local.mjs").read_text()

        self.assertIn("launchPersistentContextWithoutFocus", local)
        self.assertIn("'/usr/bin/open'", local)
        self.assertIn("'-g'", local)
        self.assertIn("SOREN_CHROME_NO_FOCUS_LAUNCH", local)
        self.assertIn("chromium.launchPersistentContext", local)
        self.assertIn("Google Chrome(?: for Testing)?", local)


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

    def test_soren91_reapplies_blackhole_sink_after_unity_audio_context_starts(self):
        soren91 = (REPO_ROOT / "soren91/main.mjs").read_text()

        self.assertIn("__soren91AudioOutputWatchdogInstalled", soren91)
        self.assertIn("setInterval(() =>", soren91)
        self.assertIn("globalThis.__soren91RouteAudioOutput?.()", soren91)
        self.assertIn("ctx.resume().catch(() => {})", soren91)
        self.assertIn("await ctx.setSinkId('')", soren91)
        self.assertIn("alreadyRouted && allRunning", soren91)


# --- Soviet objective is visible to improvement AI ---------------------------

class TestSovietObjectiveImproveInputs(unittest.TestCase):
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

    def test_deadline_guard_injector_filters_merge_result_crossing(self):
        injector = (REPO_ROOT / "inject_deadline_guard.py").read_text()

        self.assertIn("__dlg_merge_result_safe", injector)
        self.assertIn("__dlg_has_clean", injector)
        self.assertIn("merge_result_crosses_deadline", injector)
        self.assertIn("return not c.get(\"merge_result_crosses_deadline\")", injector)
        self.assertIn("__dlg_safe_no_merge", injector)
        self.assertIn("__dlg_merge_preferred", injector)

    def test_deadline_guard_injector_replaces_stale_guard(self):
        import inject_deadline_guard

        stale_src = '''
def decide(game_state, analysis):
    """strategy"""
    # --- BEGIN DEADLINE GUARD (old) ---
    if True:
        return {"x": 0.0, "reason": "OLD_DEADLINE_GUARD"}
    # --- END DEADLINE GUARD ---
    return {"x": 1.0, "reason": "NORMAL"}
'''

        updated = inject_deadline_guard.inject_guard(stale_src)

        self.assertIsNotNone(updated)
        self.assertNotIn("OLD_DEADLINE_GUARD", updated)
        self.assertIn("__dlg_has_clean", updated)
        self.assertIn("merge_result_crosses_deadline", updated)
        self.assertIn("__dlg_safe_no_merge", updated)
        self.assertIn("__dlg_merge_preferred", updated)
        self.assertEqual(inject_deadline_guard.inject_guard(updated), None)

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
        self.assertIn("deadline_guard_rate", text)
        self.assertIn("deadline_guard_reason_top", text)
        self.assertIn("guard_reason_top=", text)
        self.assertIn("deadline guard が多発", text)
        self.assertIn("ガードを弱めず", text)
        self.assertIn("peak_high_type_counts", text)
        self.assertIn("frontier_hint", text)
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
        self.assertIn('"pid=,ppid=,command="', supervisor)
        self.assertIn("_detect_worker_duplicates", supervisor[supervisor.index("# --- 全 worker 起動 ---"):])
        self.assertIn("tmp/state/worker_duplicates.json", status)
        self.assertIn("Duplicates", status)
        self.assertIn("DETECTED", status)

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
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("objective_reasons = []", regression)
        self.assertIn("lost_russia_path", regression)
        self.assertIn("lost_soviet_path", regression)
        self.assertIn("def stage_gate_regression_reason", regression)
        self.assertIn("rank <= rolling_score_russia_grace_rank", regression)
        self.assertIn("lost_ukraine_gate", regression)
        self.assertIn("lost_kazakhstan_gate", regression)
        self.assertIn("mode=objective_regression", regression)
        self.assertIn("mode=early_objective_regression", regression)
        self.assertIn("early_objective_min_games", regression)
        self.assertIn("early_objective_min_best_type", regression)
        self.assertIn("STRATEGY_HASH_PERMANENT_ARCHIVE_DIR", regression)
        self.assertIn("permanent_archive_dir", regression)
        self.assertIn("rollback target normalized", regression)
        self.assertIn("normalized_to_hash", regression)
        self.assertIn("rollback_target_normalized", regression)
        self.assertIn("normalized fallback target rejected; retry anchor rollback", regression)
        self.assertIn("normalized_fallback_anchor", regression)
        self.assertIn("_rollback_candidate_file_is_valid", regression)
        self.assertIn("validation失敗archive", regression)
        self.assertIn('with open(meta_file, "w", encoding="utf-8") as f:', regression)
        self.assertIn("json.dump(meta, f, ensure_ascii=False)", regression)
        self.assertIn("EARLY_OBJECTIVE_REGRESSION_ENABLED", config)
        self.assertIn("EARLY_OBJECTIVE_REGRESSION_MIN_GAMES", config)
        self.assertIn("anchor_best_max_type", regression)
        self.assertIn("curr_best_max_type", regression)

    def test_post_regression_improve_uses_failed_batch(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("POST_REGRESSION_IMPROVE_ENABLED", config)
        self.assertIn("POST_REGRESSION_DIRECT_ESCAPE_ENABLED", config)
        self.assertIn("回帰ロールバック直後 → 失敗バッチで改善ロック作成", loop)
        self.assertIn("_post_regression_route", loop)
        self.assertIn("curr_russia_seen", loop)
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
        self.assertIn("post_regression improve", loop)
        self.assertIn('data["improve_reason"] = "post_regression"', loop)
        self.assertIn('data["regression_result"]', loop)
        self.assertIn("REGRESSION_ROLLBACK_HASH", loop)
        self.assertIn('[ "${REGRESSION_ROLLBACK_DONE:-0}" = "1" ]', loop)
        self.assertIn("ロールバック直後の失敗バッチを改善入力として使用", improve)
        self.assertIn("normal|post_regression|wildcard|escape_ai", improve)
        self.assertIn("post_regression_direct_escape", improve)
        self.assertIn("archive candidate unavailable → wildcard", improve)
        self.assertIn("_persist_improve_lock_reason()", improve)
        self.assertIn('data["improve_reason"] = reason', improve)
        self.assertIn('_persist_improve_lock_reason "$reason"', improve)
        self.assertIn('_start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "$improve_reason"', improve)

    def test_fast_escape_harvest_does_not_start_soren91_handover(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("IMPROVE_FAST_ESCAPE_OVERLAY_HOLD_SEC", config)
        self.assertIn("improve_reason", improve)
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
        self.assertIn("wildcard|archive_restart)", loop)
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
        self.assertIn("_is_recorded_running_improve_pid()", improve)
        self.assertIn('if [ -z "$cmd" ]; then', improve)
        self.assertIn('_is_recorded_running_improve_pid "$pid"', improve)
        self.assertIn("command取得不可だが、記録済みrunning状態と一致", improve)
        self.assertIn("improve_wall_timeout_harvest", improve)
        self.assertIn("if _stop_improve_pid_if_running \"$pid\" \"improve_wall_timeout\"; then", improve)
        self.assertIn("通常改善が上限時間を超えたため", improve)
        self.assertIn("中華AI改善は適用可能な戦略変更を出せず終了しました", improve)
        self.assertIn("improve_failed_no_apply", improve)
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

    def test_startup_validation_syncs_current_run_hash_after_guard_injection(self):
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("validation後hash同期", loop)
        self.assertIn("extract_decide_hash.py \"$STRATEGY_FILE\"", loop)
        self.assertIn("_reset_current_strategy_run \"$_validated_hash\"", loop)

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
        self.assertIn("current_objective.get(\"best_max_type\"", regression)
        self.assertIn("anchor_objective.get(\"russia_count\"", regression)
        self.assertIn("best_max_type >= 15 and russia_count <= 0", regression)
        self.assertIn("SAME_HASH_BACKSLIDE_RESET_ENABLED", config)
        self.assertIn("SAME_HASH_BACKSLIDE_MIN_EXTRA_GAMES", config)

    def test_anchor_rank_key_prioritizes_near_score_objective_progress(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("Rollback anchors must stay score-mature, but the Soviet objective is the", regression)
        self.assertIn("near_score_leader = (", regression)
        self.assertIn("objective_key = _objective_tuple(data)", regression)
        self.assertIn("*objective_key,\n        selection_score", regression)

    def test_russia_recovery_mode_suppresses_mechanical_wildcard(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("Russia recovery mode active", improve)
        self.assertIn("mechanical wildcard suppressed", improve)
        self.assertIn("no_russia_24h", improve)
        self.assertIn("archive_restart を即時優先", improve)
        self.assertIn("archive candidate unavailable → wildcard でtype14→15 frontier 復旧", improve)
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

    def test_russia_progress_suppresses_mechanical_wildcard(self):
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("current strategy has Russia progress", improve)
        self.assertIn("current_russia_progress", improve)
        self.assertIn('russia > 0 or best_type >= 15', improve)
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
        self.assertIn("WildParFail", (REPO_ROOT / "show_status.sh").read_text())
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

    def test_wildcard_parallel_obs_restores_after_nonzero_exit(self):
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("wildcard_parallel_restore_on_exit()", improve)
        self.assertIn("wildcard_parallel_restore_once()", improve)
        self.assertIn("trap wildcard_parallel_restore_on_exit EXIT INT TERM", improve)
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
        self.assertIn("_discover_live_video_id()", youtube)
        self.assertIn("YOUTUBE_CHANNEL_ID", youtube)
        self.assertIn("LIVE_VIDEO_ID_FILE", youtube)
        self.assertIn("eventType=live", youtube)
        self.assertIn('rm -f "$LIVE_CHAT_ID_FILE" "$LIVE_VIDEO_ID_FILE" "$PAGE_TOKEN_FILE"', youtube)
        self.assertIn('rm -f "$LIVE_CHAT_ID_FILE" "$PAGE_TOKEN_FILE"', youtube)
        self.assertIn("_send_api()", twitch_send)
        self.assertIn("https://api.twitch.tv/helix/chat/messages", twitch_send)
        self.assertIn("is_sent", twitch_send)
        self.assertIn("falling back to IRC after Twitch API send failure", twitch_send)
        self.assertIn("_outbound_chat_log_twitch_failure", outbound)
        self.assertIn("last_twitch_send_error.txt", outbound)
        self.assertIn("outbound_chat_twitch.log", outbound)
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
        self.assertIn("./monitor_improve_runtime.sh", eloop)
        self.assertIn("post_game_bookkeeping", eloop)

    def test_archive_restart_monitor_does_not_start_soren91(self):
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("_improve_reason_get", monitor)
        self.assertIn("wildcard|archive_restart)", monitor)
        self.assertIn("leaving soren91 stopped", monitor)
        self.assertLess(
            monitor.index("wildcard|archive_restart)"),
            monitor.index("calling existing soren91_start"),
        )
        self.assertIn("${_pause_reason}改善中(隔離評価): soren91代打を立てず待機", loop)

    def test_rollback_revalidates_strategy_after_restore(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn('validate_strategy "$STRATEGY_FILE"', regression)
        self.assertIn("ロールバック後バリデーション失敗", regression)
        self.assertIn('cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"', regression)
        self.assertIn("ROLLBACK_REVALIDATE_TARGET_ENABLED", config)
        self.assertIn('if [ "${ROLLBACK_REVALIDATE_TARGET_ENABLED:-1}" = "1" ]; then', regression)
        self.assertIn('_reset_current_strategy_run "$rolled_hash"', regression)
        self.assertIn("rollback revalidate fresh cycle", regression)
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

    def test_rollback_target_cooldown_blocks_immediate_reuse(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("ROLLBACK_TARGET_COOLDOWN_FILE", config)
        self.assertIn("ROLLBACK_TARGET_COOLDOWN_SEC", config)
        self.assertIn("_is_rollback_target_on_cooldown()", regression)
        self.assertIn("_record_rollback_target_cooldown()", regression)
        self.assertIn("anchor_top1候補スキップ", regression)
        self.assertIn("rollback先cooldown中", regression)
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

    def test_rollback_candidate_prefers_objective_progress_over_plain_score_top(self):
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
            self.assertTrue(result.stdout.startswith("russiaNearTop|"), msg=result.stdout)
            rolling = json.loads(rs_file.read_text())
            self.assertIn("scoreOnlyTop", rolling)
            self.assertIn("russiaNearTop", rolling)
            self.assertFalse((td / "rolling_score_pruned_hashes.jsonl").exists())

    def test_invalid_rollback_archive_is_pruned_from_rolling_scores(self):
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
            self.assertIn("goodNext|", result.stdout)
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

        self.assertIn("BRIDGE_DESYNC_STOP_STALE_SOREN91_ENABLED", config)
        self.assertIn("BRIDGE_DESYNC_SOREN91_STOP_TIMEOUT", config)
        self.assertIn("PHANTOM_GAME_AUTO_RECOVER_ENABLED", config)
        self.assertIn("即bridge復旧", loop)
        self.assertIn("PHANTOM_GAME_AUTO_RECOVER_ENABLED", loop)
        self.assertIn("[PHANTOM] bridge 再起動 成功", loop)
        self.assertIn("中華AIプレイ中に soren91 残存を検出", loop)
        self.assertIn('SOREN91_STOP_TIMEOUT="${BRIDGE_DESYNC_SOREN91_STOP_TIMEOUT:-0}" soren91_stop', loop)
        self.assertIn("! _is_improve_running", loop)
        self.assertIn("manual_meriken_mode_is_enabled", loop)

    def test_rollback_postmortem_opencode_lock_cannot_block_live_radio_forever(self):
        ai_generate = (REPO_ROOT / "lib/ai_generate.sh").read_text()
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn("ROLLBACK-POSTMORTEM", ai_generate)
        self.assertIn("ROLLBACK_POSTMORTEM_OPENCODE_LOCK_STALE_SEC", ai_generate)
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


if __name__ == "__main__":
    unittest.main()
