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
#
# 配信に映している専用ターミナルでフォアグラウンド起動する前提。
# nohup / Codex exec / cron などの見えない場所からの起動は拒否する。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

# デーモンモード: _start_improvement_job() が eloop_improve.sh を
# フォアグラウンド実行（wait）するよう指示するフラグ
export IMPROVE_DAEMON_MODE=1

source ./eloop_lib.sh

POLL_INTERVAL=${IMPROVE_DAEMON_POLL_INTERVAL:-30}
DAEMON_TTY_FILE="$TMP_STATE_DIR/improve_daemon.tty"

_require_visible_terminal() {
	[ "${IMPROVE_DAEMON_ALLOW_NONINTERACTIVE:-0}" = "1" ] && return 0
	if [ ! -t 0 ] || [ ! -t 1 ]; then
		echo "[$(date '+%H:%M:%S')] [improve_daemon] ERROR: visible terminal required; run ./improve_daemon.sh in the streaming terminal"
		exit 2
	fi

	local current_tty
	current_tty=$(tty 2>/dev/null || true)
	if [ -z "$current_tty" ] || [ "$current_tty" = "not a tty" ]; then
		echo "[$(date '+%H:%M:%S')] [improve_daemon] ERROR: visible terminal required; tty unavailable"
		exit 2
	fi
	printf '%s\n' "$current_tty" > "$DAEMON_TTY_FILE"
}

_require_visible_terminal
echo "[$(date '+%H:%M:%S')] [improve_daemon] 起動 (poll=${POLL_INTERVAL}s)"
echo $$ > "$IMPROVE_DAEMON_PID_FILE"

_daemon_cleanup() {
	local recorded_pid
	recorded_pid=$(cat "$IMPROVE_DAEMON_PID_FILE" 2>/dev/null || true)
	if [ "$recorded_pid" = "$$" ]; then
		rm -f "$IMPROVE_DAEMON_PID_FILE" "$DAEMON_TTY_FILE"
	fi
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
	echo $$ > "$IMPROVE_DAEMON_PID_FILE"

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

_daemon_cleanup
