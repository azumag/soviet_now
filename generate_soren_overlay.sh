#!/bin/bash
# generate_soren_overlay.sh - unified SOREN overlay (OPS + STATS in one HTML)
#
# 統合 1 画面: show_status (OPS) と status_dashboard (STATS) を 1 枚に統合し、
# 重複項目を片側へ寄せて表示する。旧 2 枚 (opsOverlay / statsOverlay) の置換。
#
# Usage:
#   ./generate_soren_overlay.sh once
#   ./generate_soren_overlay.sh watch [interval_sec]
#   ./generate_soren_overlay.sh start [interval_sec]
#   ./generate_soren_overlay.sh stop
#   ./generate_soren_overlay.sh ensure-obs [show|hide]

set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=/dev/null
source ./eloop_lib.sh
source ./lib/background_priority.sh
soren_background_priority

mode="${1:-once}"
interval="${2:-2}"
case "$interval" in
''|*[!0-9]*) interval=2 ;;
esac
(( interval < 1 )) && interval=1

out_file="${SOREN_OVERLAY_HTML_FILE:-tmp/state/soren_overlay.html}"
width="${SOREN_OVERLAY_WIDTH:-1120}"
height="${SOREN_OVERLAY_HEIGHT:-980}"
pid_file="tmp/state/soren_overlay_watch.pid"
log_file="tmp/debug/soren_overlay.log"
tmux_session="soren_soren_overlay"

# Parent directories are stable for the lifetime of the watcher. Creating them
# on every 2-second render used to spawn dirname+mkdir repeatedly. Keep this
# lazy so "stop" remains side-effect free.
_OVERLAY_DIRS_READY=0
_ensure_overlay_dirs() {
	[ "$_OVERLAY_DIRS_READY" -eq 1 ] && return 0
	local _overlay_path _overlay_dir
	local -a _overlay_dirs=()
	for _overlay_path in \
		"$out_file" \
		"${SHOW_STATUS_OVERLAY_HTML_FILE:-tmp/state/show_status_overlay.html}" \
		"${STATUS_OVERLAY_HTML_FILE:-tmp/state/status_overlay.html}"; do
		_overlay_dir="${_overlay_path%/*}"
		[ "$_overlay_dir" = "$_overlay_path" ] && _overlay_dir="."
		_overlay_dirs+=("$_overlay_dir")
	done
	mkdir -p "${_overlay_dirs[@]}"
	_OVERLAY_DIRS_READY=1
}

# Reviewed renderer wiring (#970). The HTML/card/event code now lives in
# lib/soren_overlay_render.py so one long-lived interpreter renders every tick;
# this wrapper only passes the reviewed environment and arguments.
_overlay_render_env() {
	VIEWER_CHAT_MONITOR_SOURCE="${VIEWER_CHAT_MONITOR_SOURCE:-tmp/.viewer_chat/comment_context_history.log}" \
	VIEWER_CHAT_MONITOR_FILE="${VIEWER_CHAT_MONITOR_FILE:-tmp/state/viewer_chat_monitor.json}" \
	VIEWER_CHAT_MONITOR_LOOKBACK="${VIEWER_CHAT_MONITOR_LOOKBACK:-200}" \
	EVENT_OVERLAY_STATE_BASE="$ELOOP_LIB_DIR" \
	EVENT_OVERLAY_COMMENT_GEN_STATE="${COMMENT_GEN_STATE_FILE:-tmp/state/.comment_gen_state}" \
	EVENT_OVERLAY_RADIO_STATE="${RADIO_STATE_FILE:-tmp/state/.radio_state}" \
	HIDE_STATUS_DASHBOARD_OBSERVER_SECTION=0 \
	"$@"
}

_overlay_render_args() {
	_OVERLAY_ARGS=(
		"$out_file" "$width" "$height"
		"${EVENT_OVERLAY_EVENTS_FILE:-$ELOOP_LIB_DIR/tmp/state/overlay_events.jsonl}"
		"${EVENT_OVERLAY_HTML_FILE:-$ELOOP_LIB_DIR/tmp/state/event_overlay.html}"
		"${EVENT_OVERLAY_KEEP_EVENTS:-180}" "${EVENT_OVERLAY_VISIBLE_SEC:-18}"
		"${CODEX_WORK_OVERLAY_STATE_FILE:-$ELOOP_LIB_DIR/tmp/state/codex_work_indicator.json}"
	)
}

render_once() {
	_ensure_overlay_dirs
	_overlay_render_args
	_overlay_render_env python3 lib/soren_overlay_render.py once "${_OVERLAY_ARGS[@]}"
	printf 'generated:%s\n' "$out_file"
}

render_event_overlay_indicators() { :; }

case "$mode" in
once)
	render_once
	;;
watch)
	# #970: a single interpreter renders every tick instead of spawning
	# `python3 -` (and a `sleep` child) per interval. The shell process stays
	# as the supervised worker, so the reviewed cmdline pattern, pid file and
	# start/stop paths are unchanged.
	_ensure_overlay_dirs
	_overlay_render_args
	_overlay_render_env python3 lib/soren_overlay_render.py serve "$interval" "${_OVERLAY_ARGS[@]}"
	;;
start)
	mkdir -p "$(dirname "$pid_file")"
	mkdir -p "$(dirname "$log_file")"
	if command -v tmux >/dev/null 2>&1; then
		if tmux has-session -t "$tmux_session" 2>/dev/null; then
			old_pid=$(tmux display-message -p -t "$tmux_session" "#{pane_pid}" 2>/dev/null || true)
			printf '%s\n' "$old_pid" >"$pid_file"
			printf 'running:%s\n' "$old_pid"
			exit 0
		fi
		tmux new-session -d -s "$tmux_session" "cd '$PWD' && exec ./generate_soren_overlay.sh watch '$interval' >> '$log_file' 2>&1"
		sleep 1
		if tmux has-session -t "$tmux_session" 2>/dev/null; then
			new_pid=$(tmux display-message -p -t "$tmux_session" "#{pane_pid}" 2>/dev/null || true)
			printf '%s\n' "$new_pid" >"$pid_file"
			printf 'started:%s\n' "$new_pid"
			exit 0
		fi
		printf 'tmux-start-failed:fallback-nohup\n' >>"$log_file"
	fi
	if [ -f "$pid_file" ]; then
		old_pid=$(cat "$pid_file" 2>/dev/null || true)
		case "$old_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$old_pid" 2>/dev/null; then
				printf 'running:%s\n' "$old_pid"
				exit 0
			fi
			;;
		esac
	fi
	nohup "$0" watch "$interval" >"$log_file" 2>&1 &
	new_pid=$!
	printf '%s\n' "$new_pid" >"$pid_file"
	printf 'started:%s\n' "$new_pid"
	;;
stop)
	if command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t "$tmux_session" 2>/dev/null || true
	fi
	if [ -f "$pid_file" ]; then
		old_pid=$(cat "$pid_file" 2>/dev/null || true)
		case "$old_pid" in
		''|*[!0-9]*) ;;
		*) kill "$old_pid" 2>/dev/null || true ;;
		esac
		rm -f "$pid_file"
	fi
	printf 'stopped\n'
	;;
ensure-obs)
	render_once >/dev/null
	visibility="${2:-show}"
	case "$visibility" in show|hide) ;; *) visibility=show ;; esac
	./obs_browser_source.sh ensure "${OBS_DASHBOARD_SCENE:-soren}" "${SOREN_OVERLAY_SOURCE:-sorenOverlay}" "$out_file" "$width" "$height" "$visibility"
	;;
*)
	echo "usage: $0 once|watch [interval_sec]|start [interval_sec]|stop|ensure-obs [show|hide]" >&2
	exit 2
	;;
esac
