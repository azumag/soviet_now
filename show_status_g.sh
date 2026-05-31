#!/bin/zsh
# show_status_g.sh - CUI Graphical Statistics Dashboard
#
# Usage: ./show_status_g.sh             # 10秒間隔で常時表示
#        ./show_status_g.sh 5           # 5秒間隔で常時表示
#        ./show_status_g.sh --html-once # 600x900 HTML overlay を1回生成
#        ./show_status_g.sh --html-watch [sec]
#        ./show_status_g.sh --html-start [sec]
#        ./show_status_g.sh --html-stop
#        ./show_status_g.sh --html-obs [show|hide]

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

_is_wildcard_parallel_active() {
	# 2026-05-31 fix (OBS overlay flicker): improve_state.json routinely goes idle while
	# wildcard_parallel.py is still running (orphaned), so checking ONLY it returns false during
	# a live param-parallel — then --html-obs re-SHOWS ops/stats overlays and they FLICKER (~15s)
	# against the param-parallel reconcile that hides them. So ALSO treat a FRESH
	# wildcard_parallel_status.json (phase generating/running, mtime <= 180s so a crashed/stale
	# run releases the overlays) as active.
	local _wp="${WILDCARD_PARALLEL_STATUS_FILE:-tmp/state/wildcard_parallel_status.json}"
	if [ -f "$_wp" ] && python3 - "$_wp" <<'PY' >/dev/null 2>&1
import json, os, sys, time
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
try:
    age = time.time() - os.path.getmtime(sys.argv[1])
except Exception:
    age = 1e9
raise SystemExit(0 if str(d.get("phase") or "") in ("generating", "running") and age <= 600 else 1)
PY
	then
		return 0
	fi
	# Process-liveness fallback: if wildcard_parallel.py is running, treat as active
	pgrep -f 'python.* wildcard_parallel\.py' >/dev/null 2>&1 && return 0
	[ -f tmp/state/improve_state.json ] || return 1
	python3 - tmp/state/improve_state.json <<'PY' >/dev/null 2>&1
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
active = data.get("status") == "running" and data.get("phase") == "wildcard_parallel"
raise SystemExit(0 if active else 1)
PY
}

case "${1:-}" in
--html-once)
	exec ./generate_status_overlay.sh once
	;;
--html-watch)
	exec ./generate_status_overlay.sh watch "${2:-2}"
	;;
--html-start)
	exec ./generate_status_overlay.sh start "${2:-2}"
	;;
--html-stop)
	exec ./generate_status_overlay.sh stop
	;;
	--html-obs)
		# statsOverlay is a persistent monitoring surface. Older running
		# supervisors may still ask for "hide"; keep this hot path visible.
		if _is_wildcard_parallel_active; then
			exec ./generate_status_overlay.sh ensure-obs hide
		fi
		exec ./generate_status_overlay.sh ensure-obs show
		;;
esac

WATCH_INTERVAL=${1:-10}
DROP_REFRESH_INTERVAL=${SHOW_STATUS_DROP_REFRESH_INTERVAL:-0.25}
LATEST_DROP_LOG="game_history/latest.jsonl"

case "$WATCH_INTERVAL" in
''|*[!0-9]*) WATCH_INTERVAL=10 ;;
esac
[[ "$DROP_REFRESH_INTERVAL" =~ '^[0-9]+([.][0-9]+)?$' ]] || DROP_REFRESH_INTERVAL=0.25
(( WATCH_INTERVAL < 1 )) && WATCH_INTERVAL=10
(( DROP_REFRESH_INTERVAL <= 0 )) && DROP_REFRESH_INTERVAL=0.25
(( DROP_REFRESH_INTERVAL > WATCH_INTERVAL )) && DROP_REFRESH_INTERVAL=$WATCH_INTERVAL

CLR=$'\033[K'

render() {
	local buf=""
	while IFS= read -r line; do
		buf+="${line}${CLR}"$'\n'
	done
	printf '\033[H%s\033[J' "$buf"
}

latest_drop_signature() {
	[[ -f "$LATEST_DROP_LOG" ]] || {
		printf 'missing'
		return
	}
	local stat_sig last_turn
	stat_sig=$(stat -f '%m:%z' "$LATEST_DROP_LOG" 2>/dev/null || printf 'unknown')
	last_turn=$(tail -n 1 "$LATEST_DROP_LOG" 2>/dev/null | sed -nE 's/.*"turn"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p')
	printf '%s:%s' "$stat_sig" "$last_turn"
}

render_dashboard_once() {
	python3 status_dashboard.py 2>/dev/null | render
}

wait_for_status_update() {
	local last_drop_sig="$1"
	local deadline=$(( $(date +%s) + WATCH_INTERVAL ))
	local current_drop_sig=""

	while (( $(date +%s) < deadline )); do
		sleep "$DROP_REFRESH_INTERVAL"
		current_drop_sig=$(latest_drop_signature)
		[[ "$current_drop_sig" != "$last_drop_sig" ]] && return 0
	done
	return 0
}

printf '\033[?25l'          # カーソル非表示
trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
printf '\033[2J'            # 初回だけ画面クリア

while true; do
	current_drop_sig=$(latest_drop_signature)
	render_dashboard_once
	wait_for_status_update "$current_drop_sig"
done
