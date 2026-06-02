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
DAEMON_LOCK_DIR="$TMP_STATE_DIR/improve_daemon.lockdir"
DAEMON_LOCK_PID_FILE="$DAEMON_LOCK_DIR/pid"
_HEARTBEAT_PID=""

_pid_alive_local() {
	local pid="${1:-}"
	[ -n "$pid" ] || return 1
	kill -0 "$pid" 2>/dev/null
}

_acquire_daemon_singleton() {
	local old_pid
	old_pid=$(cat "$IMPROVE_DAEMON_PID_FILE" 2>/dev/null || true)
	if _pid_alive_local "$old_pid" && [ "$old_pid" != "$$" ]; then
		mkdir -p "$DAEMON_LOCK_DIR" 2>/dev/null || true
		printf '%s\n' "$old_pid" >"$DAEMON_LOCK_PID_FILE" 2>/dev/null || true
		echo "[$(date '+%H:%M:%S')] [improve_daemon] 既存 daemon PID=${old_pid} が稼働中 → 重複起動を終了"
		exit 0
	fi
	while ! mkdir "$DAEMON_LOCK_DIR" 2>/dev/null; do
		old_pid=$(cat "$DAEMON_LOCK_PID_FILE" 2>/dev/null || true)
		if _pid_alive_local "$old_pid"; then
			echo "[$(date '+%H:%M:%S')] [improve_daemon] 既存 daemon PID=${old_pid} が稼働中 → 重複起動を終了"
			exit 0
		fi
		rm -rf "$DAEMON_LOCK_DIR" 2>/dev/null || true
	done
	printf '%s\n' "$$" >"$DAEMON_LOCK_PID_FILE" 2>/dev/null || true
}

_release_daemon_singleton() {
	local old_pid
	old_pid=$(cat "$DAEMON_LOCK_PID_FILE" 2>/dev/null || true)
	if [ "$old_pid" = "$$" ]; then
		rm -rf "$DAEMON_LOCK_DIR" 2>/dev/null || true
	fi
}

_require_visible_terminal() {
	# 配信用フォアグラウンド端末前提は廃止。進捗は HTML オーバーレイに
	# 書き出す運用に移行したため、ヘッドレス (supervisor/nohup 配下・TTY無し)
	# が通常運用。TTY 必須ガードは撤廃し、TTY があれば記録のみ行う。
	# IMPROVE_DAEMON_REQUIRE_TTY=1 を明示した時だけ旧来の TTY 必須に戻す。
	local current_tty
	current_tty=$(tty 2>/dev/null || true)
	if [ "${IMPROVE_DAEMON_REQUIRE_TTY:-0}" = "1" ]; then
		if [ ! -t 0 ] || [ ! -t 1 ] || [ -z "$current_tty" ] || [ "$current_tty" = "not a tty" ]; then
			echo "[$(date '+%H:%M:%S')] [improve_daemon] ERROR: IMPROVE_DAEMON_REQUIRE_TTY=1 だが TTY 無し"
			exit 2
		fi
	fi
	if [ -n "$current_tty" ] && [ "$current_tty" != "not a tty" ]; then
		printf '%s\n' "$current_tty" > "$DAEMON_TTY_FILE" 2>/dev/null || true
	else
		echo "[$(date '+%H:%M:%S')] [improve_daemon] ヘッドレス起動 (TTY無し・HTML overlay運用)"
		rm -f "$DAEMON_TTY_FILE" 2>/dev/null || true
	fi
}

_reject_detached_headless_duplicate() {
	# Headless operation is managed by start_all.sh. A detached standalone
	# launcher can bypass backoff and repeatedly flash OBS wildcard layouts.
	if [ -t 0 ] || [ -t 1 ]; then
		return 0
	fi
	if [ "${IMPROVE_DAEMON_ALLOW_STANDALONE_HEADLESS:-0}" = "1" ]; then
		return 0
	fi
	if [ "${PPID:-0}" = "1" ]; then
		echo "[$(date '+%H:%M:%S')] [improve_daemon] detached headless standalone 起動を拒否 (supervisor 管理に統一)"
		exit 0
	fi
}

_reject_detached_headless_duplicate
_acquire_daemon_singleton
_require_visible_terminal
echo "[$(date '+%H:%M:%S')] [improve_daemon] 起動 (poll=${POLL_INTERVAL}s)"
echo $$ > "$IMPROVE_DAEMON_PID_FILE"

_start_pid_heartbeat() {
	(
		while true; do
			echo $$ > "$IMPROVE_DAEMON_PID_FILE" 2>/dev/null || true
			sleep "${WORKER_PID_HEARTBEAT_INTERVAL:-5}"
		done
	) &
	_HEARTBEAT_PID=$!
}

_daemon_cleanup() {
	if [ -n "$_HEARTBEAT_PID" ]; then
		kill "$_HEARTBEAT_PID" 2>/dev/null || true
	fi
	_release_daemon_singleton
	local recorded_pid
	recorded_pid=$(cat "$IMPROVE_DAEMON_PID_FILE" 2>/dev/null || true)
	if [ "$recorded_pid" = "$$" ]; then
		rm -f "$IMPROVE_DAEMON_PID_FILE" "$DAEMON_TTY_FILE"
	fi
	echo "[$(date '+%H:%M:%S')] [improve_daemon] 終了"
	exit 0
}
trap '_daemon_cleanup' INT TERM
_start_pid_heartbeat

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
		if [ -f "$TMP_STATE_DIR/rate_limit_backoff" ]; then
			now=$(date +%s)
			last_log=$(cat "$TMP_STATE_DIR/rate_limit_backoff_last_log" 2>/dev/null || echo 0)
			case "$last_log" in '' | *[!0-9]*) last_log=0 ;; esac
			if [ $((now - last_log)) -ge "${IMPROVE_DAEMON_BACKOFF_LOG_INTERVAL:-60}" ]; then
				echo "[$(date '+%H:%M:%S')] [IMPROVE] rate-limit backoff中のため daemon trigger をスキップ"
				printf '%s\n' "$now" >"$TMP_STATE_DIR/rate_limit_backoff_last_log" 2>/dev/null || true
			fi
		else
			trigger_adaptive_improvement
		fi
	fi

	sleep "$POLL_INTERVAL"
done

_daemon_cleanup
