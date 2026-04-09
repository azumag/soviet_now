#!/bin/bash
# workers/radio_worker.sh - ラジオ生成スケジュールの foreground worker
#
# 責務:
#   1. ゲーム進行 (game_count.txt) を監視し、新しい試合開始を検知
#   2. schedule_nonessential_audio_jobs() を呼んでラジオ生成をトリガー
#   3. 時刻ベースのコーナーも同関数が内部で処理する
#
# 生成されたラジオ本文は deferred radio queue に積まれ、
# audio_worker が再生する。
#
# 起動: ./workers/radio_worker.sh
# 停止: touch tmp/stop  or  kill <PID>  or  Ctrl+C

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ ---
source ./eloop_lib.sh

WORKER_NAME="radio_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
POLL_INTERVAL="${RADIO_WORKER_INTERVAL:-10}"

_STOPPED=0
_LAST_GAME_NUM=""

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
echo $$ > "$PID_FILE"

# 初期 game_num
_LAST_GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

# === メインループ ===
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s, initial_game=${_LAST_GAME_NUM})"

while true; do
	# 停止チェック
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi

	# eloop_lib.sh を再読み込み
	if ! source ./eloop_lib.sh 2>/dev/null; then
		_log "WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)"
	fi

	# ゲーム番号の変化を検知
	current_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

	if [ "$current_game_num" != "$_LAST_GAME_NUM" ]; then
		_log "新試合検知: ${_LAST_GAME_NUM} → ${current_game_num}"
		_LAST_GAME_NUM="$current_game_num"

		# ラジオスケジュール実行 (サイクルベース + 時刻ベース)
		# サイクルベースコーナー (news/theme/jiji) は marker なしのため、
		# game_num 変化時のみ呼ぶ（重複発火防止）
		score=$(_last_score 2>/dev/null || echo 0)
		schedule_nonessential_audio_jobs "$current_game_num" "$score" 2>/dev/null || true
	fi

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
