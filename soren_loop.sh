#!/bin/bash
# soren_loop.sh - Soren Evolution Loop 親スクリプト
#
# ユーザーが実行するエントリーポイント。安定した薄いラッパー。
# eloop.sh が AI 改善で書き換わっても、このスクリプトは生き残り、
# 次のイテレーションで自動的に新しいコードを読み込む。
#
# アーキテクチャ:
#   soren_loop.sh (このファイル) — メインループ、初期化、クリーンアップ
#   eloop_lib.sh  — 全モジュールsource shim (core/, strategy/, broadcast/, infra/)
#   eloop.sh      — 1試合のゲームプレイ関数 (毎試合 source で最新版を読み込み)
#   eloop_improve.sh — バックグラウンド改善サブプロセス

# 親プロセスから SIGINT/SIGTERM 無視状態を継承していると Ctrl-C が効かない。
# その場合でも確実に停止できるよう、起動直後にシグナル既定動作へ戻して再execする。
if [ -z "${SOREN_SIGRESET_DONE:-}" ]; then
	export SOREN_SIGRESET_DONE=1
	exec python3 - "$0" "$@" <<'PY'
import os
import signal
import sys

targets = {signal.SIGINT, signal.SIGTERM}
if hasattr(signal, "SIGQUIT"):
    targets.add(signal.SIGQUIT)

# 1) 無視ハンドラ継承を解除
for sig in targets:
    try:
        signal.signal(sig, signal.SIG_DFL)
    except Exception:
        pass

# 2) ブロックされたシグナルマスクも解除（Ctrl-Cが届かないケース対策）
try:
    signal.pthread_sigmask(signal.SIG_UNBLOCK, targets)
except Exception:
    pass

os.execv("/bin/bash", ["/bin/bash", sys.argv[1], *sys.argv[2:]])
PY
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 端末起動時は Ctrl-C キー定義を標準化し、届かない状況を明示する
if [ -t 0 ]; then
	# screen/tmux配下などで ^C が文字入力扱いになるケースを防ぐ
	stty intr '^C' isig 2>/dev/null || true
	_self_pgid=$(ps -p $$ -o pgid= 2>/dev/null | tr -d ' ')
	_tty_pgid=$(ps -p $$ -o tpgid= 2>/dev/null | tr -d ' ')
	if [ -n "$_self_pgid" ] && [ -n "$_tty_pgid" ] && [ "$_self_pgid" != "$_tty_pgid" ]; then
		echo "[WARN] この端末では soren_loop が前面PGではないため Ctrl-C は届きません。" >&2
		echo "[WARN] soren_loop を実行した端末に戻るか、fg で前面に戻して停止してください。" >&2
	fi
fi

# --- 多重起動防止 (mkdir-based アトミックロック) ---
LOCKDIR="tmp/.soren_loop.lock"
mkdir -p tmp
if ! mkdir "$LOCKDIR" 2>/dev/null; then
	old_pid=$(cat "$LOCKDIR/pid" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		echo "ERROR: soren_loop.sh is already running (PID=$old_pid). Aborting."
		exit 1
	fi
	# stale lock — force acquire
	rm -rf "$LOCKDIR"
	mkdir "$LOCKDIR" || { echo "ERROR: failed to acquire lock."; exit 1; }
fi
echo $$ > "$LOCKDIR/pid"
export SOREN_MAIN_PID="$$"
rm -f tmp/stop

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ読み込み ---
source ./eloop_lib.sh

# --- グローバル状態 ---
GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
IMPROVE_PID=0
HALT_STRATEGY_AFTER_SOVIET=0
STOP_REQUESTED=0
_SOREN_CLEANED_UP=0
DEFER_NEXT_GAME_PREP=0
SOREN_PAUSE_LOG_INTERVAL_SEC="${SOREN_PAUSE_LOG_INTERVAL_SEC:-900}"
_SOREN_LAST_PAUSE_LOG_TS=0
_SOREN_LAST_PAUSE_LOG_KEY=""
SOREN_OVERLAY_AUTORECOVER_ENABLED="${SOREN_OVERLAY_AUTORECOVER_ENABLED:-1}"
SOREN_OVERLAY_AUTORECOVER_INTERVAL_SEC="${SOREN_OVERLAY_AUTORECOVER_INTERVAL_SEC:-15}"
SOREN_LOOP_OVERLAY_REFRESH_SEC="${SOREN_LOOP_OVERLAY_REFRESH_SEC:-2}"
SOREN_IMPROVE_MONITOR_INTERVAL_SEC="${SOREN_IMPROVE_MONITOR_INTERVAL_SEC:-15}"
_SOREN_OVERLAY_RECOVER_TS=0
_SOREN_OVERLAY_RECOVER_BOOTSTRAPPED=0
_SOREN_IMPROVE_MONITOR_TS=0

log_pause_throttled() {
	local key="${1:-pause}" message="${2:-[PAUSE]}"
	local now interval safe_key state_file last_ts
	interval="${SOREN_PAUSE_LOG_INTERVAL_SEC:-900}"
	case "$interval" in ''|*[!0-9]*) interval=900 ;; esac
	now=$(date +%s)
	safe_key=$(printf '%s' "$key" | tr -cd 'A-Za-z0-9_.-')
	[ -n "$safe_key" ] || safe_key="pause"
	state_file="${TMP_STATE_DIR:-tmp/state}/pause_log_${safe_key}.ts"
	mkdir -p "$(dirname "$state_file")" 2>/dev/null || true
	last_ts=$(cat "$state_file" 2>/dev/null || echo 0)
	case "$last_ts" in ''|*[!0-9]*) last_ts=0 ;; esac
	if [ $((now - last_ts)) -ge "$interval" ]; then
		_SOREN_LAST_PAUSE_LOG_KEY="$key"
		_SOREN_LAST_PAUSE_LOG_TS="$now"
		printf '%s\n' "$now" >"$state_file" 2>/dev/null || true
		log "$message"
	fi
}

notify_rank1_hot_streak_extension() {
	local acc_count="${1:-0}" reason="${2:-cycle}"
	local current_hash marker_file marker_key previous_key title body chat_msg
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	[ -n "$current_hash" ] || current_hash="unknown"
	marker_file="${TMP_STATE_DIR:-tmp/state}/rank1_hot_streak_notified"
	marker_key="$current_hash"
	previous_key=$(cat "$marker_file" 2>/dev/null || true)
	[ "$previous_key" = "$marker_key" ] && return 0

	title="rank1 hot streak 延長 (game ${GAME_NUM:-?})"
	body="current=${current_hash:0:12} | 蓄積=${acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} | reason=${reason} | 1位で自己ベスト更新中のため改善サイクルを延長"
	chat_msg="現在の戦略が1位で自己ベスト更新中のため、改善サイクルを延長してこのまま続行します。蓄積 ${acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} games / hash ${current_hash:0:8}"

	if [ -x ./overlay_notify.sh ]; then
		./overlay_notify.sh worker "$title" "$body" "info" >/dev/null 2>&1 || true
	fi
	enqueue_chat_message "$chat_msg" "hot_streak" 4 || true
	mkdir -p "$(dirname "$marker_file")" 2>/dev/null || true
	printf '%s\n' "$marker_key" >"$marker_file" 2>/dev/null || true
}

_evolution_flow_notify() {
	local step="${1:-flow}" title="${2:-改善フロー}" body="${3:-}" chat="${4:-}" level="${5:-info}"
	local full_title="改善フロー: ${title}"
	[ -n "$body" ] || body="$step"
	if [ -x ./overlay_notify.sh ]; then
		./overlay_notify.sh worker "$full_title" "$body" "$level" >/dev/null 2>&1 || true
	fi
	case "$step" in
		game_finished | regression_check | no_rollback | twelve_game_improve)
			chat=""
			;;
	esac
	if [ -n "$chat" ]; then
		enqueue_chat_message "$chat" "improve_flow" 4 || true
	fi
}

_post_regression_route() {
	python3 - \
		"${REGRESSION_ROLLBACK_RESULT:-}" \
		"${REGRESSION_ROLLBACK_HASH:-}" \
		"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
		"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
		"${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" \
		"${WILDCARD_REGRESSION_STREAK:-3}" <<'PY' 2>/dev/null || echo "post_regression|fallback|0|0|0"
import json
import os
import re
import sys

result, rollback_hash, current_file, rolling_file, stagnation_file, threshold_raw = sys.argv[1:7]

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def metric(name):
    m = re.search(rf"{re.escape(name)}=(-?\d+)", result or "")
    return as_int(m.group(1), 0) if m else 0

try:
    threshold = max(1, int(threshold_raw))
except Exception:
    threshold = 3

stagnation = load(stagnation_file)
rstreak = as_int(stagnation.get("regression_streak", 0), 0)
rolling = load(rolling_file)
current = load(current_file)
target = rolling.get(rollback_hash) if rollback_hash else {}
if not isinstance(target, dict):
    target = {}
if rollback_hash and str(current.get("hash", "") or "") == rollback_hash:
    merged = dict(target)
    for key in ("russia_count", "soviet_count", "best_max_type"):
        merged[key] = max(as_int(merged.get(key, 0), 0), as_int(current.get(key, 0), 0))
    target = merged

target_russia = as_int(target.get("russia_count", 0), 0)
target_best = as_int(target.get("best_max_type", 0), 0)
target_has_progress = target_russia > 0 or target_best >= 15

anchor_russia = metric("anchor_russia")
curr_russia = metric("curr_russia")
anchor_best = metric("anchor_best_max_type")
curr_best = metric("curr_best_max_type")
curr_russia_seen = "curr_russia=" in (result or "")
curr_best_seen = "curr_best_max_type=" in (result or "")
objective_loss = (
    "objective_regression" in result
    or "lost_russia_path" in result
    or (curr_russia_seen and curr_russia <= 0)
    or (anchor_russia > 0 and curr_russia <= 0)
    or (anchor_best > 0 and curr_best > 0 and curr_best < anchor_best)
    or (curr_best_seen and anchor_best >= 15 and curr_best < 15)
)

if target_has_progress:
    mode = "revalidate"
    detail = f"target_progress_russia={target_russia}_best={target_best}"
elif objective_loss and rstreak >= threshold:
    mode = "direct_escape"
    detail = f"objective_loss_rstreak={rstreak}_target_russia={target_russia}_best={target_best}"
else:
    mode = "post_regression"
    detail = f"rstreak={rstreak}_objective_loss={int(objective_loss)}"

print(f"{mode}|{detail}|{rstreak}|{int(objective_loss)}|{int(target_has_progress)}")
PY
}

_expire_rate_limit_backoff_if_elapsed() {
	local file="${TMP_STATE_DIR:-tmp/state}/rate_limit_backoff"
	[ -f "$file" ] || return 0
	local count ts now exp wait
	count=$(sed -n '1p' "$file" 2>/dev/null || echo 1)
	ts=$(sed -n '2p' "$file" 2>/dev/null || echo 0)
	case "$count" in ''|*[!0-9]*) count=1 ;; esac
	case "$ts" in ''|*[!0-9]*) ts=0 ;; esac
	now=$(date +%s)
	exp=$((count - 1))
	[ "$exp" -lt 0 ] && exp=0
	[ "$exp" -gt 5 ] && exp=5
	wait=$((300 * (1 << exp)))
	if [ "$ts" -le 0 ] || [ $((now - ts)) -ge "$wait" ]; then
		log "[IMPROVE] rate-limit backoff期限切れ → 早期脱出判定のため解除 (count=${count}, wait=${wait}s)"
		rm -f "$file" "${TMP_STATE_DIR:-tmp/state}/rate_limit_backoff_last_log" 2>/dev/null || true
	fi
}

_ensure_status_overlays_watchers() {
	[ "${SOREN_OVERLAY_AUTORECOVER_ENABLED:-1}" = "1" ] || return 0
	local now interval
	now=$(date +%s)
	interval="${SOREN_OVERLAY_AUTORECOVER_INTERVAL_SEC:-15}"
	case "$interval" in
	''|*[!0-9]*) interval=15 ;;
	esac
	if [ "${_SOREN_OVERLAY_RECOVER_BOOTSTRAPPED:-0}" -eq 1 ] && [ $((now - _SOREN_OVERLAY_RECOVER_TS)) -lt "$interval" ]; then
		return 0
	fi
	_SOREN_OVERLAY_RECOVER_BOOTSTRAPPED=1
	_SOREN_OVERLAY_RECOVER_TS=$now
	./show_status_g.sh --html-start "${SOREN_LOOP_OVERLAY_REFRESH_SEC:-2}" >/dev/null 2>&1 || true
	./show_status.sh --html-start "${SOREN_LOOP_OVERLAY_REFRESH_SEC:-2}" >/dev/null 2>&1 || true
		# stats/ops are monitoring overlays. Keep them visible even while the
		# improve overlay is shown; hiding them here races with soren91 layout
		# switching and causes visible flicker.
		./show_status_g.sh --html-obs show >/dev/null 2>&1 || true
		./show_status.sh --html-obs show >/dev/null 2>&1 || true
	}

_run_improve_runtime_monitor() {
	[ -x ./monitor_improve_runtime.sh ] || return 0
	local now interval
	now=$(date +%s)
	interval="${SOREN_IMPROVE_MONITOR_INTERVAL_SEC:-15}"
	case "$interval" in
	''|*[!0-9]*) interval=15 ;;
	esac
	if [ "${_SOREN_IMPROVE_MONITOR_TS:-0}" -gt 0 ] && [ $((now - _SOREN_IMPROVE_MONITOR_TS)) -lt "$interval" ]; then
		return 0
	fi
	_SOREN_IMPROVE_MONITOR_TS=$now
	./monitor_improve_runtime.sh >/dev/null 2>&1 ||
		log "[MONITOR] improve runtime monitor skipped/failed"
}

_cleanup_once() {
	local reason="${1:-unknown}"
	local current_pid lock_pid
	current_pid=$(_my_pid)
	if [ -n "${SOREN_MAIN_PID:-}" ] && [ "$current_pid" != "${SOREN_MAIN_PID}" ]; then
		return 0
	fi
	lock_pid=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
	case "$lock_pid" in
	''|*[!0-9]*) lock_pid="" ;;
	esac
	if [ -n "$lock_pid" ] && [ "$lock_pid" != "${SOREN_MAIN_PID:-}" ]; then
		log "[CLEANUP] lock owner changed (owner=${lock_pid}, self=${SOREN_MAIN_PID:-?}) -> skip global cleanup"
		return 0
	fi
	if [ "${_SOREN_CLEANED_UP:-0}" -eq 1 ]; then
		return 0
	fi
	_SOREN_CLEANED_UP=1
	cleanup_all "$reason"
}

_handle_stop_signal() {
	local sig="${1:-INT}"
	STOP_REQUESTED=1
	rm -f tmp/stop
	log "[SIGNAL] ${sig} を受信: 停止処理に入ります"
	trap - INT TERM
	_cleanup_once "signal:${sig}"
	trap - EXIT
	exit 130
}

_handle_exit() {
	local rc=$?
	if [ "${STOP_REQUESTED:-0}" -eq 1 ]; then
		return 0
	fi
	_cleanup_once "exit:${rc}"
}

_abort_if_interrupted() {
	local rc="${1:-0}"
	local stage="${2:-unknown}"
	if [ "${STOP_REQUESTED:-0}" -eq 1 ] || [ "$rc" -eq 130 ] || [ "$rc" -eq 143 ]; then
		log "[SIGNAL] ${stage} で割り込みを検出 (rc=${rc})"
		exit 130
	fi
	return 0
}

_exit_if_lock_owner_changed() {
	local stage="${1:-loop}"
	local lock_pid=""
	lock_pid=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
	case "$lock_pid" in
	''|*[!0-9]*)
		log "[LOCK] ${stage}: lock owner is missing/invalid -> stop this loop (self=${SOREN_MAIN_PID:-?})"
		STOP_REQUESTED=1
		trap - EXIT
		exit 130
		;;
	esac
	if [ "$lock_pid" != "${SOREN_MAIN_PID:-$$}" ]; then
		log "[LOCK] ${stage}: another soren_loop owns the lock (owner=${lock_pid}, self=${SOREN_MAIN_PID:-?}) -> stop duplicate loop"
		STOP_REQUESTED=1
		trap - EXIT
		exit 130
	fi
	return 0
}

_run_scheduled_meriken_time_window() {
	local reason="${1:-scheduled}"
	local start_log="${2:-[MERIKEN_TIME] メリケンAIタイム開始}"
	local end_epoch="" end_label=""
	if command -v scheduled_meriken_time_begin >/dev/null 2>&1; then
		end_epoch=$(scheduled_meriken_time_begin "$reason" 2>/dev/null || true)
	fi
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	if [ "${end_epoch:-0}" -le "$(date +%s)" ]; then
		log "[MERIKEN_TIME] 定時枠の終了時刻を過ぎているため開始しない"
		soren91_stop 2>/dev/null || true
		return 1
	fi
	if command -v scheduled_meriken_time_end_label >/dev/null 2>&1; then
		end_label=$(scheduled_meriken_time_end_label "$end_epoch" 2>/dev/null || true)
	fi
	if [ -n "$end_label" ]; then
		log "${start_log} (until ${end_label})"
	else
		log "${start_log}"
	fi
	while [ "$(date +%s)" -lt "$end_epoch" ]; do
		[ -f tmp/stop ] && break
		sleep 15
	done
	log "[MERIKEN_TIME] メリケンAIタイム終了"
	soren91_stop 2>/dev/null || true
	return 0
}


# --- 初期化 ---
log "=== Soren Evolution Loop ==="
log "strategy.py → 1game → adaptive improve → repeat"

# クリーンアップ trap
trap '_handle_exit' EXIT
trap '_handle_stop_signal INT' INT
trap '_handle_stop_signal TERM' TERM

# 前回中断時のリカバリ
recover_strategy_backup

# 初期バリデーション
if [ ! -f "$STRATEGY_FILE" ]; then
	log "ERROR: $STRATEGY_FILE が見つかりません"
	exit 1
fi
if ! validate_strategy; then
	log "ERROR: 初期バリデーション失敗"
	exit 1
fi
# validate_strategy may inject the runtime deadline guard. If that changes
# decide() hash, keep the live run bucket aligned before any new score lands.
_validated_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
_current_run_hash=$(python3 -c "import json; print(json.load(open('$CURRENT_STRATEGY_RUN_FILE')).get('hash',''))" 2>/dev/null || echo "")
if [ -n "$_validated_hash" ] && [ "$_validated_hash" != "$_current_run_hash" ]; then
	log "[CURRENT-RUN] validation後hash同期: ${_current_run_hash:-none} -> $_validated_hash"
	_reset_current_strategy_run "$_validated_hash"
fi

# 前回中断した改善プロセスの状態復元
check_and_harvest_improvement

# MOVE状態待ち
# 起動直後に前回試合の STOP/GAMEOVER が残っている場合は、ただ MOVE を待つと
# retry が送られず停止したように見えるため、明示的に次ゲームへ進める。
if is_game_over; then
	log "[STARTUP] GAMEOVER/STOP 検出 → retry送信"
	send_retry
	wait_rc=$?
else
	wait_for_move
	wait_rc=$?
	if [ "$wait_rc" -ne 0 ] && is_game_over; then
		log "[STARTUP] MOVE待機中に GAMEOVER/STOP 検出 → retry送信"
		send_retry
		wait_rc=$?
	fi
fi
_abort_if_interrupted "$wait_rc" "wait_for_move(initial)"
if [ "$wait_rc" -ne 0 ]; then
	log "ゲームが起動していません"
	exit 1
fi
_ensure_status_overlays_watchers
_run_improve_runtime_monitor

# --- メインループ: 1試合ずつ ---
while true; do
	# stop-file チェック (stop_soren.sh からの停止要求)
	if [ -f tmp/stop ]; then
		log "[STOP] Stop file detected"
		rm -f tmp/stop
		exit 130
	fi
	_ensure_status_overlays_watchers
	_run_improve_runtime_monitor

	# .env を毎試合再読込（再起動なしで設定変更を反映）
	[ -f .env ] && set -a && . ./.env && set +a

	# eloop_lib.sh は全モジュールをsourceするshim
	if ! source ./eloop_lib.sh 2>/dev/null; then
		log "WARNING: eloop_lib.sh の読み込みに失敗 (前回の定義で継続)"
	fi
	if ! source ./eloop.sh 2>/dev/null; then
		log "WARNING: eloop.sh の読み込みに失敗 (前回の定義で続行)"
	fi
	_expire_rate_limit_backoff_if_elapsed
	_exit_if_lock_owner_changed "before_game"

	# ゲーム番号を毎試合読み直す
	GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

	# 改善中pauseループ内でも stale/完了した改善ジョブを確実に回収する。
	# improve_daemon は改善開始後に wait でブロックするため、
	# ここで watchdog/harvest を回さないと running 状態が残留しうる。
	check_and_harvest_improvement

	# ソ連建国後は strategy 実行を止め、コメント系のみ維持する
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] strategy停止中: コメント返し/読み上げのみ継続"
		sleep 5
		continue
	fi

	# 改善中は中華AIのゲームプレイを一時停止 (soren91が代わりにプレイ中)
	if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
		if command -v soren91_is_running >/dev/null 2>&1 && ! soren91_is_running 2>/dev/null; then
			soren91_start 2>/dev/null || true
		fi
		command -v _soren91_switch_obs_layout >/dev/null 2>&1 && _soren91_switch_obs_layout meriken 2>/dev/null || true
		log_pause_throttled "manual_meriken_mode" "[PAUSE] manual_meriken_mode: ゲームプレイ一時停止 (メリケンAI手動モード)"
		sleep 10
		continue
	fi
	if _is_improve_running; then
		# WILDCARD 改善 (AI不使用・隔離評価) は本線ゲームを止め、
		# soren91 代打も立てない。WILDCARD PARALLEL 中は OBS に候補3面
		# (wildcardParallelCand1/2/3) を出し、本線は見えない裏で進ませない。
		# 代打起動→完了時bridge再起動が commands 経路 desync=空転の発生源。
		_pause_reason=$(python3 -c "import json,sys
for path in sys.argv[1:]:
    try:
        reason=json.load(open(path, encoding='utf-8')).get('improve_reason') or ''
    except Exception:
        reason=''
    if reason:
        print(reason)
        raise SystemExit(0)
" "$IMPROVE_STATE_FILE" "$IMPROVE_LOCK_FILE" 2>/dev/null || echo "")
		_live_improve_pid=""
		if command -v _find_live_improve_pid >/dev/null 2>&1; then
			_live_improve_pid=$(_find_live_improve_pid 2>/dev/null || true)
		fi
		case "$_pause_reason" in
		wildcard|archive_restart)
			log_pause_throttled "${_pause_reason}_improve" "[PAUSE] ${_pause_reason}改善中(隔離評価): soren91代打を立てず待機"
			_run_improve_runtime_monitor
			sleep "${SOREN_IMPROVE_PAUSE_SEC:-3}"
			continue
			;;
		esac
		if [ -z "$_live_improve_pid" ]; then
			log_pause_throttled "improve_state_no_live_pid" "[PAUSE] 改善状態だが実改善PIDなし: soren91は起動せず回収待ち"
			_run_improve_runtime_monitor
			sleep "${SOREN_IMPROVE_PAUSE_SEC:-3}"
			continue
		fi
		if command -v soren91_is_running >/dev/null 2>&1 && ! soren91_is_running 2>/dev/null; then
			soren91_start 2>/dev/null || true
		fi
		command -v _soren91_switch_obs_layout >/dev/null 2>&1 && _soren91_switch_obs_layout meriken 2>/dev/null || true
		log_pause_throttled "improve_running" "[PAUSE] 改善中: ゲームプレイ一時停止 (メリケンAIが代打中)"
		_run_improve_runtime_monitor
		sleep "${SOREN_IMPROVE_PAUSE_SEC:-3}"
		continue
	fi
	# 改善完了直後の連続(カスケード)改善ロックに即 PAUSE せず、メインゲームを
	# 最低1回走らせる窓。soren91 代打がサイクル間で無限に連続起動するのを防ぐ。
	# soren91 が完全停止してから消費 (同時プレイ回避)。1改善完了につき1ゲーム。
	_post_improve_marker="${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}"
	if [ "${POST_IMPROVE_MAINPLAY_ENABLED:-1}" = "1" ] && [ -f "$_post_improve_marker" ] &&
		! _is_improve_running &&
		! { command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; } &&
		! { command -v _soren91_stop_in_progress >/dev/null 2>&1 && _soren91_stop_in_progress; }; then
		rm -f "$_post_improve_marker" 2>/dev/null || true
		log "[CYCLE] 改善完了→次改善前にメインゲームを1回実行 (代打無限化防止)"
	elif [ -f "$IMPROVE_LOCK_FILE" ] && [ ! -f "$TMP_STATE_DIR/rate_limit_backoff" ]; then
		log_pause_throttled "improve_lock_wait" "[PAUSE] 改善ロック待ち: ゲームプレイ一時停止"
		_run_improve_runtime_monitor
		sleep "${SOREN_IMPROVE_PAUSE_SEC:-3}"
		continue
	fi
	if command -v _soren91_stop_in_progress >/dev/null 2>&1 && _soren91_stop_in_progress; then
		log_pause_throttled "soren91_stop_in_progress" "[PAUSE] soren91停止中: 完全停止までメインゲーム再開を待機"
		_run_improve_runtime_monitor
		sleep "${SOREN_IMPROVE_PAUSE_SEC:-3}"
		continue
	fi


	# 改善完了が20時台だった場合: soren91を停止せずメリケンAIタイムに移行
	# improve_daemon からのファイルベース通知を読み取る
	if [ -f "tmp/state/meriken_time_pending" ]; then
		rm -f "tmp/state/meriken_time_pending"
		MERIKEN_TIME_PENDING=1
	fi
	if [ "${MERIKEN_TIME_PENDING:-0}" -eq 1 ]; then
		MERIKEN_TIME_PENDING=0
		if [ "${MERIKEN_SCHEDULED_TIME_ENABLED:-1}" = "1" ] && command -v _soren91_enabled >/dev/null 2>&1 && _soren91_enabled; then
			# soren91は既に動いているのでstartは不要。アナウンスのみ
			enqueue_audio_text "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております" "meriken_time" "${SOREN91_VOICEVOX_SPEAKER:-46}"
			enqueue_chat_message "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております 【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" "soren_loop"
			_run_scheduled_meriken_time_window \
				"improve_complete" \
				"[MERIKEN_TIME] 改善完了→20時台: メリケンAIタイム開始"
		else
			log "[MERIKEN_TIME] 定時メリケンAIタイム無効のため、soren91を通常停止"
			soren91_stop 2>/dev/null || true
			soren91_improve 2>/dev/null || true
		fi
	fi

	if [ "${DEFER_NEXT_GAME_PREP:-0}" -eq 1 ]; then
		log "[CYCLE] 改善明け: 保留していた次ゲーム準備を実行"
		prepare_next_game
		defer_rc=$?
		_abort_if_interrupted "$defer_rc" "prepare_next_game(deferred)"
		DEFER_NEXT_GAME_PREP=0
	fi

	# 1試合プレイ
	play_one_game
	play_rc=$?
	_abort_if_interrupted "$play_rc" "play_one_game"
	if [ "$play_rc" -eq "${PLAY_RECOVERED_RETRY_RC:-75}" ]; then
		log "[RECOVERY] decide例外リカバリ済み: この試合の後処理をスキップして次へ"
		sleep 2
		continue
	fi
	_exit_if_lock_owner_changed "before_post_game"

	# 後処理 (スコア記録, バージョン保存, git commit 等)
	# 粛清判定が走る前に prediction_worker がサイクル完了 resolve を先行発火させないよう、
	# post_game_bookkeeping (acc_count++) → check_regression の全区間をガードする。
	touch "$TMP_STATE_DIR/regression_check_in_progress" 2>/dev/null || true
	post_game_bookkeeping
	post_rc=$?
	_abort_if_interrupted "$post_rc" "post_game_bookkeeping"
	_evolution_flow_notify \
		"game_finished" \
		"game finished" \
		"game=${GAME_NUM:-?} post_game_bookkeeping complete" \
		"改善フロー: game finished。game=${GAME_NUM:-?} の結果を記録し、粛清チェックへ進みます。" \
		"info"
	if [ "${CURRENT_RUN_AUTO_REPAIR_ENABLED:-1}" = "1" ] && [ -x ./repair_current_run_from_history.sh ]; then
		./repair_current_run_from_history.sh "${CURRENT_RUN_AUTO_REPAIR_LIMIT:-12}" >/dev/null 2>&1 ||
			log "[CURRENT-RUN] auto repair skipped/failed after post_game_bookkeeping"
	fi
	if [ -x ./wildcard_progress_report.sh ]; then
		./wildcard_progress_report.sh >/dev/null 2>&1 ||
			log "[WILDCARD] progress report skipped/failed after post_game_bookkeeping"
	fi
	# 定期 tmp/ クリーンアップ (50ゲームごと)
	if (( GAME_NUM % 50 == 0 )); then
		cleanup_tmp_files
	fi

	# ソ連建国達成後は retry を含む次ゲーム操作を行わない
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		rm -f "$TMP_STATE_DIR/regression_check_in_progress" 2>/dev/null || true
		log "[HALT] retry・次ゲーム操作を停止"
		sleep 5
		continue
	fi

	# 粛清チェック: improve_daemon とは独立して毎試合実行
	# (daemon は改善中にブロックされるため、soren_loop 側で必ず走らせる)
	_evolution_flow_notify \
		"regression_check" \
		"regression check" \
		"game=${GAME_NUM:-?} check_regression start" \
		"改善フロー: regression check。game=${GAME_NUM:-?} の粛清判定を実行します。" \
		"info"
	if check_regression; then
		_evolution_flow_notify \
			"rollback_happened" \
			"rollback happened? yes" \
			"rollback_hash=${REGRESSION_ROLLBACK_HASH:-unknown} result=${REGRESSION_ROLLBACK_RESULT:-unknown}" \
			"改善フロー: rollback happened? yes。粛清が発生したため理由分類へ進みます。復帰先=${REGRESSION_ROLLBACK_HASH:-unknown}" \
			"warn"
		# フラグを最初に書く: _clear_accumulated_data がレース時でも best_outcome=3 を保証
		touch "$TMP_STATE_DIR/regression_pending" 2>/dev/null || true
		if [ -f "$TMP_STATE_DIR/current_prediction.json" ]; then
			# best_outcome を粛清(3)に更新: prediction_worker が検知して resolve する
			python3 -c "
import json
f='$TMP_STATE_DIR/current_prediction.json'
d=json.load(open(f)); d['best_outcome']=3; json.dump(d,open(f,'w'))
" 2>/dev/null || true
		fi
		_post_regression_route_info=$(_post_regression_route)
		IFS='|' read -r _post_regression_mode _post_regression_detail _post_regression_rstreak _post_regression_objective_loss _post_regression_target_progress <<EOF
$_post_regression_route_info
EOF
		_evolution_flow_notify \
			"classify_rollback_reason" \
			"classify rollback reason" \
			"mode=${_post_regression_mode:-unknown} detail=${_post_regression_detail:-unknown} rstreak=${_post_regression_rstreak:-0} objective_loss=${_post_regression_objective_loss:-0}" \
			"改善フロー: classify rollback reason。mode=${_post_regression_mode:-unknown} / ${_post_regression_detail:-unknown}" \
			"info"
		if [ "${ROLLBACK_REVALIDATE_TARGET_ENABLED:-1}" = "1" ] &&
			[ "${REGRESSION_ROLLBACK_DONE:-0}" = "1" ] &&
			[ "${_post_regression_mode:-}" = "revalidate" ]; then
			log "[CYCLE] 回帰ロールバック直後 → 復帰先にロシア進捗あり (${_post_regression_detail}) のため再評価を優先"
			_evolution_flow_notify \
				"russia_path_alive" \
				"russia path still alive? yes" \
				"${_post_regression_detail} → rollback target revalidation / light improve" \
				"改善フロー: russia path still alive? yes。復帰先にロシア進捗があるため、脱出せず再検証を優先します。" \
				"info"
		elif [ "${POST_REGRESSION_IMPROVE_ENABLED:-1}" = "1" ] &&
			[ -f "$ACCUMULATED_GAMES_FILE" ] &&
			[ ! -f "$IMPROVE_LOCK_FILE" ] &&
			! _is_improve_running; then
			if [ "${POST_REGRESSION_DIRECT_ESCAPE_ENABLED:-1}" = "1" ] && [ "${_post_regression_mode:-}" = "direct_escape" ]; then
				log "[CYCLE] 回帰ロールバック直後 → ロシア建国ルート喪失の粛清連鎖 (${_post_regression_detail}) のため直接脱出ロック作成"
				_evolution_flow_notify \
					"direct_escape" \
					"direct escape, no next game" \
					"russia_path_dead rstreak=${_post_regression_rstreak:-0}/${WILDCARD_REGRESSION_STREAK:-3} ${_post_regression_detail:-unknown}" \
					"改善フロー: direct escape, no next game。ロシア進捗なし・粛清連鎖のため次ゲームを待たず脱出します。" \
					"warn"
			else
				log "[CYCLE] 回帰ロールバック直後 → 失敗バッチで改善ロック作成"
				if [ "${_post_regression_target_progress:-0}" != "1" ]; then
					_evolution_flow_notify \
						"post_regression_improve" \
						"post_regression improve" \
						"russia_path_dead but rstreak=${_post_regression_rstreak:-0}/${WILDCARD_REGRESSION_STREAK:-3}; direct escape threshold not reached" \
						"改善フロー: post_regression improve。ロシア進捗は弱いが粛清連鎖閾値未満のため、失敗バッチで通常の回帰後改善に入ります。" \
						"info"
				fi
			fi
			enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE" 2>/dev/null || true
			cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE"
			enrich_accumulated_game_metadata "$IMPROVE_LOCK_FILE" 2>/dev/null || true
			_direct_escape_flag=0
			if [ "${POST_REGRESSION_DIRECT_ESCAPE_ENABLED:-1}" = "1" ] && [ "${_post_regression_mode:-}" = "direct_escape" ]; then
				_direct_escape_flag=1
			fi
			POST_REGRESSION_MODE="${_post_regression_mode:-post_regression}" \
			POST_REGRESSION_DETAIL="${_post_regression_detail:-}" \
			POST_REGRESSION_DIRECT="${_direct_escape_flag:-0}" \
			POST_REGRESSION_RSTREAK="${_post_regression_rstreak:-0}" \
			POST_REGRESSION_OBJECTIVE_LOSS="${_post_regression_objective_loss:-0}" \
			REGRESSION_ROLLBACK_HASH_VALUE="${REGRESSION_ROLLBACK_HASH:-}" \
			REGRESSION_ROLLBACK_RESULT_VALUE="${REGRESSION_ROLLBACK_RESULT:-}" \
			python3 - "$IMPROVE_LOCK_FILE" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    data = json.load(f)

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

data["started_at"] = int(time.time())
data["improve_reason"] = "post_regression"
data["rollback_hash"] = os.environ.get("REGRESSION_ROLLBACK_HASH_VALUE", "")
data["regression_result"] = os.environ.get("REGRESSION_ROLLBACK_RESULT_VALUE", "")
data["post_regression_route"] = os.environ.get("POST_REGRESSION_MODE", "post_regression")
data["post_regression_route_detail"] = os.environ.get("POST_REGRESSION_DETAIL", "")
data["post_regression_direct_escape"] = bool(as_int(os.environ.get("POST_REGRESSION_DIRECT", "0")))
data["post_regression_regression_streak"] = as_int(os.environ.get("POST_REGRESSION_RSTREAK", "0"))
data["post_regression_objective_loss"] = bool(as_int(os.environ.get("POST_REGRESSION_OBJECTIVE_LOSS", "0")))
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
			if [ -x ./overlay_notify.sh ]; then
				if [ "${_direct_escape_flag:-0}" = "1" ]; then
					./overlay_notify.sh worker "粛清連鎖脱出 queued (game ${GAME_NUM:-?})" "ロシア建国ルート喪失 | route=direct_escape | ${_post_regression_detail:-unknown} | 復帰先=${REGRESSION_ROLLBACK_HASH:-unknown}" "warn" >/dev/null 2>&1 || true
				else
					./overlay_notify.sh worker "回帰後改善 queued (game ${GAME_NUM:-?})" "粛清後の失敗バッチを改善入力として投入 | reason=post_regression | 復帰先=${REGRESSION_ROLLBACK_HASH:-unknown} | game=${GAME_NUM:-?}" "warn" >/dev/null 2>&1 || true
				fi
			fi
		fi
		_clear_accumulated_data
	else
		_evolution_flow_notify \
			"no_rollback" \
			"rollback happened? no" \
			"normal cycle / 12-game improve / hot streak extension" \
			"改善フロー: rollback happened? no。通常サイクル、12ゲーム改善、またはhot streak延長へ進みます。" \
			"info"
	fi
	rm -f "$TMP_STATE_DIR/regression_check_in_progress" 2>/dev/null || true

	# 早期脱出ロック: 停滞が閾値を超えたら12試合サイクルを待たず、
	# 最低限の失敗バッチを改善daemonへ渡す。実際に wildcard/archive/escape_ai
	# へ上げるかは trigger_adaptive_improvement 側の最終モード判定に任せる。
	if [ "${WILDCARD_EARLY_ESCAPE_LOCK_ENABLED:-1}" = "1" ] &&
		[ "${WILDCARD_ENABLED:-0}" = "1" ] &&
		[ -f "$ACCUMULATED_GAMES_FILE" ] &&
		[ ! -f "$IMPROVE_LOCK_FILE" ] &&
		[ ! -f "$TMP_STATE_DIR/rate_limit_backoff" ] &&
		! _is_improve_running; then
		_cycle_acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
		_stag_count=$(python3 -c "import json; print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('consecutive_no_improve',0)))" 2>/dev/null || echo 0)
		_rstreak_count=$(python3 -c "import json; print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('regression_streak',0)))" 2>/dev/null || echo 0)
		_early_min="${WILDCARD_EARLY_ESCAPE_MIN_GAMES:-4}"
		case "$_early_min" in ''|*[!0-9]*) _early_min=4 ;; esac
		_rollback_revalidate_probe=$(
			python3 - "$CURRENT_STRATEGY_RUN_FILE" "$TMP_STATE_DIR/last_rollback_pair.json" "${MIN_GAMES_BEFORE_IMPROVE:-12}" <<'PY' 2>/dev/null || echo "0:0:"
import json
import os
import sys

current_file, pair_file, mature_raw = sys.argv[1:4]
try:
    mature = max(1, int(mature_raw))
except Exception:
    mature = 12

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

current = load(current_file)
pair = load(pair_file)
current_hash = str(current.get("hash", "") or "")
rollback_hash = str(pair.get("to_hash", "") or "")
try:
    n = int(current.get("games_total", 0) or len(current.get("scores", []) or []))
except Exception:
    n = 0

if current_hash and rollback_hash and current_hash == rollback_hash and n < mature:
    print(f"1:{n}:{current_hash}")
else:
    print(f"0:{n}:{current_hash}")
PY
		)
		_rollback_revalidate_active="${_rollback_revalidate_probe%%:*}"
		_rollback_revalidate_rest="${_rollback_revalidate_probe#*:}"
		_rollback_revalidate_n="${_rollback_revalidate_rest%%:*}"
		_rollback_revalidate_hash="${_rollback_revalidate_rest#*:}"
		if [ "${_cycle_acc_count:-0}" -ge "$_early_min" ] && {
			[ "${_stag_count:-0}" -ge "${WILDCARD_TRIGGER_STAGNATION:-3}" ] ||
				[ "${_rstreak_count:-0}" -ge "${WILDCARD_REGRESSION_STREAK:-3}" ];
		}; then
			if [ "${ROLLBACK_REVALIDATE_TARGET_ENABLED:-1}" = "1" ] && [ "$_rollback_revalidate_active" = "1" ]; then
				log "[EARLY_ESCAPE] stagnation=${_stag_count}/${WILDCARD_TRIGGER_STAGNATION:-3} regression_streak=${_rstreak_count}/${WILDCARD_REGRESSION_STREAK:-3} だが rollback revalidate fresh cycle 中 (${_rollback_revalidate_hash:0:8} ${_rollback_revalidate_n}/${MIN_GAMES_BEFORE_IMPROVE}) → 早期脱出ロックを延期"
			elif [ "${HOT_STREAK_EXTEND_ENABLED:-1}" = "1" ] && _is_rank1_hot_streak; then
				log "[EARLY_ESCAPE] stagnation=${_stag_count}/${WILDCARD_TRIGGER_STAGNATION:-3} regression_streak=${_rstreak_count}/${WILDCARD_REGRESSION_STREAK:-3} だが rank1 hot streak 中 → 早期脱出ロックを延期"
				notify_rank1_hot_streak_extension "$_cycle_acc_count" "wildcard_early_escape"
			else
				log "[EARLY_ESCAPE] stagnation=${_stag_count}/${WILDCARD_TRIGGER_STAGNATION:-3} regression_streak=${_rstreak_count}/${WILDCARD_REGRESSION_STREAK:-3}, acc=${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} → 改善ロック作成 (最終モードはimprove側で判定)"
				enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE" 2>/dev/null || true
				cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE"
				enrich_accumulated_game_metadata "$IMPROVE_LOCK_FILE" 2>/dev/null || true
				python3 -c "
import json, time
f='$IMPROVE_LOCK_FILE'
d=json.load(open(f))
d['started_at']=int(time.time())
d['improve_reason']='normal'
d['early_escape_lock']=True
d['early_escape_stagnation']=${_stag_count:-0}
d['early_escape_regression_streak']=${_rstreak_count:-0}
json.dump(d,open(f,'w'))
" 2>/dev/null || true
				if [ -x ./overlay_notify.sh ]; then
					./overlay_notify.sh worker "早期脱出ロック queued (game ${GAME_NUM:-?})" "停滞 ${_stag_count}/${WILDCARD_TRIGGER_STAGNATION:-3}・回帰 ${_rstreak_count}/${WILDCARD_REGRESSION_STREAK:-3}・蓄積 ${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} で12試合待ちを短縮。最終モードは改善側で判定" "warn" >/dev/null 2>&1 || true
				fi
				_clear_accumulated_data
			fi
		fi
	fi

	# 改善サイクル管理: 12試合蓄積時にロックファイルを作成してdeamonに通知
	# improve_daemon が動いていない場合は蓄積リセットのみ行い次サイクルへ
	if [ -f "$ACCUMULATED_GAMES_FILE" ] && ! _is_improve_running; then
		_cycle_acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
		if [ "${_cycle_acc_count:-0}" -ge "$MIN_GAMES_BEFORE_IMPROVE" ]; then
			if [ "${HOT_STREAK_EXTEND_ENABLED:-1}" = "1" ] && _is_rank1_hot_streak; then
				log "[CYCLE] ${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} 試合到達だが rank1 hot streak 中 → 改善を延期してスコア更新継続"
				_evolution_flow_notify \
					"hot_streak_extension" \
					"hot streak extension" \
					"acc=${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} rank1 hot streak; improve delayed" \
					"改善フロー: hot streak extension。${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE}試合到達ですが、1位更新中なので改善を延期します。" \
					"info"
				notify_rank1_hot_streak_extension "$_cycle_acc_count" "cycle_threshold"
				prepare_next_game
				next_rc=$?
				_abort_if_interrupted "$next_rc" "prepare_next_game(hot_streak_extend)"
				sleep 2
				continue
			fi
			# デーモンの存在確認
			_improve_daemon_alive=false
			if [ -f "$IMPROVE_DAEMON_PID_FILE" ]; then
				_daemon_pid=$(cat "$IMPROVE_DAEMON_PID_FILE" 2>/dev/null)
				if [ -n "$_daemon_pid" ] && kill -0 "$_daemon_pid" 2>/dev/null; then
					_improve_daemon_alive=true
				fi
			fi
			# 予想サイクル終了: prediction_worker が acc_count >= threshold を検知して resolve する
			# daemon 生死によらずロックファイルを作成する
			# daemon が落ちていても再起動後に trigger_adaptive_improvement が拾えるようにする
			if [ "$_improve_daemon_alive" = true ]; then
				log "[CYCLE] ${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} 試合到達 → ロックファイル作成 (デーモンが改善開始予定)"
			else
				log "[CYCLE] ${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} 試合到達 (デーモンなし) → ロックファイル作成、daemon再起動後に改善予定"
			fi
			_evolution_flow_notify \
				"twelve_game_improve" \
				"12-game improve" \
				"acc=${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE} daemon_alive=${_improve_daemon_alive}" \
				"改善フロー: 12-game improve。${_cycle_acc_count}/${MIN_GAMES_BEFORE_IMPROVE}試合が貯まったため改善ロックを作成します。" \
				"info"
			enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE" 2>/dev/null || true
			cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE"
			enrich_accumulated_game_metadata "$IMPROVE_LOCK_FILE" 2>/dev/null || true
			python3 -c "
import json, time
f='$IMPROVE_LOCK_FILE'
d=json.load(open(f))
d['started_at']=int(time.time())
json.dump(d,open(f,'w'))
" 2>/dev/null || true
			_clear_accumulated_data
		fi
	fi

	# 20時台メリケンAIタイム: 改善サイクル区切り（蓄積0かつファイルあり=改善直後）で定時枠終了までメリケンモード
	# ファイルなし(初回起動)では発火しない。MERIKEN_TIME_PENDINGパスとは別の入口。
	_meriken_acc_count=-1
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		_meriken_acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo -1)
	fi
	if [ "${_meriken_acc_count}" -eq 0 ] && [ "$(date +%H)" = "20" ]; then
		if [ "${MERIKEN_SCHEDULED_TIME_ENABLED:-1}" = "1" ] && command -v _soren91_enabled >/dev/null 2>&1 && _soren91_enabled; then
			soren91_start 2>/dev/null || true
			# メリケンAIタイム専用アナウンス (読み上げ + Twitch投稿)
			enqueue_audio_text "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております" "meriken_time" "${SOREN91_VOICEVOX_SPEAKER:-46}"
			enqueue_chat_message "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております 【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" "soren_loop"
			_run_scheduled_meriken_time_window \
				"cycle_boundary" \
				"[MERIKEN_TIME] サイクル区切り+20時台: メリケンAIタイム開始"
		fi
	fi

	# 改善実行中 or ロック待ち(バックオフ中でない): 次ゲーム準備を保留
	if _is_improve_running; then
		log_pause_throttled "cycle_improve_running" "[CYCLE] 改善実行中 → 次ゲーム準備を保留"
		DEFER_NEXT_GAME_PREP=1
		sleep 2
		continue
	fi
	if [ -f "$IMPROVE_LOCK_FILE" ] && [ ! -f "$TMP_STATE_DIR/rate_limit_backoff" ]; then
		log_pause_throttled "cycle_improve_lock_wait" "[CYCLE] 改善ロック待ち → 次ゲーム準備を保留"
		DEFER_NEXT_GAME_PREP=1
		sleep 2
		continue
	fi

	# 次の試合準備
	prepare_next_game
	next_rc=$?
	_abort_if_interrupted "$next_rc" "prepare_next_game"

	sleep 2
done
