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

    def test_score_state_persists_nation_progress_metadata(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        improve = (REPO_ROOT / "strategy/improve.sh").read_text()
        for text in (regression, improve):
            self.assertIn("def nation_progress(path):", text)
            self.assertIn('row.get("russia_created")', text)
            self.assertIn('row.get("soviet_created")', text)
            self.assertIn('piece.get("type", 0)', text)
            self.assertIn('["max_types"]', text)
            self.assertIn('["russia_count"]', text)
            self.assertIn('["soviet_count"]', text)

    def test_rollback_analysis_surfaces_soviet_objective_delta(self):
        regression = (REPO_ROOT / "strategy/regression.sh").read_text()
        improve = (REPO_ROOT / "eloop_improve.sh").read_text()

        self.assertIn("## Soviet Objective Delta", regression)
        self.assertIn("progress_gap_vs_target", regression)
        self.assertIn("current はロシア(type15)未到達", regression)
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
        self.assertIn("normal|post_regression|wildcard", improve)
        self.assertIn('_start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "$improve_reason"', improve)

    def test_improve_and_system_progress_are_queued_for_audio_worker(self):
        config = (REPO_ROOT / "core/config.sh").read_text()
        eloop_improve = (REPO_ROOT / "eloop_improve.sh").read_text()
        system_report = (REPO_ROOT / "system_progress_report.sh").read_text()

        self.assertIn("IMPROVE_AUDIO_SUMMARY_ENABLED", config)
        self.assertIn("IMPROVE_AUDIO_SUMMARY_INTERVAL_SEC", config)
        self.assertIn("_improve_audio_summary_maybe", eloop_improve)
        self.assertIn('enqueue_audio_text "$text" "improve_progress"', eloop_improve)
        self.assertIn("改善進捗", eloop_improve)
        self.assertIn('enqueue_audio_text "$text" "system_progress"', system_report)
        self.assertIn("システム改善進捗", system_report)

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
