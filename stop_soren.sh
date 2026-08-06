#!/bin/bash
# stop_soren.sh - soren_loop.sh および全 worker を安全に停止する
#
# tmp/stop ファイルを作成し、supervisor / soren_loop の PID に SIGINT を送信。
# 全 worker は tmp/stop を検知して自主的に終了する。
# strategy_runner.py も stop-file を検知して自主的に終了する。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOCKFILE="tmp/.soren_loop.lock/pid"
SUPERVISOR_PID_FILE="tmp/state/start_all.pid"
STOP_FILE="tmp/stop"

# stop-file 作成 (全 worker + strategy_runner.py が検知)
mkdir -p tmp
touch "$STOP_FILE"
echo "Stop file created: $STOP_FILE"

_signal_pid() {
	local label="$1" pid_file="$2" sig="${3:-INT}"
	if [ -f "$pid_file" ]; then
		local pid
		pid=$(cat "$pid_file" 2>/dev/null)
		if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
			local cmd
			cmd=$(ps -p "$pid" -o comm= 2>/dev/null)
			case "$cmd" in
			*bash*|*zsh*|*sh|*python*|*soren*)
				echo "Sending SIG${sig} to ${label} (PID=$pid)"
				kill "-${sig}" "$pid" 2>/dev/null
				;;
			*)
				echo "WARNING: ${label} PID=$pid is '$cmd', not a shell. Skipping."
				;;
			esac
		else
			echo "${label}: PID=$pid is not running (stale)"
			rm -f "$pid_file"
		fi
	fi
}

# supervisor が動いている場合はそれに SIGTERM を送る (全 worker を停止してくれる)
if [ -f "$SUPERVISOR_PID_FILE" ]; then
	_signal_pid "supervisor" "$SUPERVISOR_PID_FILE" "TERM"
fi

# 探索モード (explore.sh) で動いている場合は explore.sh に SIGINT を送る。
# explore.sh の trap がブリッジ/daemon/watchdog をクリーンアップし、
# その後に soren_loop.sh の多重起動ロックも解放される。
if [ -f "tmp/state/explore.pid" ]; then
	_explore_pid=$(cat "tmp/state/explore.pid" 2>/dev/null || true)
	case "$_explore_pid" in
	'' | *[!0-9]*)
		_explore_pid=0
		;;
	esac
	if [ "$_explore_pid" -gt 0 ] && kill -0 "$_explore_pid" 2>/dev/null; then
		echo "Sending SIGINT to explore.sh (PID=$_explore_pid)"
		kill -INT "$_explore_pid" 2>/dev/null
	else
		# stale な explore.pid / explore_mode マーカ掃除 (SIGKILL 等で explore.sh の
		# EXIT trap が走らなかった場合に配信モードが無言で headless 化するのを防ぐ)
		rm -f "tmp/state/explore.pid" "tmp/state/explore_mode"
	fi
fi

# soren_loop が単体で動いている場合 (supervisor なし)
if [ -f "$LOCKFILE" ]; then
	_signal_pid "soren_loop" "$LOCKFILE" "INT"
fi

# 個別 worker PID (supervisor なしで手動起動した場合の安全策)
for wf in tmp/state/chat_worker.pid tmp/state/audio_worker.pid \
          tmp/state/radio_worker.pid tmp/state/prediction_worker.pid; do
	if [ -f "$wf" ]; then
		name=$(basename "$wf" .pid)
		_signal_pid "$name" "$wf" "TERM"
	fi
done

if [ ! -f "$SUPERVISOR_PID_FILE" ] && [ ! -f "$LOCKFILE" ]; then
	echo "No supervisor or soren_loop found. Stop file created for safety."
fi
