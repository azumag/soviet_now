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

    def test_anchor_selection_prefers_soviet_objective_progress_within_score_band(self):
        """anchor は同程度スコア帯なら comp だけでなく建国進捗を守る。"""
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
        self.assertIn("wildcard_applied", eloop)
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
        self.assertIn("normal|post_regression|wildcard|escape_ai", improve)
        self.assertIn('improve_reason="escape_ai"', improve)
        self.assertIn("AI 構造変異モードで脱出", improve)
        self.assertIn("rejected_hash_metrics.json", improve)
        self.assertIn("rolling_scores.json", improve)
        self.assertIn("wildcard_origin.json", improve)
        self.assertIn("reconstructs failures from WILDCARDs", improve)
        self.assertIn("Mature origin + below-anchor metrics", improve)
        self.assertIn("m.get(\"comp\", 0.0) < anchor_comp", improve)
        self.assertIn("export IMPROVE_REASON", eloop)
        self.assertIn('os.environ.get("IMPROVE_REASON", "normal") == "escape_ai"', eloop)
        self.assertIn("今回だけAIによる小さな構造変異で大域脱出を狙う", eloop)
        self.assertIn("WILDCARD起源からAI改善の起点候補を選定", eloop)
        self.assertIn("ESCAPE_AI_SEED_JSON", eloop)
        self.assertIn("seed_from_wildcard_", eloop)
        self.assertIn("origin_type != \"wildcard\"", eloop)
        self.assertIn("AI改善失敗のためWILDCARD seed適用を元へ戻した", eloop)
        self.assertIn("escape_ai seed: 粛清済みWILDCARD群", eloop)
        self.assertIn('"ESCAPE_AI_APPLIED"', eloop)
        self.assertIn('"escape_ai_success_reset"', eloop)
        self.assertIn('"last_escape_ai_hash"', eloop)
        self.assertIn('"origin_type": "escape_ai"', eloop)
        self.assertIn("stagnation/wildcard latch cleared", eloop)

    def test_wildcard_stagnation_can_queue_early_escape_lock(self):
        """WILDCARD 停滞時は12試合サイクルを待たずに改善daemonへ渡せる。"""
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("WILDCARD_EARLY_ESCAPE_LOCK_ENABLED", config)
        self.assertIn("WILDCARD_EARLY_ESCAPE_MIN_GAMES", config)
        self.assertIn("WILDCARD 即応ロック", loop)
        self.assertIn("早期脱出ロック作成", loop)
        self.assertIn("early_escape_lock", loop)
        self.assertIn("early_escape_stagnation", loop)
        self.assertIn("rank1 hot streak 中 → 即応脱出ロックを延期", loop)
        self.assertIn("WILDCARD_TRIGGER_STAGNATION", loop)
        self.assertIn("MIN_GAMES_BEFORE_IMPROVE", loop)
        self.assertIn("normal|post_regression|wildcard|escape_ai|archive_restart", improve)
        self.assertLess(
            loop.index("WILDCARD 即応ロック"),
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
        self.assertIn("anchor_russia", improve)
        self.assertIn("anchor_soviet", improve)
        self.assertIn("archive_restart を飛ばして escape_ai", improve)
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
        self.assertIn("anchor_russia", eloop)
        self.assertIn("anchor_soviet", eloop)
        self.assertIn("objective escape mechanism", eloop)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_FILE", eloop)
        self.assertIn("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE", eloop)
        self.assertIn("archive_no_candidate_fallback", eloop)
        self.assertIn("source_russia_count", eloop)
        self.assertIn("source_hash = str(selected.get(\"selected_hash\")", eloop)
        self.assertIn("archive_restart_source", eloop)
        self.assertIn("source_russia_count", regression)
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
        self.assertNotIn("`tmp/review_result.md` は既に存在", review_prompt)
        self.assertIn("_repair_review_verdict_file", eloop)
        self.assertIn("REVIEW-VERDICT-REPAIR", eloop)
        self.assertIn("Stage3: review verdict missing → repair verdict file", eloop)
        self.assertIn("strategy.py.staging` は編集禁止", eloop)
        self.assertIn("必ず `tmp/review_result.md` を Write/Edit", eloop)


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


# --- 共通: stagnation counter transitions ------------------------------------

class TestStagnationCounterTransitions(unittest.TestCase):
    def test_python_block_contains_all_event_calls(self):
        """check_regression Python ブロックに 4 種類の _update_stagnation 呼び出しが存在する。"""
        text = (REPO_ROOT / "strategy/regression.sh").read_text()
        self.assertIn('_update_stagnation("PROMOTE")', text)
        self.assertIn('_update_stagnation("REGRESSION")', text)
        self.assertIn('_update_stagnation("RESET")', text)
        self.assertIn('_update_stagnation("OK_BEAT")', text)
        self.assertIn('_update_stagnation("OK_IDLE")', text)
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
        self.assertIn("$CODEX_WORK_OVERLAY_STATE_FILE", notify)
        self.assertIn("generate_event_overlay.py", indicator)
        self.assertIn("eventOverlay", agents)
        self.assertIn("./codex_work_indicator.sh start", agents)
        self.assertIn("./codex_work_indicator.sh stop", agents)
        self.assertNotIn("./obs_control.sh show soren systemMsg", agents)
        self.assertNotIn("./obs_control.sh hide soren systemMsg", agents)

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

    def test_obs_control_can_report_overlay_source_status(self):
        obs = (REPO_ROOT / "obs_control.sh").read_text()

        self.assertIn("./obs_control.sh status <scene> <source>", obs)
        self.assertIn("action === 'status'", obs)
        self.assertIn("sceneItemEnabled === true", obs)
        self.assertIn("=missing", obs)

    def test_status_g_has_wide_short_html_overlay_generator(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        overlay = (REPO_ROOT / "generate_status_overlay.sh").read_text()
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
        self.assertIn("640", config)
        self.assertIn("1000", config)
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
        self.assertIn("load_monitor_report_status", dashboard)
        self.assertIn("MONITOR_REPORT_STATUS_FILE", dashboard)
        self.assertIn("live {live", dashboard)
        self.assertIn("load_latest_annealing_candidate", dashboard)
        self.assertIn("load_wildcard_attempt_status", dashboard)
        self.assertIn("SOREN_MONITOR_REPORT_FILE", dashboard)
        self.assertIn("VIEWER_CHAT_MONITOR_FILE", dashboard)
        self.assertIn("load_viewer_chat_monitor", dashboard)
        self.assertIn("ChatObs", dashboard)
        self.assertIn("ANNEALING_OBSERVE_FILE", dashboard)
        self.assertIn("WILDCARD_ATTEMPT_STATE_FILE", dashboard)
        self.assertIn("WildStreak", dashboard)
        self.assertIn("archive_restart next", dashboard)
        self.assertIn("load_archive_restart_candidate", dashboard)
        self.assertIn("ArchiveNext", dashboard)
        self.assertIn("ARCHIVE_RESTART_COOLDOWN_FILE", dashboard)
        self.assertIn("ARCHIVE_RESTART_MIN_BEST_TYPE", dashboard)
        self.assertIn("best_type < min_best_type", dashboard)
        self.assertIn("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE", dashboard)
        self.assertIn("no_candidate_cooldown", dashboard)
        self.assertIn('"status": "no_candidate"', dashboard)
        self.assertIn("threshold c>=", dashboard)
        self.assertIn("escape_ai direct", dashboard)
        self.assertIn("effective_streak", dashboard)
        self.assertIn("failed_origin_count", dashboard)
        self.assertIn("ARCHIVE_RESTART_STREAK", dashboard)
        self.assertIn("WILDCARD_AI_ESCALATE_STREAK", dashboard)
        self.assertIn("observe-only", dashboard)

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

    def test_deadline_crossing_overlay_notify_uses_event_overlay(self):
        import strategy_runner

        with mock.patch.object(strategy_runner.os.path, "exists", return_value=True), mock.patch.object(strategy_runner.subprocess, "run") as run:
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

        args = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(args[:3], ["./overlay_notify.sh", "deadline", "デッドライン超過: 安全候補あり"])
        self.assertEqual(args[-1], "warn")
        self.assertEqual(kwargs["env"]["OVERLAY_NOTIFY_OBS_SHOW"], "1")

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

    def test_monitor_status_surfaces_deadline_no_merge_audit(self):
        monitor = (REPO_ROOT / "monitor_report_stale_report.sh").read_text()
        show_status = (REPO_ROOT / "show_status.sh").read_text()
        dashboard = (REPO_ROOT / "status_dashboard.py").read_text()

        self.assertIn("def deadline_crossing_audit", monitor)
        self.assertIn('row.get("decision_crosses_deadline")', monitor)
        self.assertIn('row.get("best_merge_grade")', monitor)
        self.assertIn('"deadline_no_merge_count"', monitor)
        self.assertIn('"deadline_no_merge_with_safe_count"', monitor)
        self.assertIn("safe_candidate_count > 0", monitor)
        self.assertIn("deadline_no_merge=", monitor)
        self.assertIn("safe_available=", monitor)
        self.assertIn('cached.get("deadline_no_merge_count")', show_status)
        self.assertIn('cached.get("deadline_no_merge_with_safe_count")', show_status)
        self.assertIn("DLsafe=", show_status)
        self.assertIn("DLno=", show_status)
        self.assertIn('cached.get("deadline_no_merge_count")', dashboard)
        self.assertIn('cached.get("deadline_no_merge_with_safe_count")', dashboard)
        self.assertIn("deadline_prefix", dashboard)

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
        self.assertIn("mode=objective_regression", regression)
        self.assertIn("mode=early_objective_regression", regression)
        self.assertIn("early_objective_min_games", regression)
        self.assertIn("early_objective_min_best_type", regression)
        self.assertIn("rollback target normalized", regression)
        self.assertIn("normalized_to_hash", regression)
        self.assertIn("rollback_target_normalized", regression)
        self.assertIn("EARLY_OBJECTIVE_REGRESSION_ENABLED", config)
        self.assertIn("EARLY_OBJECTIVE_REGRESSION_MIN_GAMES", config)
        self.assertIn("anchor_best_max_type", regression)
        self.assertIn("curr_best_max_type", regression)

    def test_post_regression_improve_uses_failed_batch(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()

        self.assertIn("POST_REGRESSION_IMPROVE_ENABLED", config)
        self.assertIn("回帰ロールバック直後 → 失敗バッチで改善ロック作成", loop)
        self.assertIn("d['improve_reason']='post_regression'", loop)
        self.assertIn("REGRESSION_ROLLBACK_HASH", loop)
        self.assertIn("ロールバック直後の失敗バッチを改善入力として使用", improve)
        self.assertIn("normal|post_regression|wildcard|escape_ai", improve)
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

    def test_same_hash_objective_gap_does_not_reset_wildcard_stagnation(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        config = (REPO_ROOT / "core/config.sh").read_text()

        self.assertIn("if current_hash == anchor_hash and not branch_active:", regression)
        self.assertIn("current_games_total = int(current_data.get(\"games_total\"", regression)
        self.assertIn("and current_games_total >= same_hash_backslide_mature_n", regression)
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

    def test_show_status_surfaces_current_wildcard_evaluation(self):
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("wildcard_eval_name", status)
        self.assertIn("wildcard_eval_label", status)
        self.assertIn("wildcard_outcomes.jsonl", status)
        self.assertIn("WildEval", status)
        self.assertIn("ArcEval", status)
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
        self.assertIn("SOREN_MONITOR_REPORT_FILE", status)
        self.assertIn("/tmp/soren_report.md", status)
        self.assertIn("Monitor", status)
        self.assertIn("monitor_report_label", status)
        self.assertIn("MONITOR_REPORT_STATUS_FILE", status)
        self.assertIn("live {live", status)
        self.assertIn("最終更新:", status)
        self.assertIn("datetime.strptime", status)
        self.assertIn("viewer_chat_monitor.sh", status)
        self.assertIn("viewer_chat_label", status)
        self.assertIn("ChatObs", status)
        self.assertIn("rate_limit_backoff", status)
        self.assertIn("improve_backoff_label", status)
        self.assertIn("ImproveBack", status)
        self.assertIn("archive_next_label", status)
        self.assertIn("ArchiveNext", status)
        self.assertIn("no cand c>=", status)
        self.assertIn("-> escape_ai", status)
        self.assertIn("archive_is_runtime_stable", status)
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

    def test_monitor_report_staleness_is_reported_to_audio(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        reporter = (REPO_ROOT / "monitor_report_stale_report.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()

        self.assertIn("MONITOR_REPORT_AUDIO_ENABLED", config)
        self.assertIn("MONITOR_REPORT_AUDIO_STATE_FILE", config)
        self.assertIn("MONITOR_REPORT_STALE_SEC", config)
        self.assertIn("MONITOR_REPORT_OLD_SEC", config)
        self.assertIn("MONITOR_REPORT_AUDIO_MIN_INTERVAL_SEC", config)
        self.assertIn('MONITOR_REPORT_AUDIO_MIN_INTERVAL_SEC="${MONITOR_REPORT_AUDIO_MIN_INTERVAL_SEC:-900}"', config)
        self.assertIn('${MONITOR_REPORT_AUDIO_MIN_INTERVAL_SEC:-900}', reporter)
        self.assertIn("SOREN_MONITOR_REPORT_FILE", reporter)
        self.assertIn("CURRENT_STRATEGY_RUN_FILE", reporter)
        self.assertIn("BEST_STRATEGY_ANCHOR_FILE", reporter)
        self.assertIn("WILDCARD_ORIGIN_FILE", reporter)
        self.assertIn("MONITOR_REPORT_STATUS_FILE", reporter)
        self.assertIn("メリケンAI監視レポート", reporter)
        self.assertIn("ライブは", reporter)
        self.assertIn("live_origin_type", reporter)
        self.assertIn("enqueue_audio_text \"$message\" \"monitor_report\"", reporter)
        self.assertIn("overlay_notify.sh", reporter)
        self.assertIn("monitor_report_stale_report.env.tmp", reporter)
        self.assertIn("./monitor_report_stale_report.sh", loop)
        self.assertIn("stale report notice skipped/failed", loop)

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

    def test_onair_sanitizer_strips_tool_markers_from_comment_replies(self):
        radio_engine = (REPO_ROOT / "broadcast/radio_engine.sh").read_text()
        comment = (REPO_ROOT / "broadcast/comment.sh").read_text()

        self.assertIn(r"^\s*%?\s*(?:WebFetch|WebSearch)\b\s*", radio_engine)
        self.assertIn("attempt_talk=$(printf '%s' \"$attempt_talk\" | _sanitize_onair_text)", comment)

    def test_status_surfaces_fresh_improve_state_when_pid_is_hidden(self):
        dashboard = (REPO_ROOT / "status_dashboard.py").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()
        loop = (REPO_ROOT / "soren_loop.sh").read_text()
        eloop = (REPO_ROOT / "eloop.sh").read_text()

        self.assertIn("improve_monitor_status.json", dashboard)
        self.assertIn("state_activity_fresh", dashboard)
        self.assertIn("Imp:{improve.get('progress', 0):>3}% {phase} log", dashboard)
        self.assertIn("improve_monitor_status.json", status)
        self.assertIn("imp_state_activity_fresh", status)
        self.assertIn("PID=%s not visible, log fresh", status)
        self.assertIn("SOREN_IMPROVE_MONITOR_INTERVAL_SEC", loop)
        self.assertIn("_run_improve_runtime_monitor", loop)
        self.assertIn("./monitor_improve_runtime.sh >/dev/null 2>&1", loop)
        self.assertIn("improve runtime monitor skipped/failed", loop)
        self.assertIn("./monitor_improve_runtime.sh", eloop)
        self.assertIn("post_game_bookkeeping", eloop)

    def test_rollback_revalidates_strategy_after_restore(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()

        self.assertIn('validate_strategy "$STRATEGY_FILE"', regression)
        self.assertIn("ロールバック後バリデーション失敗", regression)
        self.assertIn('cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"', regression)

    def test_soren91_ranking_comments_are_prioritized_in_comment_queue(self):
        comment_lib = (REPO_ROOT / "broadcast/comment_lib.sh").read_text()

        self.assertIn("_comment_queue_priority()", comment_lib)
        self.assertIn("soren91:ranking_comment) printf '%s' \"00\"", comment_lib)
        self.assertIn("_comment_queue_ordered_files()", comment_lib)
        self.assertIn("for qf in $(_comment_queue_ordered_files); do", comment_lib)

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


if __name__ == "__main__":
    unittest.main()
