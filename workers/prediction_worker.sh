#!/bin/bash
# workers/prediction_worker.sh - チャネルポイント予想の foreground worker
#
# 責務:
#   1. ゲーム進行と state file を監視
#   2. 予想の create / cleanup / resolve を自律判定して実行
#   3. 告知は outbound queue 経由
#
# 監視対象:
#   - game_count.txt — ゲーム番号変化
#   - tmp/state/accumulated_games.json — サイクル蓄積 (count, best_outcome)
#   - tmp/state/improve_state.json — 改善ステータス
#   - tmp/state/current_prediction.json — 予想状態
#   - tmp/state/regression_pending — 粛清フラグ
#
# 起動: ./workers/prediction_worker.sh
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ ---
source ./eloop_lib.sh

WORKER_NAME="prediction_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
POLL_INTERVAL="${PREDICTION_WORKER_INTERVAL:-5}"

_STOPPED=0
_LAST_GAME_NUM=""
_LAST_ACC_COUNT=""

_log() {
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	_log "停止処理開始"
	rm -f "$PID_FILE"
	_log "停止完了"
}

_handle_signal() {
	_cleanup
	trap - EXIT
	exit 130
}
trap '_cleanup' EXIT
trap '_handle_signal' INT TERM

# --- 多重起動防止 ---
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		_log "ERROR: 既に起動中 (PID=$old_pid)"
		exit 1
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" 2>/dev/null || true
echo $$ >"$PID_FILE"

# --- ヘルパー ---
_read_json_field() {
	local file="$1" field="$2" default="${3:-}"
	python3 -c "import json; print(json.load(open('$file')).get('$field', '$default'))" 2>/dev/null || echo "$default"
}

_has_prediction() {
	[ -f "$TMP_STATE_DIR/current_prediction.json" ]
}

_get_acc_count() {
	[ -f "$ACCUMULATED_GAMES_FILE" ] || {
		echo 0
		return
	}
	_read_json_field "$ACCUMULATED_GAMES_FILE" "count" "0"
}

_get_improve_status() {
	[ -f "$IMPROVE_STATE_FILE" ] || {
		echo ""
		return
	}
	_read_json_field "$IMPROVE_STATE_FILE" "status" ""
}

_get_best_outcome() {
	_has_prediction || {
		echo 0
		return
	}
	_read_json_field "$TMP_STATE_DIR/current_prediction.json" "best_outcome" "0"
}

# --- 初期状態 ---
_LAST_GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
_LAST_ACC_COUNT=$(_get_acc_count)

# === メインループ ===
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s, game=${_LAST_GAME_NUM})"

while true; do
	# 停止チェック
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	# eloop_lib.sh を再読み込み
	source ./eloop_lib.sh 2>/dev/null || true

	current_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	current_acc_count=$(_get_acc_count)
	_resolved_this_tick=0

	# --- 粛清 resolve: regression_pending フラグ (最優先) ---
	if _has_prediction && [ -f "$TMP_STATE_DIR/regression_pending" ]; then
		_log "粛清検知 → resolve outcome=3"
		./twitch_predictions.sh resolve 3 >>tmp/prediction.log 2>&1 || true
		_resolved_this_tick=1
	# regression_pending は regression.sh 側で削除される
	fi

	# --- ソ連建国 resolve: best_outcome=2 かつ予想が active ---
	if [ "$_resolved_this_tick" -eq 0 ] && _has_prediction; then
		best=$(_get_best_outcome)
		if [ "${best:-0}" -eq 2 ]; then
			_log "ソ連建国検知 → resolve outcome=2"
			./twitch_predictions.sh resolve 2 >>tmp/prediction.log 2>&1 || true
			_resolved_this_tick=1
		fi
	fi

	# --- サイクル完了 resolve: acc_count >= threshold ---
	# soren_loop の post_game_bookkeeping → check_regression 区間中は保留する。
	# この区間で resolve すると、粛清判定前に best_outcome=0 (建国なし) で確定してしまう。
	if [ "$_resolved_this_tick" -eq 0 ] &&
		_has_prediction &&
		[ "${current_acc_count:-0}" -ge "${MIN_GAMES_BEFORE_IMPROVE:-12}" ] &&
		[ ! -f "$TMP_STATE_DIR/regression_check_in_progress" ]; then
		best=$(_get_best_outcome)
		_log "サイクル完了 (acc=${current_acc_count}) → resolve outcome=${best}"
		./twitch_predictions.sh resolve "${best:-0}" >>tmp/prediction.log 2>&1 || true
		_resolved_this_tick=1
	fi

	# --- ゲーム番号変化時: cleanup (resolve 後に実行して stale 先行を防止) ---
	if [ "$current_game_num" != "$_LAST_GAME_NUM" ]; then
		_LAST_GAME_NUM="$current_game_num"
		./twitch_predictions.sh cleanup >>tmp/prediction.log 2>&1 || true
	fi

	# --- サイクル先頭: 前サイクルの予想を resolve ---
	# acc_count が非0→0 に変わった瞬間のみ resolve（毎ループ発火を防止）
	# ただし同 tick 内で既に resolve 済みならスキップ（二重投稿防止）
	if [ "$_resolved_this_tick" -eq 0 ] &&
		[ "${current_acc_count:-0}" -eq 0 ] &&
		[ "${_LAST_ACC_COUNT:-0}" -ne 0 ] &&
		_has_prediction; then
		best=$(_get_best_outcome)
		_log "サイクル先頭: 前サイクルの予想を resolve (outcome=${best})"
		./twitch_predictions.sh resolve "${best:-0}" >>tmp/prediction.log 2>&1 || true
		_resolved_this_tick=1
	fi

	# --- 予想作成: サイクル開始 (acc_count=0, 改善完了後, 予想なし) ---
	if [ "${current_acc_count:-0}" -eq 0 ] && ! _has_prediction; then
		improve_status=$(_get_improve_status)
		if [ "$improve_status" != "running" ] && [ ! -f "$IMPROVE_LOCK_FILE" ]; then
			_log "予想作成: game=${current_game_num}, acc=0, improve=${improve_status}"
			./twitch_predictions.sh create "$current_game_num" >>tmp/prediction.log 2>&1 || true
		fi
	fi

	_LAST_ACC_COUNT="$current_acc_count"

	# sleep を1秒単位で分割
	_sleep_remaining="$POLL_INTERVAL"
	while [ "${_sleep_remaining:-0}" -gt 0 ]; do
		[ -f tmp/stop ] && break 2
		sleep 1
		_sleep_remaining=$((_sleep_remaining - 1))
	done
done

_log "メインループ終了"
exit 0
