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
PAUSE_FILE="tmp/state/${WORKER_NAME}.paused"
POLL_INTERVAL="${PREDICTION_WORKER_INTERVAL:-5}"

_STOPPED=0
_RELOAD_REQUESTED=0
_LAST_GAME_NUM=""
_LAST_ACC_COUNT=""

_log() {
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"
}

_pid_alive() {
	local pid="${1:-}" err=""
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	case "$err" in
	*"operation not permitted"*|*"Operation not permitted"*) return 0 ;;
	esac
	return 1
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ "$active_pid" != "$$" ]; then
		_log "cleanup skipped: pidfile owner is ${active_pid:-none} (self=$$)"
		return 0
	fi
	_log "停止処理開始"
	rm -f "$PID_FILE"
	_log "停止完了"
}

_handle_signal() {
	_cleanup
	trap - EXIT
	exit 130
}
_request_reload() {
	_RELOAD_REQUESTED=1
	_log "reload requested (signal=$1)"
}
_reload_runtime() {
	[ "$_RELOAD_REQUESTED" -eq 1 ] || return 0
	_RELOAD_REQUESTED=0
	if [ -f .env ]; then
		set -a
		. ./.env
		set +a
	fi
	if source ./eloop_lib.sh 2>/dev/null; then
		POLL_INTERVAL="${PREDICTION_WORKER_INTERVAL:-5}"
		_log "reload complete (interval=${POLL_INTERVAL}s)"
	else
		_log "WARNING: reload failed; keeping previous runtime"
	fi
}
trap '_cleanup' EXIT
trap '_handle_signal' INT TERM
trap '_request_reload HUP' HUP
trap '_request_reload USR1' USR1

# --- 多重起動防止 ---
if [ -f "$PAUSE_FILE" ]; then
	_log "paused by $PAUSE_FILE → exit"
	exit 0
fi

if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if _pid_alive "$old_pid"; then
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

_prediction_retry_active_for() {
	local operation="$1" file="$TMP_STATE_DIR/prediction_retry/${1}.json" now next
	[ -f "$file" ] || return 1
	now=$(date +%s)
	next=$(python3 - "$file" <<'PY' 2>/dev/null
import json, sys
try:
    print(int((json.load(open(sys.argv[1], encoding="utf-8")) or {}).get("next_retry_at", 0) or 0))
except Exception:
    print(0)
PY
)
	case "$next" in ''|*[!0-9]*) next=0 ;; esac
	[ "$next" -gt "$now" ]
}

HOT_STREAK_PREDICTION_PENDING_FILE="$TMP_STATE_DIR/hot_streak_prediction_pending"

# --- 初期状態 ---
_LAST_GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
_LAST_ACC_COUNT=$(_get_acc_count)

# === メインループ ===
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s, game=${_LAST_GAME_NUM})"

while true; do
	_reload_runtime
	# 停止チェック
	if [ -f "$PAUSE_FILE" ]; then
		_log "pause file detected → exit"
		break
	fi
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	# eloop_lib.sh を再読み込み
	source ./eloop_lib.sh 2>/dev/null || true

	current_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	current_acc_count=$(_get_acc_count)
	improve_status=$(_get_improve_status)
	_resolved_this_tick=0

	if [ -f "$HOT_STREAK_PREDICTION_PENDING_FILE" ] && [ "$improve_status" = "running" ]; then
		_log "hot streak延長ペンディング解除: 改善開始を検知"
		rm -f "$HOT_STREAK_PREDICTION_PENDING_FILE"
	fi

	# --- 粛清 resolve: regression_pending フラグ (最優先) ---
	if _has_prediction && [ -f "$TMP_STATE_DIR/regression_pending" ] && ! _prediction_retry_active_for resolve; then
		_log "粛清検知 → resolve outcome=3"
		./twitch_predictions.sh resolve 3 >>tmp/prediction.log 2>&1 || true
		_resolved_this_tick=1
	# regression_pending は regression.sh 側で削除される
	fi

	# --- ソ連建国 resolve: best_outcome=2 かつ予想が active ---
	if [ "$_resolved_this_tick" -eq 0 ] && _has_prediction && ! _prediction_retry_active_for resolve; then
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
		! _prediction_retry_active_for resolve &&
		[ "${current_acc_count:-0}" -ge "${MIN_GAMES_BEFORE_IMPROVE:-12}" ] &&
		[ ! -f "$TMP_STATE_DIR/regression_check_in_progress" ]; then
		if [ "${HOT_STREAK_EXTEND_ENABLED:-1}" = "1" ] && _is_rank1_hot_streak; then
			best=$(_get_best_outcome)
			if [ ! -f "$HOT_STREAK_PREDICTION_PENDING_FILE" ]; then
				_log "rank1 hot streak延長突入 (acc=${current_acc_count}) → prediction resolve outcome=${best}, 次改善まで新規予想停止"
				enqueue_chat_message "現在の戦略が1位でスコア更新中のため、改善サイクルを延長して続行します。予想はいったん確定し、次の改善開始まで新しい予想は待機します。" "predictions"
				[ -x ./overlay_notify.sh ] && ./overlay_notify.sh prediction "予想 延長戦 (1位スコア更新中)" "rank1 hot streak | 蓄積ゲーム=${current_acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} | best outcome=${best:-0} | 改善サイクル延長・次改善開始まで新規予想停止" "info" >/dev/null 2>&1 || true
				printf '%s\n' "$(date +%s)" >"$HOT_STREAK_PREDICTION_PENDING_FILE"
			else
				_log "rank1 hot streak延長中 (acc=${current_acc_count}) → prediction pending"
			fi
			./twitch_predictions.sh resolve "${best:-0}" >>tmp/prediction.log 2>&1 || true
		else
			best=$(_get_best_outcome)
			_log "サイクル完了 (acc=${current_acc_count}) → resolve outcome=${best}"
			./twitch_predictions.sh resolve "${best:-0}" >>tmp/prediction.log 2>&1 || true
		fi
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
		_has_prediction &&
		! _prediction_retry_active_for resolve; then
		best=$(_get_best_outcome)
		_log "サイクル先頭: 前サイクルの予想を resolve (outcome=${best})"
		./twitch_predictions.sh resolve "${best:-0}" >>tmp/prediction.log 2>&1 || true
		_resolved_this_tick=1
	fi

	# --- 予想作成: サイクル開始 (acc_count=0, 改善完了後, 予想なし) ---
	if [ "${current_acc_count:-0}" -eq 0 ] && ! _has_prediction && ! _prediction_retry_active_for create; then
		if [ "$improve_status" != "running" ] && [ ! -f "$IMPROVE_LOCK_FILE" ] && [ ! -f "$HOT_STREAK_PREDICTION_PENDING_FILE" ]; then
			_log "予想作成: game=${current_game_num}, acc=0, improve=${improve_status}"
			./twitch_predictions.sh create "$current_game_num" >>tmp/prediction.log 2>&1 || true
		elif [ -f "$HOT_STREAK_PREDICTION_PENDING_FILE" ]; then
			_log "予想作成スキップ: rank1 hot streak延長ペンディング中"
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
