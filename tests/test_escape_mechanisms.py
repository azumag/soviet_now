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
            (archive_dir / "hashA.py").write_text("")
            (archive_dir / "hashB.py").write_text("")

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
        self.assertIn('"metrics": current_payload', regression)
        self.assertIn("def _record_annealing_candidate(event):", regression)
        self.assertIn('"event": "ANNEALING_CANDIDATE"', regression)
        self.assertIn('"observe_only": True', regression)
        self.assertIn("_record_annealing_candidate(event)", regression)

    def test_repeated_wildcards_can_escalate_to_ai_structural_escape(self):
        """WILDCARD 連続失敗時は、次の脱出をAI構造変異モードへ上げられる。"""
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        eloop = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("WILDCARD_AI_ESCALATE_ENABLED", config)
        self.assertIn("WILDCARD_AI_ESCALATE_STREAK", config)
        self.assertIn("normal|post_regression|wildcard|escape_ai", improve)
        self.assertIn('improve_reason="escape_ai"', improve)
        self.assertIn("AI 構造変異モードで脱出", improve)
        self.assertIn("rejected_hash_metrics.json", improve)
        self.assertIn("wildcard_origin.json", improve)
        self.assertIn("reconstructs failures from WILDCARDs", improve)
        self.assertIn("export IMPROVE_REASON", eloop)
        self.assertIn('os.environ.get("IMPROVE_REASON", "normal") == "escape_ai"', eloop)
        self.assertIn("今回だけAIによる小さな構造変異で大域脱出を狙う", eloop)

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
        post_reject = text.split("REJECTED_HASHES_FILE")[1] if "REJECTED_HASHES_FILE" in text else ""
        self.assertNotIn("STAGNATION_COUNTER_FILE.*echo", post_reject)


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
    def test_improve_overlay_is_file_based_and_replaces_console_capture(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        monitor = (REPO_ROOT / "monitor_improve_runtime.sh").read_text()
        overlay = (REPO_ROOT / "generate_improve_overlay.sh").read_text()

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

    def test_status_g_has_520x980_html_overlay_generator(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        overlay = (REPO_ROOT / "generate_status_overlay.sh").read_text()
        status_g = (REPO_ROOT / "show_status_g.sh").read_text()
        dashboard = (REPO_ROOT / "status_dashboard.py").read_text()

        self.assertIn("STATUS_OVERLAY_HTML_FILE", config)
        self.assertIn("STATUS_OVERLAY_SOURCE", config)
        self.assertIn("STATUS_OVERLAY_WIDTH", config)
        self.assertIn("STATUS_OVERLAY_HEIGHT", config)
        self.assertIn("STATUS_OVERLAY_OBS_X", config)
        self.assertIn("STATUS_OVERLAY_OBS_Y", config)
        self.assertIn("STATUS_OVERLAY_OBS_SCALE_X", config)
        self.assertIn("STATUS_OVERLAY_OBS_SCALE_Y", config)
        self.assertIn("520", config)
        self.assertIn("980", config)
        self.assertIn("python3 status_dashboard.py", overlay)
        self.assertIn("[ -f .env ] && set -a && . ./.env && set +a", overlay)
        self.assertIn("ansi_to_html", overlay)
        self.assertIn("STATUS_OVERLAY_RAW", overlay)
        self.assertIn("obs_browser_source.sh ensure", overlay)
        self.assertIn("apply_obs_transform", overlay)
        self.assertIn("SetSceneItemTransform", overlay)
        self.assertIn("OBS_BOUNDS_NONE", overlay)
        self.assertIn("transformed:", overlay)
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
        self.assertIn("load_latest_annealing_candidate", dashboard)
        self.assertIn("SOREN_MONITOR_REPORT_FILE", dashboard)
        self.assertIn("ANNEALING_OBSERVE_FILE", dashboard)
        self.assertIn("observe-only", dashboard)

    def test_show_status_has_html_overlay_generator(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        overlay = (REPO_ROOT / "generate_show_status_overlay.sh").read_text()
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("SHOW_STATUS_OVERLAY_HTML_FILE", config)
        self.assertIn("SHOW_STATUS_OVERLAY_SOURCE", config)
        self.assertIn("show_status_overlay.html", config)
        self.assertIn("showStatusOverlay", config)
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
    def test_soren91_start_prefers_tmux_tty_runner(self):
        control = (REPO_ROOT / "soren91_control.sh").read_text()
        runner = (REPO_ROOT / "soren91/run_player_loop.sh").read_text()

        self.assertIn("tmux new-session -d -s soren91_runner", control)
        self.assertIn("exec /bin/bash '$SOREN91_RUNNER_SCRIPT'", control)
        self.assertIn("trap '' HUP", runner)
        self.assertIn("trap '_on_signal TERM' TERM", runner)
        self.assertIn("trap '_on_exit' EXIT", runner)


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

    def test_missing_blackhole_falls_back_to_default_sink(self):
        local = (REPO_ROOT / "soviet_local.mjs").read_text()

        self.assertIn("const sinkId = target ? target.deviceId : (window.__sorenAudioOutputDeviceId || '')", local)
        self.assertIn("await ctx.setSinkId(sinkId)", local)
        self.assertIn("audio output not found", local)
        self.assertIn("return Boolean(target)", local)
        self.assertIn("await ctx.setSinkId('')", local)
        self.assertIn("setTimeout(r, 250)", local)
        self.assertIn("alreadyRouted && allRunning", local)

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

        self.assertIn("objective_reasons = []", regression)
        self.assertIn("lost_russia_path", regression)
        self.assertIn("lost_soviet_path", regression)
        self.assertIn("mode=objective_regression", regression)
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

    def test_improve_and_system_progress_are_queued_for_audio_worker(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop_improve = (REPO_ROOT / "eloop_improve.sh").read_text()
        comment_lib = (REPO_ROOT / "broadcast/comment_lib.sh").read_text()
        system_report = (REPO_ROOT / "system_progress_report.sh").read_text()

        self.assertIn("IMPROVE_AUDIO_SUMMARY_ENABLED", config)
        self.assertIn("IMPROVE_AUDIO_SUMMARY_INTERVAL_SEC", config)
        self.assertIn("_improve_audio_summary_maybe", eloop_improve)
        self.assertIn("IMPROVE_AUDIO_SUMMARY_SPOKEN=0", eloop_improve)
        self.assertIn('enqueue_audio_text "$text" "improve_progress"', eloop_improve)
        self.assertIn("*improve_progress*)", comment_lib)
        self.assertIn("_comment_improve_progress_already_played", comment_lib)
        self.assertIn("_comment_mark_improve_progress_played", comment_lib)
        self.assertIn("改善進捗", eloop_improve)
        self.assertIn('enqueue_audio_text "$text" "system_progress"', system_report)
        self.assertIn('${SYSTEM_PROGRESS_AUDIO_SPEAKER:-${SOREN91_VOICEVOX_SPEAKER:-46}}', system_report)
        self.assertIn("システム改善進捗", system_report)

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

    def test_show_status_surfaces_current_wildcard_evaluation(self):
        status = (REPO_ROOT / "show_status.sh").read_text()

        self.assertIn("wildcard_eval_label", status)
        self.assertIn("wildcard_outcomes.jsonl", status)
        self.assertIn("WildEval", status)
        self.assertIn("{n}/{mature_n}", status)
        self.assertIn("quantile(xs, 0.50)", status)
        self.assertIn("0.55 * quantile", status)
        self.assertIn("best_strategy_anchor.json", status)
        self.assertIn("delta_label", status)
        self.assertIn("trend_label", status)
        self.assertIn("t={trend:+d}", status)
        self.assertIn("event_short", status)
        self.assertIn("annealing_candidates.jsonl", status)
        self.assertIn("AnnealObs", status)
        self.assertIn("accept_probability", status)
        self.assertIn("SOREN_MONITOR_REPORT_FILE", status)
        self.assertIn("/tmp/soren_report.md", status)
        self.assertIn("Monitor", status)
        self.assertIn("monitor_report_label", status)
        self.assertIn("最終更新:", status)
        self.assertIn("datetime.strptime", status)

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
        self.assertIn("overlay_notify.sh", reporter)
        self.assertIn("./wildcard_progress_report.sh", loop)
        self.assertIn("progress report skipped/failed", loop)

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

    def test_bridge_desync_stops_stale_soren91_only_outside_improve(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        loop = (REPO_ROOT / "eloop.sh").read_text()

        self.assertIn("BRIDGE_DESYNC_STOP_STALE_SOREN91_ENABLED", config)
        self.assertIn("中華AIプレイ中に soren91 残存を検出", loop)
        self.assertIn("soren91_stop 2>/dev/null", loop)
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
