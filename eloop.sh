#!/bin/bash
# eloop.sh - 1試合のゲームプレイと後処理
#
# soren_loop.sh から毎試合 source される。
# AI による改善対象 — このファイルが書き換わっても、次の source で反映される。
#
# 提供する関数:
#   play_one_game()          — 1試合プレイ、結果をグローバル変数に格納
#   post_game_bookkeeping()  — スコア記録、バージョン保存、git commit 等
#   handle_soviet_celebration() — ソ連建国祝賀
#   prepare_next_game()      — retry送信 or MOVE待ち
#   _handle_decide_exception_recovery() — decide例外時のロールバック/再試行
#
# 前提: eloop_lib.sh が source 済みであること

PLAY_RECOVERED_RETRY_RC=75

_stop_improvement_for_runtime_recovery() {
	local running_pid=0
	running_pid=$(_find_live_improve_pid 2>/dev/null || echo 0)
	if [ "${running_pid:-0}" -ne 0 ]; then
		log "[RECOVERY] 改善プロセス停止 (PID=$running_pid)"
		if ! _stop_improve_pid_if_running "$running_pid" "runtime_recovery"; then
			log "[RECOVERY] 改善プロセス停止失敗: PID=$running_pid がまだ生存"
		fi
	fi
	if _find_live_improve_pid >/dev/null 2>&1; then
		_sync_improve_state_with_live_process >/dev/null 2>&1 || true
	else
		IMPROVE_PID=0
		_write_improve_state "idle" "0" "" "runtime_recovery" "0" "decide_exception"
	fi
}

_pick_runtime_rollback_candidate() {
	local current_hash="$1"

	if [ -f "tmp/revert_strategy.py" ]; then
		local revert_hash
		revert_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
		if [ -n "$revert_hash" ] && [ "$revert_hash" != "$current_hash" ]; then
			echo "tmp/revert_strategy.py|previous_strategy|$revert_hash"
			return 0
		fi
	fi

	local best_candidate
	best_candidate=$(_pick_best_rollback_candidate "$current_hash")
	if [ -n "$best_candidate" ]; then
		local h comp p50 p25 lcb n file
		IFS='|' read -r h comp p50 p25 lcb n file <<<"$best_candidate"
		echo "${file}|best_comp hash=${h} comp=${comp} p50=${p50} p25=${p25} lcb=${lcb} n=${n}|${h}"
		return 0
	fi

	local vf vh
	while IFS= read -r vf; do
		[ -f "$vf" ] || continue
		vh=$(python3 extract_decide_hash.py "$vf" 2>/dev/null || echo "")
		[ -z "$vh" ] && continue
		if [ "$vh" != "$current_hash" ]; then
			echo "${vf}|latest_version|${vh}"
			return 0
		fi
	done < <(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null || true)

	return 1
}

_handle_decide_exception_recovery() {
	local err_msg="$1"
	local err_turn="${2:-0}"
	local err_score="${3:-0}"
	local rollback_applied=false
	log "[RECOVERY] decide例外を検出: ${err_msg} (turn=${err_turn}, score=${err_score})"

	_stop_improvement_for_runtime_recovery

	local current_hash rollback_line rollback_file rollback_note rollback_hash
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	rollback_line=$(_pick_runtime_rollback_candidate "$current_hash" || true)
	if [ -n "$rollback_line" ]; then
		IFS='|' read -r rollback_file rollback_note rollback_hash <<<"$rollback_line"
		if [ -f "$rollback_file" ]; then
			cp "$rollback_file" "$STRATEGY_FILE"
			cp "$STRATEGY_FILE" "tmp/revert_strategy.py" 2>/dev/null || true
			local rolled_hash
			local phylo_push_ok=false
			rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
			_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
			rollback_applied=true
			log "[RECOVERY] ロールバック完了: ${rollback_note} (file=${rollback_file}, hash=${rolled_hash:-unknown})"
			git add "$STRATEGY_FILE" tmp/revert_strategy.py 2>/dev/null || true
			if git commit -m "eloop Auto-revert: decide runtime exception (turn=${err_turn}, score=${err_score}, target=${rollback_note})" 2>/dev/null; then
				if git push 2>/dev/null; then
					phylo_push_ok=true
				fi
			fi
			if [ "$phylo_push_ok" = true ]; then
				_post_phyrogenetic_tree_link_to_chat "rollback" "$current_hash" "$rolled_hash"
			fi
		else
			log "[RECOVERY] ロールバック候補ファイルなし: ${rollback_file}"
		fi
	else
		log "[RECOVERY] ロールバック候補なし（現戦略のまま再試行）"
	fi

	# 戦略が切り替わる可能性が高いため蓄積データは破棄
	_clear_accumulated_data
	_clear_active_branch

	# 進行中ゲームは捨てて、即リトライ
	send_retry

	# ロールバック直後は即改善せず、ロールバック戦略の実績を一定数ためてから改善する
	if [ "$rollback_applied" = true ]; then
		echo "${MIN_GAMES_BEFORE_IMPROVE:-12}" >"$RUNTIME_RECOVERY_GATE_FILE"
		log "[RECOVERY] 改善ゲート設定: ロールバック戦略で ${MIN_GAMES_BEFORE_IMPROVE:-12} 試合蓄積後に改善開始"
	else
		rm -f "$RUNTIME_RECOVERY_GATE_FILE" 2>/dev/null || true
	fi
}

_find_strategy_archive_for_hash() {
	local expected_hash="$1" candidate actual_hash
	[ -n "$expected_hash" ] || return 1
	for candidate in \
		"${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}/${expected_hash}.py" \
		"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}/${expected_hash}.py"; do
		[ -f "$candidate" ] || continue
		actual_hash=$(python3 extract_decide_hash.py "$candidate" 2>/dev/null || echo "")
		if [ "$actual_hash" = "$expected_hash" ]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done
	return 1
}

_strategy_source_has_invalid_structural_wildcard() {
	local source_file="$1"
	[ -f "$source_file" ] || return 1
	grep -Eq 'next_type[[:space:]]*\+[[:space:]]*-1|next_type[[:space:]]*-[[:space:]]*-1' "$source_file"
}

repair_strategy_to_active_branch_head_if_needed() {
	[ "${ACTIVE_BRANCH_STRATEGY_REPAIR_ENABLED:-1}" = "1" ] || return 0
	[ -f "${ACTIVE_BRANCH_FILE:-tmp/state/active_branch.json}" ] || return 0

	local expected_hash fallback_hash current_hash source_file fallback_source actual_hash backup_file abandon_active_branch
	abandon_active_branch=false
	IFS='|' read -r expected_hash fallback_hash <<EOF
$(python3 - "${ACTIVE_BRANCH_FILE:-tmp/state/active_branch.json}" <<'PY' 2>/dev/null || true
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    data = {}
print(str(data.get("head_hash") or "") + "|" + str(data.get("best_hash") or ""))
PY
)
EOF
	[ -n "$expected_hash" ] || return 0
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	[ -n "$current_hash" ] || current_hash="unknown"

	source_file=$(_find_strategy_archive_for_hash "$expected_hash" 2>/dev/null || true)
	if [ -n "$source_file" ] && _strategy_source_has_invalid_structural_wildcard "$source_file"; then
		if [ -n "$fallback_hash" ] && [ "$fallback_hash" != "$expected_hash" ]; then
			fallback_source=$(_find_strategy_archive_for_hash "$fallback_hash" 2>/dev/null || true)
			if [ -n "$fallback_source" ]; then
				log "[STRATEGY-REPAIR] active_branch head ${expected_hash:0:12} has invalid structural wildcard; fallback to best=${fallback_hash:0:12}"
				expected_hash="$fallback_hash"
				source_file="$fallback_source"
				abandon_active_branch=true
			else
				log "[STRATEGY-REPAIR] active_branch head ${expected_hash:0:12} invalid but fallback archive missing: best=${fallback_hash:0:12}"
			fi
		fi
	fi
	[ "$current_hash" != "$expected_hash" ] || return 0

	if [ -z "${source_file:-}" ]; then
		log "[STRATEGY-REPAIR] active_branch head mismatch current=${current_hash:0:12} expected=${expected_hash:0:12} だが archive が見つからないため維持"
		return 0
	fi

	mkdir -p "${TMP_STATE_DIR:-tmp/state}/strategy_repair_backups" 2>/dev/null || true
	backup_file="${TMP_STATE_DIR:-tmp/state}/strategy_repair_backups/pre_active_branch_repair_${current_hash}_${expected_hash}_$(date +%s).py"
	cp "$STRATEGY_FILE" "$backup_file" 2>/dev/null || true
	if ! cp "$source_file" "$STRATEGY_FILE"; then
		log "[STRATEGY-REPAIR] CRITICAL: active_branch head restore copy failed source=$source_file"
		return 1
	fi
	if ! validate_strategy "$STRATEGY_FILE"; then
		log "[STRATEGY-REPAIR] CRITICAL: restored active_branch head failed validation; reverting backup"
		[ -f "$backup_file" ] && cp "$backup_file" "$STRATEGY_FILE" 2>/dev/null || true
		return 1
	fi
	actual_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	if [ "$actual_hash" != "$expected_hash" ]; then
		log "[STRATEGY-REPAIR] CRITICAL: restored hash mismatch expected=${expected_hash:0:12} actual=${actual_hash:0:12}; reverting backup"
		[ -f "$backup_file" ] && cp "$backup_file" "$STRATEGY_FILE" 2>/dev/null || true
		return 1
	fi

	_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$actual_hash" 2>/dev/null || true
	if command -v _seed_current_strategy_run_from_rolling >/dev/null 2>&1 && _seed_current_strategy_run_from_rolling "$actual_hash"; then
		log "[STRATEGY-REPAIR] current_run reseeded from rolling: hash=${actual_hash:0:12}"
	elif command -v _reset_current_strategy_run >/dev/null 2>&1; then
		_reset_current_strategy_run "$actual_hash" || true
		log "[STRATEGY-REPAIR] current_run reset: hash=${actual_hash:0:12}"
	fi
	if command -v _clear_accumulated_data >/dev/null 2>&1; then
		_clear_accumulated_data || true
	fi
	if [ "$abandon_active_branch" = true ] && command -v _clear_active_branch >/dev/null 2>&1; then
		_clear_active_branch || true
	fi
	rm -f "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || true
	log "[STRATEGY-REPAIR] active_branch head restored before game: ${current_hash:0:12} -> ${actual_hash:0:12} source=$source_file"
}

#=== 1試合プレイ ===
play_one_game() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] play_one_gameをスキップ（strategy停止中）"
		LAST_SOVIET="false"
		return 0
	fi

	# ブリッジ(soviet_local.mjs)生存監視＋自動復旧。play_one_game は soren_loop の
	# 全 pause continue (改善中/Meriken/soren91/stop) の後でのみ呼ばれるため
	# pause 安全性は保たれる。eloop.sh は毎周回 re-source されるので
	# 実行中 soren_loop に再起動なしで反映される (hot-reload)。
	if command -v _ensure_bridge_alive >/dev/null 2>&1; then
		if ! _ensure_bridge_alive; then
			log "[BRIDGE] 復旧未完了 → 試合開始を次周回へ延期"
			LAST_SCORE=0
			LAST_TURNS=0
			LAST_RUSSIA="false"
			LAST_RUSSIA_ANNOUNCED="false"
			LAST_SOVIET="false"
			rm -f "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || true
			return "${PLAY_RECOVERED_RETRY_RC:-75}"
		fi
	fi

	# 前試合のダッシュボードを非表示
	./generate_dashboard.sh MOVE || true
	# OBS 側でも dashboard ソースを hide (meriken AI と同じ visibility 制御)
	if [ "${OBS_DASHBOARD_VISIBILITY_ENABLED:-1}" = "1" ]; then
		./obs_control.sh hide "${OBS_DASHBOARD_SCENE:-soren}" "${OBS_DASHBOARD_SOURCE:-dashboard}" >/dev/null 2>&1 &
	fi

	local game_num_display=$((GAME_NUM + 1))
	log ""
	log "── Game #${game_num_display} ──"
	_clear_stale_commands_if_any "before play_one_game"
	repair_strategy_to_active_branch_head_if_needed

	# 試合開始時の strategy.py をスナップショット保存
	# (裏で改善が strategy.py を書き換えても、この試合で使った戦略を正確に保存できる)
	cp "$STRATEGY_FILE" "${STRATEGY_FILE}.game_snapshot"

	# strategy_runner.py で1試合プレイ
	# パイプラインを使わない: bash はパイプライン中 INT trap を遅延するため Ctrl-C が効かない。
	# 代わりに python3 をバックグラウンド実行 + tail -f でリアルタイム表示。
	# phantom ゲーム検知用: strategy_runner 実行前の game_state.json mtime を退避。
	# ブリッジ凍結中は実プレイされず mtime が一切進まない。
	local _pg_state_mtime0 _pg_start_epoch
	_pg_state_mtime0=$(stat -f %m "$GAME_STATE" 2>/dev/null || stat -c %Y "$GAME_STATE" 2>/dev/null || echo 0)
	_pg_start_epoch=$(date +%s)

	local runner_tmpfile
	runner_tmpfile=$(mktemp /tmp/eloop_runner.XXXXXX)
	python3 -u strategy_runner.py >"$runner_tmpfile" 2>&1 &
	local py_pid=$!
	local runner_active_file="${MAIN_STRATEGY_RUNNER_ACTIVE_FILE:-${TMP_STATE_DIR:-tmp/state}/main_strategy_runner_active.json}"
	python3 - "$runner_active_file" "$py_pid" "$game_num_display" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

path, pid, game = sys.argv[1:4]
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump({"pid": int(pid), "game": int(game), "started_at": int(time.time())}, f, ensure_ascii=False)
PY
	tail -n +1 -f "$runner_tmpfile" &
	local tail_pid=$!
	wait "$py_pid"
	local py_rc=$?
	rm -f "$runner_active_file" 2>/dev/null || true
	kill "$tail_pid" 2>/dev/null
	wait "$tail_pid" 2>/dev/null || true
	if [ "$py_rc" -eq 130 ] || [ "$py_rc" -eq 143 ]; then
		log "[SIGNAL] strategy_runner が割り込み終了 (py_rc=$py_rc)"
		rm -f "$runner_tmpfile"
		return 130
	fi
	if [ "$py_rc" -ne 0 ] && grep -q "KeyboardInterrupt" "$runner_tmpfile" 2>/dev/null; then
		log "[SIGNAL] strategy_runner KeyboardInterrupt を検出 (py_rc=$py_rc)"
		rm -f "$runner_tmpfile"
		return 130
	fi
	if [ "$py_rc" -ne 0 ]; then
		log "[RUNNER] WARNING: strategy_runner が異常終了 (py_rc=$py_rc)"
	fi

	# 結果抽出 (v542: tail -n 1 → grep -m1 '^{.*}$' で空行/追加ログ耐性)
	RESULT_JSON=$(sed -n '/^---RESULT---$/,$ p' "$runner_tmpfile" | grep -m1 '^{.*}$' | head -c 10000)
	rm -f "$runner_tmpfile"

	if [ -z "$RESULT_JSON" ]; then
		RESULT_JSON='{"score":0,"turns":0,"state":"UNKNOWN"}'
	fi

	LAST_SCORE=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score',0))" 2>/dev/null || echo 0)
	LAST_TURNS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('turns',0))" 2>/dev/null || echo 0)
	LAST_RUSSIA=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('russia_created',False) else 'false')" 2>/dev/null || echo "false")
	LAST_RUSSIA_ANNOUNCED=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('russia_announced',False) else 'false')" 2>/dev/null || echo "false")
	LAST_SOVIET=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet_created',False) else 'false')" 2>/dev/null || echo "false")
	local runner_error runner_error_msg
	runner_error=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('error',''))" 2>/dev/null || echo "")
	runner_error_msg=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('error_message',''))" 2>/dev/null || echo "")

	# --- Fix3: recovery-taint ガード (guardian/復旧アクターが当該ゲーム中に
	# stuck/hang 復旧を実行した場合、その復旧起点ゲームを評価系に入れない) ---
	# 連言 (codex#5): taint 一致が主トリガ。score==0 等"単独"では正当な早期GOを
	# 捨てない。taint が現ゲーム開始 window に一致 かつ ゲームが復旧で中断された
	# 形跡 (turns==0 / score==0 / state∈UNKNOWN,STOP / game_state未進展) の時のみ。
	local _rr_taint="${RUNTIME_RECOVERY_TAINT_FILE:-tmp/state/runtime_recovery.taint}"
	if [ -f "$_rr_taint" ]; then
		local _rr_ts _rr_gs _rr_now _rr_win
		_rr_now=$(date +%s)
		_rr_win="${RUNTIME_RECOVERY_TAINT_WINDOW:-180}"
		_rr_ts=$(python3 -c "import json,sys;print(int(json.load(open('$_rr_taint')).get('ts',0)))" 2>/dev/null || echo 0)
		_rr_gs=$(python3 -c "import json,sys;print(int(json.load(open('$_rr_taint')).get('game_start_epoch',0)))" 2>/dev/null || echo 0)
		# taint が現ゲーム期間に重なる: taint.ts が現ゲーム開始以降〜now+窓 か、
		# taint.game_start_epoch が現ゲーム開始±窓
		local _rr_match=0
		[ "$_rr_ts" -ge "$(( _pg_start_epoch - _rr_win ))" ] && _rr_match=1
		[ "$_rr_gs" -ne 0 ] && [ "$_rr_gs" -ge "$(( _pg_start_epoch - _rr_win ))" ] \
			&& [ "$_rr_gs" -le "$(( _rr_now + _rr_win ))" ] && _rr_match=1
		local _rr_state _rr_mt_now
		_rr_state=$(echo "$RESULT_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('state',''))" 2>/dev/null || echo "")
		_rr_mt_now=$(stat -f %m "$GAME_STATE" 2>/dev/null || stat -c %Y "$GAME_STATE" 2>/dev/null || echo 0)
		if [ "$_rr_match" -eq 1 ] && { [ "${LAST_TURNS:-0}" -eq 0 ] \
			|| [ "${LAST_SCORE:-0}" -eq 0 ] \
			|| [ "$_rr_state" = "UNKNOWN" ] || [ "$_rr_state" = "STOP" ] \
			|| [ "$_rr_mt_now" = "$_pg_state_mtime0" ]; }; then
			log "[RECOVERY-TAINT] 復旧起点ゲーム検知 (taint一致, turns=${LAST_TURNS:-?} score=${LAST_SCORE:-?} state=$_rr_state) → 後処理/regression を1回スキップ・taint消費"
			rm -f "$_rr_taint" 2>/dev/null || true
			rm -f "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || true
			return "${PLAY_RECOVERED_RETRY_RC:-75}"
		fi
	fi

	# --- phantom ゲームガード (ブリッジ凍結時の幽霊試合で改善データを汚染しない) ---
	# 条件 (AND, 保守的): turns==0 かつ strategy_runner 実行中に game_state.json が
	# 一切更新されない かつ runner 正常終了(py_rc==0) かつ state∈{GAMEOVER,STOP}
	# かつ runner エラー無し。= ブリッジ死亡で wait_for_move が即 STOP を見て
	# 0ターン終了した幽霊試合。戦略ロード失敗/decide例外 (turns=0 だが
	# py_rc!=0 or error 有 or state=UNKNOWN) は除外し通常エラー経路に流す (codex 指摘)。
	local _pg_state_mtime1 _pg_result_state
	_pg_state_mtime1=$(stat -f %m "$GAME_STATE" 2>/dev/null || stat -c %Y "$GAME_STATE" 2>/dev/null || echo 0)
	_pg_result_state=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state',''))" 2>/dev/null || echo "")
	if [ "${LAST_TURNS:-0}" -eq 0 ] && [ "$_pg_state_mtime1" = "$_pg_state_mtime0" ] \
		&& [ "${py_rc:-1}" -eq 0 ] && [ -z "$runner_error" ] \
		&& { [ "$_pg_result_state" = "GAMEOVER" ] || [ "$_pg_result_state" = "STOP" ]; }; then
		log "[PHANTOM] ブリッジ不稼働で試合不成立 (turns=0, game_state 不更新, state=$_pg_result_state) → 後処理スキップ・即bridge復旧"
		if [ "${PHANTOM_GAME_AUTO_RECOVER_ENABLED:-1}" = "1" ] && command -v _br_relaunch >/dev/null 2>&1; then
			if _br_relaunch; then
				log "[PHANTOM] bridge 再起動 成功"
			else
				log "[PHANTOM] bridge 再起動 失敗 (次周回 _ensure_bridge_alive で再試行)"
			fi
		fi
		rm -f "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || true
		return "${PLAY_RECOVERED_RETRY_RC:-75}"
	fi

	if [ "$runner_error" = "decide_exception" ]; then
		_handle_decide_exception_recovery "$runner_error_msg" "$LAST_TURNS" "$LAST_SCORE"
		LAST_SCORE=0
		LAST_TURNS=0
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
		LAST_SOVIET="false"
		return "$PLAY_RECOVERED_RETRY_RC"
	fi

	# bridge 非同期 (commands未消化 連続=空転) の自己回復:
	# strategy_runner が中断 → bridge を再起動して次周回で復旧。
	# 試合不成立扱いでスコアは記録しない (rolling_scores を汚さない)。
	if [ "$runner_error" = "bridge_desync" ]; then
		log "[BRIDGE-DESYNC] strategy_runner が bridge 非同期を検出 (turns=${LAST_TURNS}) → bridge 再起動して次周回で復旧"
		if [ "${BRIDGE_DESYNC_STOP_STALE_SOREN91_ENABLED:-1}" = "1" ] &&
			command -v soren91_is_running >/dev/null 2>&1 &&
			command -v soren91_stop >/dev/null 2>&1 &&
			soren91_is_running 2>/dev/null &&
			! _is_improve_running &&
			! { command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; }; then
			log "[BRIDGE-DESYNC] 中華AIプレイ中に soren91 残存を検出 → stale 代打を停止"
			SOREN91_STOP_TIMEOUT="${BRIDGE_DESYNC_SOREN91_STOP_TIMEOUT:-0}" soren91_stop 2>/dev/null ||
				log "[BRIDGE-DESYNC] soren91_stop 失敗 (次周回で再判定)"
		fi
		if [ "${BRIDGE_DESYNC_AUTO_RECOVER_ENABLED:-1}" = "1" ] && command -v _br_relaunch >/dev/null 2>&1; then
			if _br_relaunch; then
				log "[BRIDGE-DESYNC] bridge 再起動 成功"
			else
				log "[BRIDGE-DESYNC] bridge 再起動 失敗 (次周回 _ensure_bridge_alive で再試行)"
			fi
		else
			log "[BRIDGE-DESYNC] 自動回復無効 or _br_relaunch 未ロード → 次周回 _ensure_bridge_alive 任せ"
		fi
		LAST_SCORE=0
		LAST_TURNS=0
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
		LAST_SOVIET="false"
		rm -f "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || true
		return "${PLAY_RECOVERED_RETRY_RC:-75}"
	fi

	log "[RESULT] score=$LAST_SCORE turns=$LAST_TURNS"
}

#=== ロシア建国祝賀 ===
handle_russia_celebration() {
	local score="$1" turns="$2" game_num="$3"

	# ロシア建国クリップは無効化中（ソ連建国クリップは有効）
	# _create_twitch_clip "🇷🇺 ロシア建国! score=${score} (Game #${game_num})" "$game_num" "${RUSSIA_CELEBRATION_CLIP_DELAY_SEC:-5}"
	_append_celebration_history "russia" "$score" "$turns" "$game_num"

	if [ "${RUSSIA_CELEBRATION_ENABLED:-0}" = "0" ]; then
		log "[RUSSIA] 祝賀読み上げは無効化中"
		rm -f "$RUSSIA_CELEBRATION_WORKER_PID_FILE"
		rm -f "$TMP_MARKERS_DIR/.russia_created"
		return 0
	fi

	log "!!! RUSSIA CREATED !!!"

	generate_russia_celebration "$score" "$turns" "$game_num"
	if [ -f "$TMP_DEBUG_DIR/radio_russia_celebration.txt" ] && [ -s "$TMP_DEBUG_DIR/radio_russia_celebration.txt" ]; then
		_refresh_radio_intro_for_playback_file "$TMP_DEBUG_DIR/radio_russia_celebration.txt" "russia_celebration"
		enqueue_audio_file "$TMP_DEBUG_DIR/radio_russia_celebration.txt" "russia_celebration"
	fi
	_radio_clear_state "russia_celebration"
	rm -f "$RUSSIA_CELEBRATION_WORKER_PID_FILE"
	rm -f "$TMP_MARKERS_DIR/.russia_created"
}

#=== ソ連建国祝賀 ===
handle_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"

	log "!!! SOVIET CREATED !!!"
	_append_celebration_history "soviet" "$score" "$turns" "$game_num"

	# 祝賀読み上げ/クリップの有効・無効 (ロシア祝賀の RUSSIA_CELEBRATION_ENABLED と同パターン)。
	# 0 のときは建国履歴のみ記録し、トーク生成・音声・クリップ・コメント停止は行わない。
	if [ "${SOVIET_CELEBRATION_ENABLED:-1}" = "0" ]; then
		log "[SOVIET] 祝賀読み上げは無効化中 (SOVIET_CELEBRATION_ENABLED=0)"
		rm -f "$TMP_MARKERS_DIR/.soviet_created"
		return 0
	fi

	_create_twitch_clip "☭ ソ連建国! score=${score} (Game #${game_num})" "$game_num" "${SOVIET_CELEBRATION_CLIP_DELAY_SEC:-20}"

	# ロシア祝賀が走っていたら中止してソ連祝賀を優先
	_cancel_russia_celebration_worker

	# 祝賀トーク生成
	generate_soviet_celebration "$score" "$turns" "$game_num"

	# コメント生成を一時停止（祝賀トーク優先）
	_kill_comment_gen

	# 祝賀トーク再生を audio_worker に委譲
	if [ -f "$TMP_DEBUG_DIR/radio_soviet_celebration.txt" ] && [ -s "$TMP_DEBUG_DIR/radio_soviet_celebration.txt" ]; then
		enqueue_audio_file "$TMP_DEBUG_DIR/radio_soviet_celebration.txt" "soviet_celebration"
	fi
	_radio_clear_state "celebration"
	rm -f "$TMP_MARKERS_DIR/.soviet_created"

}

#=== 試合後の後処理 ===
post_game_bookkeeping() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ] && [ "${LAST_SOVIET:-false}" != "true" ]; then
		log "[HALT] post_game_bookkeepingをスキップ（建国後停止中）"
		return 0
	fi

	# LAST_TURNS をデーモン向けにファイル保存
	echo "${LAST_TURNS:-0}" >"tmp/state/last_turns.txt" 2>/dev/null || true

	local game_num_display=$((GAME_NUM + 1))

	# チャネルポイント予想: 今回の結果を best_outcome に蓄積（リセット前に判定）
	# ※cleanup前に実行し、建国イベントが確実に記録されるようにする
	if [ -f "$TMP_STATE_DIR/current_prediction.json" ]; then
		local cur_outcome=0
		if [ "${LAST_SOVIET:-false}" = "true" ]; then
			cur_outcome=2
		elif [ "${LAST_RUSSIA:-false}" = "true" ]; then
			cur_outcome=1
			# ロシア建国フラグを記録（12ゲーム後の判定用）
			python3 -c "
import json
f='$TMP_STATE_DIR/current_prediction.json'
d=json.load(open(f))
d['russia_created']=True
json.dump(d,open(f,'w'))
" 2>/dev/null
		fi
		if [ "$cur_outcome" -gt 0 ]; then
			local prev_best=0
			prev_best=$(python3 -c "import json; print(json.load(open('$TMP_STATE_DIR/current_prediction.json')).get('best_outcome',0))" 2>/dev/null || echo 0)
			if [ "$cur_outcome" -gt "$prev_best" ]; then
				python3 -c "
import json
f='$TMP_STATE_DIR/current_prediction.json'
d=json.load(open(f))
d['best_outcome']=$cur_outcome
json.dump(d,open(f,'w'))
" 2>/dev/null
			fi
		fi
	fi

	# チャネルポイント予想: prediction_worker が state file を監視して create/cleanup/resolve する

	# 蓄積用にロシア建国フラグを保存（後でリセットされるため）
	local _russia_for_acc="$LAST_RUSSIA"

	# ソ連建国チェック
	if [ "$LAST_SOVIET" = "true" ]; then
		handle_soviet_celebration "$LAST_SCORE" "$LAST_TURNS" "$game_num_display"
		# prediction_worker が best_outcome=2 を検知して resolve する
		HALT_STRATEGY_AFTER_SOVIET=1
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
		LAST_SOVIET="false"
		log "[HALT] ソ連建国達成: strategy実行を停止し、retry/次ゲーム操作を無効化"
	elif [ "$LAST_RUSSIA" = "true" ] && [ "${LAST_RUSSIA_ANNOUNCED:-false}" != "true" ]; then
		handle_russia_celebration "$LAST_SCORE" "$LAST_TURNS" "$game_num_display"
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
	fi

	# スコア履歴
	printf '%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/')" "$LAST_SCORE" >>score_history.txt

	# ダッシュボード更新（GAMEOVER状態で生成→表示される）
	log "[DASHBOARD] Generating GAMEOVER dashboard..."
	./generate_dashboard.sh GAMEOVER || log "[DASHBOARD] ERROR: generate_dashboard.sh GAMEOVER failed"
		# OBS 側で dashboard ソースを show。
		# ここは同期実行して、次ゲーム開始時の MOVE 生成で空HTMLへ戻る前に
		# OBS 側の表示切り替えとブラウザソースの再読込時間を確保する。
		local _dashboard_shown=0
		if [ "${OBS_DASHBOARD_VISIBILITY_ENABLED:-1}" = "1" ]; then
			if ./obs_control.sh show "${OBS_DASHBOARD_SCENE:-soren}" "${OBS_DASHBOARD_SOURCE:-dashboard}" >/dev/null 2>&1; then
				_dashboard_shown=1
			else
				log "[DASHBOARD] WARN: OBS dashboard show failed"
			fi
		fi
		local _dashboard_hold_sec="${DASHBOARD_GAMEOVER_HOLD_SEC:-3}"
		case "$_dashboard_hold_sec" in ''|*[!0-9]*) _dashboard_hold_sec=3 ;; esac
		if [ "$_dashboard_shown" -eq 1 ] && [ "$_dashboard_hold_sec" -gt 0 ]; then
			sleep "$_dashboard_hold_sec"
		fi

	# バージョン保存・ベスト判定・履歴アーカイブ
	save_strategy_version "$LAST_SCORE"
	update_best "$LAST_SCORE" && _create_twitch_clip "🏆 NEW HIGH SCORE: ${LAST_SCORE}! (Game #${game_num_display})" "$game_num_display"
	archive_history "$LAST_SCORE"

	# アーカイブファイル名を記録
	LAST_ARCHIVE_FILE=$(ls -1t "$HISTORY_DIR"/[0-9]*_score*.jsonl 2>/dev/null | head -1)
	archive_gameover_screenshots "$LAST_ARCHIVE_FILE"

	# 建国ボーナス: 最終盤面のtype別ボーナスを加算した評価スコア
	local EVAL_SCORE
	EVAL_SCORE=$(echo "$RESULT_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
types = d.get('final_types', [])
soviet = d.get('soviet_created', False)
TB = {1:0,2:0,3:1,4:3,5:7,6:15,7:32,8:67,9:141,10:296,11:622,12:1306,13:2743,14:5760,15:12096}
bonus = sum(TB.get(t, 0) for t in types)
if soviet: bonus += 800
print(d.get('score', 0) + bonus)
" 2>/dev/null || echo "$LAST_SCORE")

	# EVAL_SCORE履歴（ボーナス込み）
	printf '%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/')" "$EVAL_SCORE" >>eval_score_history.txt

	local _bonus=$((EVAL_SCORE - LAST_SCORE))
	if [ "$_bonus" -gt 0 ]; then
		local _top_types
		_top_types=$(echo "$RESULT_JSON" | python3 -c "import json,sys;ts=json.load(sys.stdin).get('final_types',[]);print(sorted(ts,reverse=True)[:5])" 2>/dev/null || echo "[]")
		log "[BONUS] types=${_top_types} bonus=+${_bonus} eval=${EVAL_SCORE} (raw=${LAST_SCORE})"
	fi

	# 改善用の rolling/queued 記録はここで一度だけ行う
	export LAST_RAW_SCORE="$LAST_SCORE"
		record_completed_game_for_adaptive_improvement "$LAST_ARCHIVE_FILE" "$EVAL_SCORE" "$LAST_SOVIET" "$_russia_for_acc"
		if [ -x ./monitor_improve_runtime.sh ]; then
			(
				./monitor_improve_runtime.sh >/dev/null 2>&1 ||
					log "[MONITOR] improve runtime monitor skipped/failed after post_game_bookkeeping"
			) &
		fi

	if [ -x ./overlay_notify.sh ]; then
		local _overlay_counts _cycle_progress
		_overlay_counts=$(echo "$RESULT_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
types = [int(x) for x in d.get('final_types', []) if str(x).lstrip('-').isdigit()]
print('ウクライナ=%d カザフ=%d ロシア=%d ソ連=%d' % (types.count(13), types.count(14), types.count(15), 1 if d.get('soviet_created') else 0))
" 2>/dev/null || echo "")
		_cycle_progress=$(python3 - "$ACCUMULATED_GAMES_FILE" "$MIN_GAMES_BEFORE_IMPROVE" <<'PY'
import json
import sys

path = sys.argv[1]
cycle = int(sys.argv[2]) if len(sys.argv) > 2 else 12
try:
    with open(path, encoding="utf-8") as f:
        count = int((json.load(f) or {}).get("count", 0))
except Exception:
    count = 0
if count > 0 and cycle > 0:
    print(f"[{count}/{cycle}]")
PY
)
		local _ov_best="" _ov_result="通常終了"
		_ov_best=$(cat best_score.txt 2>/dev/null | tr -dc '0-9')
		if [ "${LAST_SOVIET:-0}" = "1" ] || echo "${_overlay_counts}" | grep -q 'ソ連=1'; then
			_ov_result="ソ連建国"
		elif echo "${_overlay_counts}" | grep -qE 'ロシア=[1-9]'; then
			_ov_result="ロシア建国"
		fi
		[ "${LAST_SCORE:-0}" -gt "${_ov_best:-0}" ] 2>/dev/null && _ov_result="${_ov_result}・自己ベスト更新"
		./overlay_notify.sh game "Game #${game_num_display} 終了${_cycle_progress:+ ${_cycle_progress}} (${_ov_result})" "score=${LAST_SCORE} eval=${EVAL_SCORE} (bonus=+${_bonus:-0}) turns=${LAST_TURNS:-?}${_overlay_counts:+ | ${_overlay_counts}}${_ov_best:+ | best=${_ov_best}}" "info" >/dev/null 2>&1 || true
	fi

	# サイクル序盤の改善結果/粛清ラジオは audio_worker が deferred queue から再生する

	# サイクル進捗をチャットに投稿
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		local pred_progress
		pred_progress=$(
			python3 - "$ACCUMULATED_GAMES_FILE" "$LAST_SCORE" "$EVAL_SCORE" "$MIN_GAMES_BEFORE_IMPROVE" \
				"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
				"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
				"${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}" \
				"${STAGE_ACHIEVEMENT_GATE_MIN_RATE:-0.80}" \
				"${STAGE_ACHIEVEMENT_GATE_TYPES:-12,13,14,15}" <<'PY'
import json, sys

COUNTRY_NAMES = {
    11: "トルクメン",
    12: "ベラルーシ",
    13: "ウクライナ",
    14: "カザフ",
    15: "ロシア",
    16: "ソ連",
}

STAGE_GATE_SEQUENCE = (
    (11, "トルクメン"),
    (13, "ウクライナ"),
    (14, "カザフ"),
)

def read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback

def parse_gate_types(raw):
    stages = []
    for chunk in str(raw or "").replace(";", ",").split(","):
        chunk = chunk.strip().lstrip("Tt")
        if not chunk:
            continue
        try:
            stage = int(chunk)
        except Exception:
            continue
        if stage > 0:
            stages.append(stage)
    return sorted(set(stages)) or [12, 13, 14, 15]

def max_types_from(data):
    values = []
    for raw in (data or {}).get("max_types", []) or []:
        try:
            values.append(int(raw))
        except Exception:
            pass
    return values

def rate_line(max_types):
    total = len(max_types)
    if total <= 0:
        return ""
    parts = []
    for stage in (13, 14, 15):
        reached = sum(1 for value in max_types if value >= stage)
        parts.append(f"{COUNTRY_NAMES.get(stage, 'Type'+str(stage))}={round(reached / total * 100):.0f}%")
    return "建国率 " + " ".join(parts)

def target_from_anchor(anchor_data, threshold, gate_types):
    max_types = max_types_from(anchor_data)
    total = len(max_types)
    if total <= 0:
        return None
    candidates = []
    for stage in gate_types:
        reached = sum(1 for value in max_types if value >= stage)
        rate = reached / total
        if rate >= threshold:
            candidates.append((stage, rate, reached, total))
    return max(candidates, key=lambda item: item[0]) if candidates else None

def stage_gate_rate(data, stage):
    max_types = max_types_from(data)
    if not max_types:
        try:
            best = int((data or {}).get("best_max_type", 0) or 0)
        except Exception:
            best = 0
        return 1.0 if best >= stage else 0.0
    return sum(1 for value in max_types if value >= stage) / len(max_types)

def stage_gate_counts(data, stage):
    max_types = max_types_from(data)
    total = len(max_types)
    if total <= 0:
        try:
            best = int((data or {}).get("best_max_type", 0) or 0)
        except Exception:
            best = 0
        return (1 if best >= stage else 0, 1 if best > 0 else 0)
    return (sum(1 for value in max_types if value >= stage), total)

def target_from_stage_regression(anchor_data, current_data):
    for stage, _name in STAGE_GATE_SEQUENCE:
        reached, total = stage_gate_counts(current_data, stage)
        if total <= 0:
            return None
        current_rate = stage_gate_rate(current_data, stage)
        anchor_rate = stage_gate_rate(anchor_data, stage)
        try:
            current_best = int((current_data or {}).get("best_max_type", 0) or 0)
            anchor_best = int((anchor_data or {}).get("best_max_type", 0) or 0)
        except Exception:
            current_best = 0
            anchor_best = 0
        current_unmet = current_rate < 1.0
        regressed = anchor_rate > current_rate or (anchor_best >= stage and current_best < stage)
        if current_unmet and regressed:
            return (stage, anchor_rate, current_rate, reached, total)
    return None

acc = read_json(sys.argv[1], {})
count = acc.get("count", 0)
scores = acc.get("scores", "").split()
eval_avg = sum(int(s) for s in scores) // len(scores) if scores else 0
raw_scores = acc.get("raw_scores", "").split()
raw_avg = sum(int(s) for s in raw_scores) // len(raw_scores) if raw_scores else 0
cycle = int(sys.argv[4]) if len(sys.argv) > 4 else 12
remain = cycle - count
russia = acc.get("russia_count", 0)
russia_str = f" 🇷🇺×{russia}" if russia > 0 else ""
raw = sys.argv[2]
eval_s = sys.argv[3]
bonus = int(eval_s) - int(raw)
bonus_str = f"(+{bonus})" if bonus > 0 else ""
raw_avg_str = f"raw_avg={raw_avg} " if raw_scores else ""
current_run = read_json(sys.argv[5], {})
current_progress = dict(current_run or {})
if acc.get("max_types"):
    current_progress["max_types"] = acc.get("max_types", [])
    current_progress["best_max_type"] = max(max_types_from(current_progress), default=0)
rolling = read_json(sys.argv[6], {})
anchor = read_json(sys.argv[7], {})
try:
    threshold = max(0.0, min(1.0, float(sys.argv[8])))
except Exception:
    threshold = 0.80
gate_types = parse_gate_types(sys.argv[9] if len(sys.argv) > 9 else "12,13,14,15")
current_max_types = max_types_from(current_progress)
founding = rate_line(current_max_types)
anchor_hash = str((anchor or {}).get("hash") or "")
anchor_data = rolling.get(anchor_hash, {}) if isinstance(rolling, dict) else {}
target = target_from_anchor(anchor_data, threshold, gate_types)
target_text = ""
regression_target = target_from_stage_regression(anchor_data, current_progress)
if regression_target:
    stage, anchor_rate, current_rate, reached, total = regression_target
    remaining_games = max(0, cycle - total)
    final_total = max(1, total + remaining_games)
    max_possible_rate = (reached + remaining_games) / final_total
    status = "粛清圏" if max_possible_rate < anchor_rate else "未達"
    target_text = (
        f"target={COUNTRY_NAMES.get(stage, 'Type'+str(stage))}(T{stage}) {status}"
        f" curr={round(current_rate * 100):.0f}% anchor={round(anchor_rate * 100):.0f}%"
    )
    if status == "粛清圏":
        target_text += f" max={round(max_possible_rate * 100):.0f}%"
elif target:
    stage, anchor_rate, _, _ = target
    current_best = max([int((current_progress or {}).get("best_max_type", 0) or 0)] + current_max_types) if current_max_types or (current_progress or {}).get("best_max_type") else 0
    target_ok = current_best >= stage
    target_text = f"target={COUNTRY_NAMES.get(stage, 'Type'+str(stage))}(T{stage}) {'OK' if target_ok else '未達'}"
extra = " | ".join(part for part in (founding, target_text) if part)
extra = f" | {extra}" if extra else ""
print(f"[{count}/{cycle}] score={raw}{bonus_str} | {raw_avg_str}eval_avg={eval_avg}{russia_str}{extra} (あと{remain}試合)")
PY
		)
		enqueue_chat_message "${pred_progress}" "eloop"
	fi

	# 毎試合の git commit は廃止: 改善終了時 (eloop_improve.sh) と粛清時 (regression.sh) の
	# 区切りでまとめてコミットする。ここでは pending 通知の処理のみ。
	_post_pending_phyrogenetic_tree_link_to_chat_if_any
}

#=== 次の試合準備 ===
prepare_next_game() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] prepare_next_gameをスキップ（retryなし）"
		return 0
	fi

	# 試合時スナップショットのクリーンアップ
	rm -f "${STRATEGY_FILE}.game_snapshot"

	if is_game_over; then
		send_retry
	else
		wait_for_move || {
			send_retry
		}
	fi
}
