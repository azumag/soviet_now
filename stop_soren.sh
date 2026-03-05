#!/bin/bash
# stop_soren.sh - soren_loop.sh を安全に停止する
#
# tmp/stop ファイルを作成し、lockfile から PID を取得して SIGINT を送信。
# strategy_runner.py も stop-file を検知して自主的に終了する。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOCKFILE="tmp/soren_loop.lock"
STOP_FILE="tmp/stop"

# stop-file 作成 (strategy_runner.py が次のループで検知)
mkdir -p tmp
touch "$STOP_FILE"
echo "Stop file created: $STOP_FILE"

# lockfile から PID を取得して SIGINT 送信
if [ -f "$LOCKFILE" ]; then
	pid=$(cat "$LOCKFILE" 2>/dev/null)
	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		# PID がシェルスクリプトであることを確認
		cmd=$(ps -p "$pid" -o comm= 2>/dev/null)
		case "$cmd" in
		*bash*|*zsh*|*sh|*python*|*soren*)
			echo "Sending SIGINT to soren_loop (PID=$pid)"
			kill -INT "$pid" 2>/dev/null
			;;
		*)
			echo "WARNING: PID=$pid is '$cmd', not a shell. Skipping kill."
			;;
		esac
	else
		echo "PID=$pid is not running (stale lockfile)"
		rm -f "$LOCKFILE"
	fi
else
	echo "No lockfile found. soren_loop may not be running."
	echo "Stop file was still created for safety."
fi
