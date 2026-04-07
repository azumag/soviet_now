#!/bin/bash
# improve_daemon.sh - 改善ループ独立デーモン
#
# soren_loop.sh とは別ターミナルで起動する。
# IPC はファイルベース（tmp/state/ 以下）で行う。
#
# 起動例:
#   ./improve_daemon.sh
#
# 停止: tmp/stop を作成するか Ctrl-C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

# デーモンモード: _start_improvement_job() が eloop_improve.sh を
# フォアグラウンド実行（wait）するよう指示するフラグ
export IMPROVE_DAEMON_MODE=1

source ./eloop_lib.sh

POLL_INTERVAL=${IMPROVE_DAEMON_POLL_INTERVAL:-30}
echo "[$(date '+%H:%M:%S')] [improve_daemon] 起動 (poll=${POLL_INTERVAL}s)"
echo $$ > "$IMPROVE_DAEMON_PID_FILE"

_daemon_cleanup() {
	rm -f "$IMPROVE_DAEMON_PID_FILE"
	echo "[$(date '+%H:%M:%S')] [improve_daemon] 終了"
	exit 0
}
trap '_daemon_cleanup' INT TERM

while true; do
	if [ -f tmp/stop ]; then
		echo "[$(date '+%H:%M:%S')] [improve_daemon] stop検出 → 終了"
		break
	fi

	# モジュール再読み込み（改善中に書き換わる可能性がある）
	source ./eloop_lib.sh 2>/dev/null || true

	# GAME_NUM / LAST_TURNS をファイルから読む
	GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	LAST_TURNS=$(cat "tmp/state/last_turns.txt" 2>/dev/null || echo 0)

	# harvest → ロックファイルがあれば trigger
	check_and_harvest_improvement
	if [ -f "$IMPROVE_LOCK_FILE" ]; then
		trigger_adaptive_improvement
	fi

	sleep "$POLL_INTERVAL"
done
