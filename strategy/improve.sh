# strategy/improve.sh - improve_state管理, accumulate, trigger_adaptive_improvement


#=== spawn 排他 mutex (dual-spawner 二重起動レース防止) ===
# mkdir は POSIX atomic。owner ファイルに PID を記録し、解放/stale 回収は
# 所有者一致時のみ実行 (他 spawner の新規 lock を消さない)。

# 取得試行。成功で 0、別 spawner が保持中で取得不可なら 1。
_acquire_spawn_lock() {
	local d="$IMPROVE_SPAWN_LOCK_DIR"
	if mkdir "$d" 2>/dev/null; then
		echo "$$" >"$d/owner" 2>/dev/null || true
		return 0
	fi
	# 取得失敗 → stale 判定 (owner 死亡 or TTL 超過時のみ steal)
	local owner_pid lk_m now age
	owner_pid=$(cat "$d/owner" 2>/dev/null || echo "")
	lk_m=$(stat -f %m "$d" 2>/dev/null || stat -c %Y "$d" 2>/dev/null || echo 0)
	now=$(date +%s)
	age=$((now - lk_m))
	local stale=0
	if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then
		stale=1
	elif [ "$lk_m" -gt 0 ] && [ "$age" -ge "${IMPROVE_SPAWN_LOCK_TTL:-90}" ]; then
		stale=1
	fi
	if [ "$stale" -eq 1 ]; then
		# steal 前に owner を再確認 (別 contender が既に再取得していたら触らない)
		local owner_recheck
		owner_recheck=$(cat "$d/owner" 2>/dev/null || echo "")
		if [ "$owner_recheck" = "$owner_pid" ]; then
			rm -rf "$d" 2>/dev/null || true
			if mkdir "$d" 2>/dev/null; then
				echo "$$" >"$d/owner" 2>/dev/null || true
				log "[IMPROVE] stale spawn lock 回収し再取得 (旧owner=${owner_pid:-?})"
				return 0
			fi
		fi
	fi
	return 1
}

# 解放: owner が自 PID のときのみ削除 (他 spawner の lock を消さない)
_release_spawn_lock() {
	local d="$IMPROVE_SPAWN_LOCK_DIR"
	[ -d "$d" ] || return 0
	local owner_pid
	owner_pid=$(cat "$d/owner" 2>/dev/null || echo "")
	if [ "$owner_pid" = "$$" ]; then
		rm -rf "$d" 2>/dev/null || true
	fi
}

#=== 改善中判定 (soren_loop.sh のスキップ判定用) ===

_is_improve_running() {
	# ロックファイルが存在し、かつ実際に改善プロセスが動いている(status=running/manual)時のみtrue
	# ロックのみ存在(daemon待ち/failed後)ではfalseを返し、main loopがゲームを続行できるようにする
	[ -f "$IMPROVE_LOCK_FILE" ] || return 1
	grep -q '"status"[[:space:]]*:[[:space:]]*"running"\|"status"[[:space:]]*:[[:space:]]*"manual"' "$IMPROVE_STATE_FILE" 2>/dev/null
}

_scheduled_meriken_time_should_run() {
	[ "${MERIKEN_SCHEDULED_TIME_ENABLED:-1}" = "1" ] || return 1
	[ "$(date +%H)" = "${MERIKEN_TIME_START_HOUR:-20}" ]
}

_improve_overlay_generate_once() {
	[ -x "./generate_improve_overlay.sh" ] || return 0
	./generate_improve_overlay.sh once >/dev/null 2>&1 || true
}

_improve_overlay_show() {
	_improve_overlay_generate_once
	./obs_control.sh show soren "$IMPROVE_OVERLAY_SOURCE" 2>/dev/null &
	./obs_control.sh hide soren console4 2>/dev/null &
}

_improve_overlay_hide() {
	_improve_overlay_generate_once
	./obs_control.sh hide soren "$IMPROVE_OVERLAY_SOURCE" 2>/dev/null &
	./obs_control.sh hide soren console4 2>/dev/null &
}

_improve_overlay_watch_start() {
	local pid="${1:-}"
	[ -x "./generate_improve_overlay.sh" ] || return 0
	./generate_improve_overlay.sh watch "$pid" >/dev/null 2>&1 &
	echo $!
}

#=== 改善ステート管理 ===

_read_improve_state() {
	if [ -f "$IMPROVE_STATE_FILE" ]; then
		cat "$IMPROVE_STATE_FILE"
	else
		echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}'
	fi
}

_write_improve_state() {
	local status="$1" pid="$2" hash="$3"
	local phase="${4:-}" progress="${5:-0}" detail="${6:-}" started_at="${7:-0}" pid_birth_epoch="${8:-0}"
	local now
	now=$(date +%s)
	python3 - "$IMPROVE_STATE_FILE" "$status" "${pid:-0}" "${hash:-}" "$phase" "$progress" "$detail" "$started_at" "$now" "${pid_birth_epoch:-0}" <<'PY'
import json
import sys

out_file, status, pid_raw, hash_before, phase, progress_raw, detail, started_raw, now_raw, pid_birth_raw = sys.argv[1:11]

try:
    pid = int(pid_raw)
except Exception:
    pid = 0
try:
    progress = int(float(progress_raw))
except Exception:
    progress = 0
progress = max(0, min(100, progress))
try:
    started_at = int(started_raw)
except Exception:
    started_at = 0
try:
    now = int(now_raw)
except Exception:
    now = 0
try:
    pid_birth_epoch = int(pid_birth_raw)
except Exception:
    pid_birth_epoch = 0

if started_at <= 0 and status == "running":
    started_at = now

data = {
    "status": status,
    "pid": pid,
    "strategy_hash_before": hash_before,
    "phase": phase,
    "progress": progress,
    "detail": detail,
    "started_at": started_at,
    "updated_at": now,
    "pid_birth_epoch": pid_birth_epoch,
}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
PY
}

_is_live_improve_pid() {
	local pid="$1"
	case "$pid" in
	''|0|*[!0-9]*) return 1 ;;
	esac
	kill -0 "$pid" 2>/dev/null || return 1
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	[ -n "$cmd" ] || return 1
	echo "$cmd" | grep -q "eloop_improve\.sh"
}

_find_live_improve_pid() {
	local candidate=""
	if [ -f "$IMPROVE_STATE_FILE" ]; then
		candidate=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		if _is_live_improve_pid "$candidate"; then
			printf '%s\n' "$candidate"
			return 0
		fi
	fi
	if _is_live_improve_pid "${IMPROVE_PID:-0}"; then
		printf '%s\n' "${IMPROVE_PID:-0}"
		return 0
	fi
	return 1
}

_sync_improve_state_with_live_process() {
	local live_pid=""
	live_pid=$(_find_live_improve_pid 2>/dev/null || true)
	case "$live_pid" in
	''|0|*[!0-9]*) return 1 ;;
	esac

	local state current_status current_pid hash_before started_at pid_birth_epoch
	state=$(_read_improve_state)
	current_status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)
	current_pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)
	hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
	started_at=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('started_at',0) or 0))" 2>/dev/null || echo 0)
	pid_birth_epoch=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('pid_birth_epoch',0) or 0))" 2>/dev/null || echo 0)
	[ -n "$hash_before" ] || hash_before=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
	# PIDが変わった場合はbirth_epochを再計算
	if [ "${current_pid:-0}" != "$live_pid" ] || [ "${pid_birth_epoch:-0}" -eq 0 ]; then
		pid_birth_epoch=$(ps -p "$live_pid" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0)
	fi

	if [ "$current_status" != "running" ] || [ "${current_pid:-0}" != "$live_pid" ]; then
		log "[IMPROVE] state self-heal: live PID=$live_pid を running に再同期 (was status=${current_status:-unknown}, pid=${current_pid:-0})"
		_write_improve_state "running" "$live_pid" "$hash_before" "recovered" "1" "live_process_detected" "$started_at" "$pid_birth_epoch"
	fi
	IMPROVE_PID=$live_pid
	return 0
}

_stop_improve_pid_if_running() {
	local pid="$1" label="${2:-improve}"
	if ! _is_live_improve_pid "$pid"; then
		return 0
	fi
	local state phase detail progress pid_cmd
	state=$(_read_improve_state)
	phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
	detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
	progress=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('progress',0) or 0))" 2>/dev/null || echo 0)
	pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	log "[IMPROVE] stop request: label=${label} pid=${pid} phase=${phase:-?} detail=${detail:-?} progress=${progress:-0} cmd=${pid_cmd:-unknown}"
	_stop_loop_descendants "$pid"
	_stop_pid_with_fallback "$pid" "$label"
	wait "$pid" 2>/dev/null
	local wait_rc=$?
	log "[IMPROVE] stop result: label=${label} pid=${pid} wait_rc=${wait_rc}"
	if _is_live_improve_pid "$pid"; then
		log "[IMPROVE] stop result: label=${label} pid=${pid} still_alive=1"
		return 1
	fi
	return 0
}

check_and_harvest_improvement() {
	local state
	_sync_improve_state_with_live_process >/dev/null 2>&1 || true
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	# 孤立ロックファイル検出: idle状態でeloop_improveも動いていないのにロックが長時間残っている場合は削除
	# ※ daemon poll間隔(デフォルト30s)より大幅に長い閾値にすること
	#   (lock作成直後はstatus=idleのままdaemonが拾うまで最大poll間隔かかる)
	if [ "$status" = "idle" ] && [ -f "$IMPROVE_LOCK_FILE" ]; then
		local _lock_age _lock_mtime _orphan_threshold
		_orphan_threshold="${IMPROVE_STALE_WATCHDOG_SEC:-600}"
		_lock_mtime=$(stat -f '%m' "$IMPROVE_LOCK_FILE" 2>/dev/null || echo 0)
		_lock_age=$(( $(date +%s) - ${_lock_mtime:-0} ))
		if [ "${_lock_age:-0}" -gt "${_orphan_threshold}" ]; then
			log "[IMPROVE] 孤立ロックファイル検出 (age=${_lock_age}s > ${_orphan_threshold}s, status=idle) → 削除"
			rm -f "$IMPROVE_LOCK_FILE"
		fi
	fi

	# 手動改善モード
	if [ "$status" = "manual" ]; then
		# フラグが存在する間は待機
		[ -f "$TMP_STATE_DIR/manual_improve_mode" ] && return 0
		# フラグ削除済み (manual_improve_off.sh 実行後) → main loop context で harvest
		log "[IMPROVE][MANUAL] フラグ削除検出 → harvest処理開始"
		local hash_before hash_now
		hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
		hash_now=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
		if [ "$hash_before" != "$hash_now" ]; then
			log "[IMPROVE][MANUAL] 戦略更新検出: $hash_before -> $hash_now"
			local new_decide_hash prev_decide_hash=""
			new_decide_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
			if [ -f "tmp/revert_strategy.py" ]; then
				prev_decide_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
			fi
			if [ -z "$prev_decide_hash" ] && [ -f "$CURRENT_STRATEGY_RUN_FILE" ]; then
				prev_decide_hash=$(python3 -c "import json; print(json.load(open('$CURRENT_STRATEGY_RUN_FILE')).get('hash',''))" 2>/dev/null || echo "")
			fi
			if [ -n "$new_decide_hash" ] && [ -f "$ROLLING_SCORES_FILE" ]; then
				python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
rs = json.load(open(rs_file)) if os.path.exists(rs_file) else {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
json.dump(rs, open(rs_file, 'w'))
" 2>/dev/null || true
			fi
			if [ -n "$new_decide_hash" ]; then
				local branch_transition=""
				branch_transition=$(_branch_transition_after_improve "$prev_decide_hash" "$new_decide_hash" || true)
				[ -n "$branch_transition" ] && log "[BRANCH] ${branch_transition}"
				_reset_current_strategy_run "$new_decide_hash" || true
			fi
			local acc_count_discarded=0
			[ -f "$ACCUMULATED_GAMES_FILE" ] && acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
			_clear_accumulated_data
			[ "${acc_count_discarded:-0}" -gt 0 ] && log "[IMPROVE][MANUAL] 蓄積${acc_count_discarded}試合を破棄"
			_write_improve_state "idle" "0" "" "" "0" ""
			rm -f "$TMP_STATE_DIR/last_improve_failed_at"
		else
			log "[IMPROVE][MANUAL] 戦略変更なし → failed_no_apply"
			date +%s > "$TMP_STATE_DIR/last_improve_failed_at"
			_write_improve_state "idle" "0" "" "failed_no_apply" "100" "manual_no_change"
		fi
		rm -f "$IMPROVE_LOCK_FILE"
		IMPROVE_PID=0
		log "[IMPROVE][MANUAL] 手動改善完了 → idle"
		_improve_overlay_hide
		if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
			log "[IMPROVE][MANUAL] manual_meriken_mode=on のため、メリケンAI継続"
		elif _scheduled_meriken_time_should_run; then
			log "[IMPROVE][MANUAL] 20時台: メリケンAIタイムに移行 → soren91継続"
			soren91_improve
			MERIKEN_TIME_PENDING=1
			touch "tmp/state/meriken_time_pending"
		else
			soren91_stop
			[ "${POST_IMPROVE_MAINPLAY_ENABLED:-1}" = "1" ] && touch "${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}" 2>/dev/null || true
			soren91_improve
		fi
		return 0
	fi

	if [ "$status" = "running" ]; then
		local pid
		pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)

		# IMPROVE_PID を状態ファイルから同期 (再起動時の復元)
		if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ "${pid:-0}" -ne 0 ]; then
			IMPROVE_PID=$pid
		fi

		# PID再利用チェック: eloop_improve.sh のプロセスかどうか確認
		local pid_alive=false
		if [ "${pid:-0}" -ne 0 ] && kill -0 "$pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				# v40: 記録されたpid_birth_epochとプロセスlstartを直接照合してPID再利用を検出
				# updated_atは_improve_progress()で更新され続けるため比較に使えない
				local pid_start_epoch recorded_birth
				recorded_birth=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('pid_birth_epoch',0) or 0))" 2>/dev/null || echo 0)
				pid_start_epoch=$(ps -p "$pid" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0)
				if [ "${recorded_birth:-0}" -ne 0 ] && [ "${pid_start_epoch:-0}" -ne 0 ] && [ "$pid_start_epoch" -ne "$recorded_birth" ]; then
					# PIDは生きているがlstartが記録値と異なる → PID再利用の可能性
					log "[IMPROVE] PID=$pid はlstart($pid_start_epoch)が記録値($recorded_birth)と不一致 → PID再利用とみなしstale状態クリア"
					pid_alive=false
				else
					pid_alive=true
				fi
			else
				log "[IMPROVE] PID=$pid は別プロセス ($pid_cmd) → stale状態クリア"
			fi
		fi

			local watchdog_sec="${IMPROVE_STALE_WATCHDOG_SEC:-3600}"
			case "$watchdog_sec" in
			''|*[!0-9]*) watchdog_sec=3600 ;;
			esac
		if [ "$pid_alive" = true ] && [ "${watchdog_sec:-0}" -gt 0 ]; then
			local updated_at updated_age now_epoch log_age log_mtime prev_phase prev_detail
			updated_at=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('updated_at',0) or 0))" 2>/dev/null || echo 0)
			prev_phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
			prev_detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
			now_epoch=$(date +%s)
			updated_age=$(( now_epoch - ${updated_at:-0} ))
			log_age=$updated_age
			if [ -f "$IMPROVE_AI_LOG_FILE" ]; then
				log_mtime=$(stat -f '%m' "$IMPROVE_AI_LOG_FILE" 2>/dev/null || echo 0)
				if [ "${log_mtime:-0}" -gt 0 ]; then
					log_age=$(( now_epoch - log_mtime ))
				fi
			fi
			if [ "$updated_age" -ge "$watchdog_sec" ] && [ "$log_age" -ge "$watchdog_sec" ]; then
				log "[IMPROVE] watchdog警告: ${updated_age}s 状態更新なし / ${log_age}s ログ更新なし (PID=$pid, phase=${prev_phase:-?}, detail=${prev_detail:-})"
			fi
		fi

		if [ "$pid_alive" = false ]; then
			# プロセス完了 or stale → harvest
			local hash_before
			hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
			local prev_phase prev_detail prev_progress
			prev_phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
			prev_detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
			prev_progress=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('progress',0) or 0))" 2>/dev/null)
			local hash_now
			hash_now=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

			if [ "$hash_before" != "$hash_now" ]; then
				log "[IMPROVE] 戦略更新検出: $hash_before -> $hash_now"

				# リバート用候補はeloop_improve.shが tmp/revert_strategy.py に保存済み
				# ローリングスコアで新戦略のprev_hashを記録
				local new_decide_hash
				local prev_decide_hash=""
				new_decide_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
				if [ -f "tmp/revert_strategy.py" ]; then
					prev_decide_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
				fi
				if [ -z "$prev_decide_hash" ] && [ -f "$CURRENT_STRATEGY_RUN_FILE" ]; then
					prev_decide_hash=$(python3 -c "import json; print(json.load(open('$CURRENT_STRATEGY_RUN_FILE')).get('hash',''))" 2>/dev/null || echo "")
				fi
				if [ -n "$new_decide_hash" ] && [ -f "$ROLLING_SCORES_FILE" ]; then
					python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
with open(rs_file, 'w') as f:
    json.dump(rs, f)
" 2>/dev/null
				fi
				if [ -n "$new_decide_hash" ]; then
					local branch_transition=""
					branch_transition=$(_branch_transition_after_improve "$prev_decide_hash" "$new_decide_hash" || true)
					[ -n "$branch_transition" ] && log "[BRANCH] ${branch_transition}"
				fi
				if [ -n "$new_decide_hash" ]; then
					_reset_current_strategy_run "$new_decide_hash"
				fi

				# 戦略が変わった → 蓄積データは旧戦略のものなので破棄
				local acc_count_discarded=0
				if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
					acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
				fi
				_clear_accumulated_data
				if [ "${acc_count_discarded:-0}" -gt 0 ]; then
					log "[IMPROVE] 蓄積${acc_count_discarded}試合を破棄 (旧戦略のデータ)"
				fi
			else
				log "[IMPROVE] failed_no_apply: 戦略変更なし (phase=${prev_phase:-?}, progress=${prev_progress:-0}, detail=${prev_detail:-})"
				# 戦略が変わっていない → 蓄積データはそのまま有効
				# failed_no_apply タイムスタンプを記録 (連続再試行防止用)
				date +%s > "$TMP_STATE_DIR/last_improve_failed_at"
			fi

			if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
				:
			elif _scheduled_meriken_time_should_run; then
				:
			else
				if [ -n "${SOREN91_STOPPING_FILE:-}" ]; then
					touch "$SOREN91_STOPPING_FILE" 2>/dev/null || true
				fi
			fi

			if [ "$hash_before" != "$hash_now" ]; then
				_write_improve_state "idle" "0" "" "" "0" ""
				rm -f "$TMP_STATE_DIR/last_improve_failed_at"
				rm -f "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || true
				rm -f "$IMPROVE_LOCK_FILE"
			else
				_write_improve_state "idle" "0" "" "failed_no_apply" "100" "${prev_detail:-process_exited_without_apply}"
				# failed_no_apply: 有効なlockだけを残す。空lockはmain loopを止めるだけなので作らない。
				if [ -s "$IMPROVE_LOCK_FILE" ]; then
					touch "$IMPROVE_LOCK_FILE" 2>/dev/null || true
				elif [ -s "$ACCUMULATED_GAMES_FILE" ]; then
					cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE" 2>/dev/null || true
				else
					rm -f "$IMPROVE_LOCK_FILE" 2>/dev/null || true
				fi
				# バックオフを設定して即座にリトライしない (soren91 stop→start ループ防止)
				local _backoff_count=1
				if [ -f "$TMP_STATE_DIR/rate_limit_backoff" ]; then
					_backoff_count=$(( $(sed -n '1p' "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || echo 0) + 1 ))
				fi
				printf '%d\n%d\n' "$_backoff_count" "$(date +%s)" > "$TMP_STATE_DIR/rate_limit_backoff"
				log "[IMPROVE] ロックファイル保持 → daemon再試行待ち (backoff count=${_backoff_count})"
			fi
			IMPROVE_PID=0
			log "[IMPROVE] 改善完了 → idle"
			# OBS: 改善中オーバーレイ非表示
			_improve_overlay_hide
			if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
				log "[IMPROVE] manual_meriken_mode=on のため、メリケンAI継続"
			elif _scheduled_meriken_time_should_run; then
				# 20時台: メリケンAIタイムに移行するため停止しない
				log "[IMPROVE] 20時台: メリケンAIタイムに移行 → soren91継続"
				soren91_improve
				MERIKEN_TIME_PENDING=1
				touch "tmp/state/meriken_time_pending"
			else
				# soren91 (メリケンAI) を停止 → バックグラウンド改善開始
				soren91_stop
				# 改善完了マーカ: 次の (カスケード) 改善ロックに即 PAUSE する前に
				# soren_loop がメインゲームを最低1回走らせる窓を保証する
				[ "${POST_IMPROVE_MAINPLAY_ENABLED:-1}" = "1" ] && touch "${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}" 2>/dev/null || true
				soren91_improve
				# 読み上げ + Twitch チャットに戦略改善終了を通知 (1サイクル1回のみ)
				local _handover_guard="$TMP_STATE_DIR/handover_announced"
				if [ ! -f "$_handover_guard" ]; then
					touch "$_handover_guard"
					enqueue_audio_text "戦略改善終了。交代します" "soren91_handover" "${SOREN91_VOICEVOX_SPEAKER:-46}"
					enqueue_chat_message "戦略改善終了。交代します" "improve"
				else
					log "[IMPROVE] 交代アナウンス重複スキップ (guard存在)"
				fi
			fi
		fi
	fi
}

accumulate_game_data() {
	local archive_file="$1" score="$2" soviet="$3" strategy_hash="$4" russia="${5:-false}"

	python3 -c "
import json, os
acc_file = '$ACCUMULATED_GAMES_FILE'
if os.path.exists(acc_file):
    with open(acc_file) as f:
        acc = json.load(f)
else:
    acc = {'files': [], 'scores': '', 'soviet': False, 'count': 0, 'hash': '', 'russia_count': 0}

curr_hash = '$strategy_hash'
if acc.get('hash') and curr_hash and acc.get('hash') != curr_hash:
    acc = {'files': [], 'scores': '', 'soviet': False, 'count': 0, 'hash': curr_hash, 'russia_count': 0}
elif curr_hash:
    acc['hash'] = curr_hash

raw_score = os.environ.get('LAST_RAW_SCORE', '')
acc['files'].append('$archive_file')
acc['scores'] = (acc['scores'] + ' $score').strip()
if raw_score:
    acc['raw_scores'] = (acc.get('raw_scores', '') + ' ' + raw_score).strip()
if '$soviet' == 'true':
    acc['soviet'] = True
if '$russia' == 'true':
    acc['russia_count'] = acc.get('russia_count', 0) + 1
acc['count'] += 1

with open(acc_file, 'w') as f:
    json.dump(acc, f)
print(f'[ACCUMULATE] 蓄積: {acc[\"count\"]}試合')
" 2>/dev/null
}

_read_accumulated_data() {
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		cat "$ACCUMULATED_GAMES_FILE"
	else
		echo '{"files":[],"scores":"","soviet":false,"count":0,"hash":""}'
	fi
}

_clear_accumulated_data() {
	rm -f "$ACCUMULATED_GAMES_FILE"
	# 予想もサイクルに連動: 蓄積リセット時に現予想を確定し、次サイクルで新規作成させる
	# (粛清やソ連建国で既にresolve済みの場合はファイルが消えているのでスキップされる)
	if [ -f "$TMP_STATE_DIR/current_prediction.json" ]; then
		# 粛清フラグがある場合は best_outcome=3 に強制（レース対策）
		# soren_loop / improve.sh の粛清検出ブロックが check_regression 後に即書き込む
		if [ -f "$TMP_STATE_DIR/regression_pending" ]; then
			python3 -c "
import json
f='$TMP_STATE_DIR/current_prediction.json'
try:
    d=json.load(open(f))
    if d.get('best_outcome',0) < 3:
        d['best_outcome']=3
        json.dump(d,open(f,'w'))
except Exception:
    pass
" 2>/dev/null || true
			rm -f "$TMP_STATE_DIR/regression_pending"
		fi
		# prediction_worker が state file を監視して cleanup する
	else
		rm -f "$TMP_STATE_DIR/regression_pending" 2>/dev/null || true
	fi
}

_reset_current_strategy_run() {
	local strategy_hash="$1"
	python3 - "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" <<'PY' >/dev/null 2>&1
import json
import sys

out_file, strategy_hash = sys.argv[1], sys.argv[2]
payload = {
    "hash": strategy_hash,
    "scores": [],
    "games_total": 0,
    "_recent_archives": [],
}
with open(out_file, "w") as f:
    json.dump(payload, f)
PY
}

_seed_current_strategy_run_from_rolling() {
	local strategy_hash="$1"
	[ -n "$strategy_hash" ] || return 1
	[ -f "$ROLLING_SCORES_FILE" ] || return 1
	python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" <<'PY' >/dev/null 2>&1
import json
import os
import sys

rolling_file, out_file, strategy_hash = sys.argv[1], sys.argv[2], sys.argv[3]
if not os.path.exists(rolling_file):
    raise SystemExit(1)
try:
    rolling = json.load(open(rolling_file))
except Exception:
    raise SystemExit(1)
entry = rolling.get(strategy_hash)
if not isinstance(entry, dict):
    raise SystemExit(1)
scores = []
for x in entry.get("scores", []) or []:
    try:
        scores.append(int(x))
    except Exception:
        pass
recent_archives = entry.get("_recent_archives", []) or []
if not isinstance(recent_archives, list):
    recent_archives = []
payload = {
    "hash": strategy_hash,
    "scores": scores[-20:],
    "games_total": int(entry.get("games_total", len(scores)) or len(scores)),
    "_recent_archives": recent_archives[-50:],
}
with open(out_file, "w") as f:
    json.dump(payload, f)
PY
}

_update_current_strategy_run() {
	local strategy_hash="$1" score="$2" archive_file="${3:-}"
	[ -n "$strategy_hash" ] || return 1
	local run_result=""
	run_result=$(python3 - "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" "$score" "$archive_file" "${CURRENT_RUN_SCORE_KEEP:-20}" "${HOT_STREAK_CURRENT_RUN_KEEP:-200}" "${HOT_STREAK_EXTEND_ENABLED:-1}" <<'PY' 2>/dev/null
import json
import os
import sys

run_file, strategy_hash, score, archive_file = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
try:
    normal_keep = int(sys.argv[5])
except Exception:
    normal_keep = 20
try:
    hot_keep = int(sys.argv[6])
except Exception:
    hot_keep = 200
hot_enabled = str(sys.argv[7]).strip() == "1"
normal_keep = max(1, normal_keep)
hot_keep = max(normal_keep, hot_keep)
if os.path.exists(run_file):
    try:
        run = json.load(open(run_file))
    except Exception:
        run = {}
else:
    run = {}

if run.get("hash") != strategy_hash:
    run = {
        "hash": strategy_hash,
        "scores": [],
        "games_total": 0,
        "_recent_archives": [],
    }

recent_archives = run.get("_recent_archives", [])
if not isinstance(recent_archives, list):
    recent_archives = []

if archive_file and archive_file in recent_archives:
    print(f"{strategy_hash}|{len(run.get('scores', []))}|{int(run.get('games_total', 0) or 0)}|dedup")
    raise SystemExit

scores = [int(x) for x in run.get("scores", [])]
prev_best = max(scores) if scores else None
scores.append(score)
keep = hot_keep if hot_enabled and prev_best is not None and score > prev_best else normal_keep
run["scores"] = scores[-keep:]
run["games_total"] = int(run.get("games_total", 0) or 0) + 1

def nation_progress(path):
    max_type = 0
    russia = False
    soviet = False
    if not path or not os.path.exists(path):
        return max_type, russia, soviet
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if row.get("russia_created"):
                    russia = True
                if row.get("soviet_created"):
                    soviet = True
                pieces = ((row.get("state_snapshot") or {}).get("pieces") or [])
                for piece in pieces:
                    try:
                        t = int(piece.get("type", 0) or 0)
                    except Exception:
                        continue
                    if t > max_type:
                        max_type = t
                    if t >= 15:
                        russia = True
                    if t >= 16:
                        soviet = True
    except Exception:
        pass
    return max_type, russia, soviet

if archive_file:
    recent_archives.append(archive_file)
    recent_archives = recent_archives[-50:]
run["_recent_archives"] = recent_archives
progress_archives = recent_archives[-len(run["scores"]):] if run["scores"] else []
progress = [nation_progress(path) for path in progress_archives]
run["max_types"] = [item[0] for item in progress]
run["russia_count"] = sum(1 for _, russia_created, _ in progress if russia_created)
run["soviet_count"] = sum(1 for _, _, soviet_created in progress if soviet_created)
run["best_max_type"] = max([int(run.get("best_max_type", 0) or 0)] + [item[0] for item in progress])

with open(run_file, "w") as f:
    json.dump(run, f)

print(f"{strategy_hash}|{len(run['scores'])}|{run['games_total']}|updated")
PY
)
	if [ -n "$run_result" ]; then
		local run_n="" run_total="" run_status=""
		IFS='|' read -r strategy_hash run_n run_total run_status <<<"$run_result"
		if [ "$run_status" = "dedup" ]; then
			log "[CURRENT-RUN] duplicate skip: hash=${strategy_hash} n=${run_n} total=${run_total} score=${score} file=${archive_file}"
		else
			log "[CURRENT-RUN] updated: hash=${strategy_hash} n=${run_n} total=${run_total} score=${score} file=${archive_file}"
		fi
	else
		log "[CURRENT-RUN] update failed: hash=${strategy_hash} score=${score}"
	fi
}

_is_rank1_hot_streak() {
	local current_hash=""
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	[ -n "$current_hash" ] || return 1
	python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" <<'PY' >/dev/null 2>&1
import json
import math
import os
import sys

rolling_file, current_run_file, anchor_file, current_hash, min_games_raw = sys.argv[1:6]
try:
    min_games = int(min_games_raw)
except Exception:
    min_games = 12

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    xs = [int(x) for x in scores]
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    std = math.sqrt(sum((x - mean) ** 2 for x in xs) / n) if n > 1 else 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "n": n}

def key(m):
    if not m:
        return (-10**18, -10**18, -10**18, -10**18)
    return (float(m.get("comp", 0.0)), float(m.get("p50", 0.0)), float(m.get("p25", 0.0)), int(m.get("n", 0)))

def scores_from(entry):
    out = []
    for raw in (entry or {}).get("scores", []) or []:
        try:
            out.append(int(raw))
        except Exception:
            pass
    return out

rolling = load_json(rolling_file)
run = load_json(current_run_file)
current_scores = scores_from(run if str(run.get("hash", "") or "") == current_hash else rolling.get(current_hash, {}))
if len(current_scores) < min_games:
    raise SystemExit(1)

# "更新し続けている" は直近ゲームがこの current run の評価スコア自己ベストを
# 厳密に更新したこと。同点では延長しない。
if len(current_scores) < 2 or current_scores[-1] <= max(current_scores[:-1]):
    raise SystemExit(1)

current_metrics = metrics(current_scores)
if not current_metrics:
    raise SystemExit(1)

ranked = []
for h, data in rolling.items():
    scores = scores_from(data)
    if len(scores) < min_games:
        continue
    ranked.append((key(metrics(scores)), h))

anchor = load_json(anchor_file)
anchor_hash = str(anchor.get("hash", "") or "")
anchor_metrics = {
    "comp": float(anchor.get("comp", 0.0) or 0.0),
    "p50": float(anchor.get("p50", 0.0) or 0.0),
    "p25": float(anchor.get("p25", 0.0) or 0.0),
    "lcb": float(anchor.get("lcb", 0.0) or 0.0),
    "n": int(anchor.get("n", 0) or 0),
} if anchor_hash else None
if anchor_metrics:
    ranked.append((key(anchor_metrics), anchor_hash))
ranked.append((key(current_metrics), current_hash))
ranked.sort(reverse=True)

top_hash = ranked[0][1] if ranked else ""
raise SystemExit(0 if top_hash == current_hash else 1)
PY
}

_rolling_keep_limit_for_hash() {
	local target_hash="$1"
	if [ "${HOT_STREAK_EXTEND_ENABLED:-1}" = "1" ] && [ -n "$target_hash" ] && _is_rank1_hot_streak; then
		echo "${HOT_STREAK_ROLLING_KEEP:-200}"
	else
		echo "${ROLLING_SCORE_KEEP:-20}"
	fi
}


record_completed_game_for_adaptive_improvement() {
	local archive_file="$1" score="$2" soviet="$3" russia="${4:-false}"
	local played_hash="" current_hash=""
	if [ -f "${STRATEGY_FILE}.game_snapshot" ]; then
		played_hash=$(python3 extract_decide_hash.py "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || echo "")
	fi
	if [ -z "$played_hash" ] && [ -f "$STRATEGY_FILE" ]; then
		played_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	fi
	if [ -f "$STRATEGY_FILE" ]; then
		current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	fi

	update_rolling_scores "$score" "$archive_file"

	if [ -n "$played_hash" ] && [ -n "$current_hash" ] && [ "$played_hash" != "$current_hash" ]; then
		log "[IMPROVE] current戦略と異なる試合を検出: played=${played_hash:0:8} current=${current_hash:0:8} → queuedをリセットしてこの試合は蓄積しない"
		_clear_accumulated_data
		_reset_current_strategy_run "$current_hash"
	else
		if [ -n "$current_hash" ]; then
			_update_current_strategy_run "$current_hash" "$score" "$archive_file"
		fi
		accumulate_game_data "$archive_file" "$score" "$soviet" "$played_hash" "$russia"
	fi

	if ! _has_active_branch; then
		_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	fi
}

_start_improvement_job() {
	local all_history_files="$1" all_scores="$2" any_soviet="$3" acc_count="$4" reason="$5"

	# 手動改善モード: プロセスを起動せず待機状態にする
	if [[ -f "$TMP_STATE_DIR/manual_improve_mode" ]]; then
		local strategy_hash
		strategy_hash=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
		_write_improve_state "manual" "0" "$strategy_hash" "manual_wait" "0" "手動改善待ち" "$(date +%s)"
		log "[IMPROVE] 手動改善モード: strategy.py を編集後 ./manual_improve_off.sh を実行してください"
		# soren91は起動する（手動改善中の代打）
		soren91_start
		# OBS: 改善中オーバーレイ表示
		_improve_overlay_show
		return 0
	fi

	# spawn 排他 mutex 取得 (dual-spawner 二重起動レース防止)
	# 別 spawner が spawn 中なら return 1 でスキップ (success 扱いにせず
	# caller の last_improve_failed_at クリアを誤発火させない)
	if ! _acquire_spawn_lock; then
		log "[IMPROVE] spawn lock を別 spawner が保持中 → 二重起動回避でスキップ"
		return 1
	fi

	# 既存の eloop_improve プロセスが残っていないか確認
	# -f exact match + 自プロセス除外 (grep -v $$) で誤殺防止
	local stale_pids
	stale_pids=$(pgrep -f "eloop_improve\.sh" 2>/dev/null | grep -vw "$$" || true)
	if [ -n "$stale_pids" ]; then
		log "[IMPROVE] WARNING: 既存の eloop_improve プロセス検出 (PIDs: $stale_pids) → kill"
		echo "$stale_pids" | while read -r spid; do
			kill "$spid" 2>/dev/null || true
		done
		sleep 1
	fi

	if [ "$reason" = "post_regression" ]; then
		log "[IMPROVE] 回帰ロールバック直後の即時改善を開始"
	else
		log "[IMPROVE] ${acc_count}試合分のデータで改善開始"
	fi

	# 戦略ハッシュ記録
	local strategy_hash
	strategy_hash=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
	local improve_ai_log="$IMPROVE_AI_LOG_FILE"
	mkdir -p "$(dirname "$improve_ai_log")" 2>/dev/null || true
	: >"$improve_ai_log"
	printf '[%s] [IMPROVE] job start reason=%s game=%s scores=%s\n' \
		"$(date '+%H:%M:%S')" "$reason" "${GAME_NUM:-?}" "${all_scores:-}" >>"$improve_ai_log" 2>/dev/null || true
	_improve_overlay_generate_once
	if [ -x ./overlay_notify.sh ]; then
		local _ov_anchor
		_ov_anchor=$(python3 -c "import json;d=json.load(open('${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}'));print('%s comp=%.0f'%(d.get('hash','?')[:8],d.get('comp',0)))" 2>/dev/null || echo "")
		./overlay_notify.sh worker "改善開始 (${reason})" "reason=${reason} game=${GAME_NUM:-?} hash=${strategy_hash} acc=${acc_count:-?}${_ov_anchor:+ | anchor=${_ov_anchor}} scores=${all_scores:-}" "info" >/dev/null 2>&1 || true
	fi

	# デーモンコンテキストではファイルからフォールバック読み取り
	[ "${GAME_NUM:-0}" -eq 0 ] && GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ "${LAST_TURNS:-0}" -eq 0 ] && LAST_TURNS=$(cat "tmp/state/last_turns.txt" 2>/dev/null || echo 0)

	# バックグラウンド改善開始 (reason は wildcard モード判定のため eloop_improve.sh に伝搬)
	RUN_CMD_LOG_FILE="$improve_ai_log" ./eloop_improve.sh "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" "$reason" &
	IMPROVE_PID=$!
	local _pid_birth_epoch
	_pid_birth_epoch=$(ps -p "$IMPROVE_PID" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0)

	# 起動成功を確認してから状態更新
	if kill -0 "$IMPROVE_PID" 2>/dev/null; then
		local _overlay_pid=""
		rm -f "$TMP_STATE_DIR/handover_announced" 2>/dev/null || true
		_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash" "boot" "1" "job_started" "$(date +%s)" "$_pid_birth_epoch"
		_overlay_pid=$(_improve_overlay_watch_start "$IMPROVE_PID")
		# spawn 完了・state=running 書込済 → 既存ガードが他 spawner を弾くので
		# spawn mutex の役目は終了。daemon-mode の長い inline wait の前に解放。
		_release_spawn_lock
		if [ "$reason" = "post_regression" ]; then
			log "[IMPROVE] 回帰ロールバック後の改善開始 (PID=$IMPROVE_PID, base=${REGRESSION_ROLLBACK_HASH:-unknown})"
		elif [ "${IMPROVE_DAEMON_MODE:-0}" = "1" ]; then
			log "[IMPROVE] デーモンモード: フォアグラウンド実行開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		else
			log "[IMPROVE] バックグラウンド開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		fi
		# OBS: 改善中オーバーレイ表示
		_improve_overlay_show
		# soren91 (メリケンAI) を起動 — 中華AI改善中の代打プレイ
		soren91_start
		if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
			# Twitch チャットに戦略改善開始を通知
			enqueue_chat_message "中華AIが戦略を改善中。その間、メリケンAIがソ連ゲーム91で同志を迎え撃ちます。挑戦お待ちしています ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" "improve"
		else
			log "[IMPROVE] soren91 は停止処理中のため起動通知をスキップ"
		fi
		# デーモンモードではフォアグラウンド実行（完了まで wait → 即 harvest 可能になる）
		# run_cmd が stdout/stderr をログファイルにリダイレクトするため、
		# tail -f でログをターミナルに中継する
		if [ "${IMPROVE_DAEMON_MODE:-0}" = "1" ]; then
			tail -n +1 -f "$improve_ai_log" &
			local _tail_pid=$!
			wait "$IMPROVE_PID"
			local _wait_rc=$?
			kill "$_tail_pid" 2>/dev/null; wait "$_tail_pid" 2>/dev/null || true
			if [ -n "$_overlay_pid" ]; then
				kill "$_overlay_pid" 2>/dev/null; wait "$_overlay_pid" 2>/dev/null || true
			fi
			_improve_overlay_generate_once
			log "[IMPROVE] フォアグラウンド実行完了 (PID=$IMPROVE_PID, rc=${_wait_rc})"
			# daemon mode: wait 完了後に即 harvest して状態を idle に遷移
			# (次の poll で "running"+死PID を拾って繰り返し発火するのを防ぐ)
			check_and_harvest_improvement
		fi
		return 0
	else
		log "[IMPROVE] 起動失敗 (PID=$IMPROVE_PID 即死)"
		IMPROVE_PID=0
		_release_spawn_lock
		return 1
	fi
}

trigger_adaptive_improvement() {
	type reload_runtime_toggles >/dev/null 2>&1 && reload_runtime_toggles
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] trigger_adaptive_improvementをスキップ（建国後停止中）"
		return
	fi

	# ロックファイルが存在しない場合は何もしない（メインループが作成する）
	[ -f "$IMPROVE_LOCK_FILE" ] || return 0

	# 既に改善プロセス中（状態ファイルで確認）
	local state status
	state=$(_read_improve_state)
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)
	if [ "$status" = "running" ] || [ "$status" = "manual" ]; then
		log "[IMPROVE] 改善中 (status=$status) → スキップ"
		return 0
	fi

	# レートリミット指数バックオフ
	if [ -f "$TMP_STATE_DIR/rate_limit_backoff" ]; then
		local _rl_count _rl_ts _rl_now _rl_wait _rl_exp
		_rl_count=$(sed -n '1p' "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || echo 1)
		_rl_ts=$(sed -n '2p' "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || echo 0)
		_rl_now=$(date +%s)
		_rl_exp=$((_rl_count - 1 > 5 ? 5 : _rl_count - 1))
		_rl_wait=$((300 * (1 << _rl_exp)))
		if [ $((_rl_now - _rl_ts)) -lt "$_rl_wait" ]; then
			log "[IMPROVE] rate-limit backoff中 (残$((_rl_wait - (_rl_now - _rl_ts)))秒, count=${_rl_count})"
			return
		else
			log "[IMPROVE] rate-limit backoff終了 → リトライ許可"
			rm -f "$TMP_STATE_DIR/rate_limit_backoff"
		fi
	fi

	# ロックファイルから蓄積データを読む
	local lock_data acc_count all_history_files all_scores any_soviet
	lock_data=$(cat "$IMPROVE_LOCK_FILE" 2>/dev/null) || {
		log "[IMPROVE] ロックファイル読み込み失敗 → スキップ"
		rm -f "$IMPROVE_LOCK_FILE"
		return 1
	}
	if ! printf '%s' "$lock_data" | python3 -m json.tool >/dev/null 2>&1; then
		log "[IMPROVE] ロックファイルが空または壊れているため削除"
		rm -f "$IMPROVE_LOCK_FILE"
		return 1
	fi
	acc_count=$(echo "$lock_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo 0)
	all_history_files=$(echo "$lock_data" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('files',[])))" 2>/dev/null)
	all_scores=$(echo "$lock_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scores',''))" 2>/dev/null)
	any_soviet=$(echo "$lock_data" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet',False) else 'false')" 2>/dev/null)

	# F: stagnation 連続発生時は wildcard モードに切替
	local improve_reason="normal"
	improve_reason=$(echo "$lock_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('improve_reason','normal'))" 2>/dev/null || echo "normal")
	case "$improve_reason" in
	normal|post_regression|wildcard) ;;
	*) improve_reason="normal" ;;
	esac
	if [ "$improve_reason" = "post_regression" ]; then
		log "[IMPROVE] ロールバック直後の失敗バッチを改善入力として使用"
	fi
	# 粛清カスケード中は毎サイクル post_regression で起動するため、ゲートを
	# normal 限定にすると WILDCARD(脱出弾)に構造的に永遠に入れない。
	# 回帰ストリーク/停滞が閾値超なら post_regression でも WILDCARD へ昇格を
	# 許可する (まさに粛清連鎖からの脱出が WILDCARD の目的)。
	if { [ "$improve_reason" = "normal" ] || [ "$improve_reason" = "post_regression" ]; } && [ "${WILDCARD_ENABLED:-0}" = "1" ] && [ -f "${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" ]; then
		local stag rstreak
		stag=$(python3 -c "
import json,sys
try:
    print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('consecutive_no_improve', 0)))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
		rstreak=$(python3 -c "
import json,sys
try:
    print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('regression_streak', 0)))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
		if [ "$stag" -ge "${WILDCARD_TRIGGER_STAGNATION:-3}" ]; then
			improve_reason="wildcard"
			log "[WILDCARD] stagnation=$stag >= ${WILDCARD_TRIGGER_STAGNATION:-3} → wildcard モードで起動"
		elif [ "$rstreak" -ge "${WILDCARD_REGRESSION_STREAK:-4}" ]; then
			# counter 非依存 回帰ストリーク経路 (OK_BEAT マスク回避)。
			# churn 緩和: cooldown マーカ経過時のみ発火し発火時に更新。
			local _wccd="${WILDCARD_STREAK_COOLDOWN_FILE:-tmp/state/.wildcard_streak_cooldown}"
			local _wccd_sec="${WILDCARD_STREAK_COOLDOWN_SEC:-1800}" _wccd_ok=1
			if [ -f "$_wccd" ]; then
				local _wm _wnow
				_wm=$(stat -f %m "$_wccd" 2>/dev/null || stat -c %Y "$_wccd" 2>/dev/null || echo 0)
				_wnow=$(date +%s)
				[ "$(( _wnow - _wm ))" -lt "$_wccd_sec" ] && _wccd_ok=0
			fi
			if [ "$_wccd_ok" -eq 1 ]; then
				improve_reason="wildcard"
				: >"$_wccd" 2>/dev/null || true
				log "[WILDCARD] regression_streak=$rstreak >= ${WILDCARD_REGRESSION_STREAK:-4} (counter非依存) → wildcard モード起動 (cooldown ${_wccd_sec}s)"
			else
				log "[WILDCARD] regression_streak=$rstreak だが cooldown 中 → 今回は通常改善 (churn緩和)"
			fi
		fi
	fi

	if _start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "$improve_reason"; then
		rm -f "$TMP_STATE_DIR/last_improve_failed_at"
	fi
}
