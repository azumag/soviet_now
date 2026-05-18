#!/bin/bash
# monitor_improve_runtime.sh - one-shot watchdog for improve/runtime presentation
#
# Intended to be run periodically. It reconciles observable state through the
# existing control functions only; it does not directly kill worker processes.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a
source ./eloop_lib.sh

LOG_FILE="${IMPROVE_MONITOR_LOG_FILE:-logs/improve_monitor.log}"
STATUS_FILE="${IMPROVE_MONITOR_STATUS_FILE:-tmp/state/improve_monitor_status.json}"
IMPROVE_LAST_ACTIVATE_MODE=""
IMPROVE_LAST_ACTIVATE_STATE_FILE="${IMPROVE_LAST_ACTIVATE_STATE_FILE:-tmp/.soren91_improve_last_activate_mode}"
LONG_SEC="${IMPROVE_MONITOR_LONG_SEC:-3600}"
STALE_SEC="${IMPROVE_MONITOR_STALE_SEC:-900}"
LOCKDIR="tmp/.improve_monitor.lock"

case "$LONG_SEC" in ''|*[!0-9]*) LONG_SEC=3600 ;; esac
case "$STALE_SEC" in ''|*[!0-9]*) STALE_SEC=900 ;; esac

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$STATUS_FILE")" tmp 2>/dev/null || true

if ! mkdir "$LOCKDIR" 2>/dev/null; then
	old_pid=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		printf '[%s] monitor already running pid=%s\n' "$(date '+%H:%M:%S')" "$old_pid" >>"$LOG_FILE"
		exit 0
	fi
	rm -rf "$LOCKDIR"
	mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR"' EXIT

_monitor_log() {
	printf '[%s] [monitor] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG_FILE" >&2
}

_json_get() {
	local key="$1" default="${2:-}"
	python3 - "$key" "$default" <<'PY'
import json
import sys

key, default = sys.argv[1:3]
try:
    data = json.load(open("tmp/state/improve_state.json", encoding="utf-8"))
except Exception:
    print(default)
    raise SystemExit(0)
value = data.get(key, default)
print("" if value is None else value)
PY
}

_pid_lstart_epoch() {
	local pid="$1"
	case "$pid" in ''|0|*[!0-9]*) echo 0; return 0 ;; esac
	ps -p "$pid" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0
}

_file_age_sec() {
	local path="$1" now="$2" mtime=0
	[ -f "$path" ] || { echo 999999; return 0; }
	mtime=$(stat -f '%m' "$path" 2>/dev/null || echo 0)
	[ "${mtime:-0}" -gt 0 ] || { echo 999999; return 0; }
	echo $(( now - mtime ))
}

_activate_shared_browser_tab() {
	local mode="${1:-china}"
	local last_mode
	mode="$(printf '%s' "$mode" | tr -d '[:space:]')"
	last_mode="$(cat "$IMPROVE_LAST_ACTIVATE_STATE_FILE" 2>/dev/null || printf '')"
	if [ "$IMPROVE_LAST_ACTIVATE_MODE" = "$mode" ] || [ "$last_mode" = "$mode" ]; then
		mkdir -p "$(dirname "${IMPROVE_MONITOR_LOG_FILE:-logs/improve_monitor.log}")" 2>/dev/null || true
		printf '%s [IMPROVE_ACTIVATE] event=skip mode=%s prev_mode=%s\n' \
			"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" "$last_mode" \
			>>"${IMPROVE_MONITOR_LOG_FILE:-logs/improve_monitor.log}"
		return 0
	fi
	mkdir -p "$(dirname "$IMPROVE_LAST_ACTIVATE_STATE_FILE")" 2>/dev/null || true
	printf '%s [IMPROVE_ACTIVATE] event=activate mode=%s prev_mode=%s\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" "$last_mode" \
		>>"${IMPROVE_MONITOR_LOG_FILE:-logs/improve_monitor.log}"
	node - "$mode" <<'NODE' >/dev/null 2>>logs/improve_monitor.log || true
const fs = require('fs');
const mode = process.argv[2] || 'china';
let base = `http://127.0.0.1:${process.env.SOREN_CDP_PORT || '9222'}`;
try {
  const endpoint = JSON.parse(fs.readFileSync('tmp/cdp_endpoint.json', 'utf8'));
  if (endpoint && endpoint.url) base = endpoint.url.replace('localhost', '127.0.0.1');
} catch {}

function matches(target) {
  const title = target.title || '';
  const url = target.url || '';
  if (mode === 'meriken') {
    return /91人対戦|ソ連ゲーム91|sorengame91|74337\.play\.unityroom\.com/.test(title + ' ' + url);
  }
  return /^https?:\/\/(localhost|127\.0\.0\.1):8080\b/.test(url) || /Unity WebGL Player \| soren-game/.test(title);
}

(async () => {
  const targets = await fetch(`${base}/json`).then(r => r.json());
  const page = targets.find(t => t.type === 'page' && matches(t));
  if (!page || !page.id) return;
  await fetch(`${base}/json/activate/${encodeURIComponent(page.id)}`, { method: 'PUT' });
})();
NODE
	IMPROVE_LAST_ACTIVATE_MODE="$mode"
	mkdir -p "$(dirname "$IMPROVE_LAST_ACTIVATE_STATE_FILE")" 2>/dev/null || true
	printf '%s\n' "$mode" > "$IMPROVE_LAST_ACTIVATE_STATE_FILE"
}

_write_status() {
	local status="$1" improve_pid="$2" elapsed="$3" stale="$4" action="$5" note="$6"
	python3 - "$STATUS_FILE" "$status" "$improve_pid" "$elapsed" "$stale" "$action" "$note" <<'PY'
import json
import sys
import time

path, status, pid, elapsed, stale, action, note = sys.argv[1:8]
def as_int(v):
    try:
        return int(v)
    except Exception:
        return 0
payload = {
    "checked_at": int(time.time()),
    "status": status,
    "improve_pid": as_int(pid),
    "elapsed_sec": as_int(elapsed),
    "stale_sec": as_int(stale),
    "action": action,
    "note": note,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
}

now=$(date +%s)
check_and_harvest_improvement >/dev/null 2>&1 || true

state_status=$(_json_get status idle)
state_pid=$(_json_get pid 0)
phase=$(_json_get phase "")
detail=$(_json_get detail "")
started_at=$(_json_get started_at 0)
updated_at=$(_json_get updated_at 0)
live_pid=$(_find_live_improve_pid 2>/dev/null || true)
case "$live_pid" in ''|*[!0-9]*) live_pid=0 ;; esac

if [ "$state_status" = "running" ] && [ "$live_pid" -eq 0 ]; then
	_monitor_log "running state has no live eloop_improve pid; existing harvest will reconcile"
	check_and_harvest_improvement >/dev/null 2>&1 || true
	state_status=$(_json_get status idle)
	state_pid=$(_json_get pid 0)
fi

if [ "$live_pid" -ne 0 ]; then
	if [ "${started_at:-0}" -le 0 ]; then
		started_at=$(_pid_lstart_epoch "$live_pid")
	fi
	elapsed=$(( now - ${started_at:-now} ))
	updated_age=$(( now - ${updated_at:-0} ))
	log_age=$(_file_age_sec "$IMPROVE_AI_LOG_FILE" "$now")
	[ "$updated_age" -lt "$log_age" ] && stale_age="$updated_age" || stale_age="$log_age"

	./generate_improve_overlay.sh once >/dev/null 2>&1 || true
	./obs_control.sh show soren "$IMPROVE_OVERLAY_SOURCE" >/dev/null 2>&1 || true
	if [ -n "${IMPROVE_LEGACY_CONSOLE_SOURCE:-}" ]; then
		./obs_control.sh hide soren "$IMPROVE_LEGACY_CONSOLE_SOURCE" >/dev/null 2>&1 || true
	fi
	if command -v soren91_is_running >/dev/null 2>&1 && ! soren91_is_running 2>/dev/null; then
		if command -v _soren91_stop_in_progress >/dev/null 2>&1 && _soren91_stop_in_progress; then
			_monitor_log "improve running but soren91 stop is in progress; leaving process control to soren91_stop"
		else
			_monitor_log "improve running but soren91 is not active; calling existing soren91_start"
			soren91_start >/dev/null 2>&1 || true
		fi
	fi
	_activate_shared_browser_tab meriken

	if [ "$elapsed" -ge "$LONG_SEC" ] || [ "$stale_age" -ge "$STALE_SEC" ]; then
		note="long_or_stale phase=${phase:-?} detail=${detail:-?}"
		_monitor_log "attention: improve pid=$live_pid elapsed=${elapsed}s stale=${stale_age}s ${note}"
		_write_status attention "$live_pid" "$elapsed" "$stale_age" "observe_only" "$note"
	else
		_write_status running "$live_pid" "$elapsed" "$stale_age" "layout_reconciled" "improve active"
	fi
	exit 0
fi

./generate_improve_overlay.sh once >/dev/null 2>&1 || true
./obs_control.sh hide soren "$IMPROVE_OVERLAY_SOURCE" >/dev/null 2>&1 || true
if [ -n "${IMPROVE_LEGACY_CONSOLE_SOURCE:-}" ]; then
	./obs_control.sh hide soren "$IMPROVE_LEGACY_CONSOLE_SOURCE" >/dev/null 2>&1 || true
fi
if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
	_activate_shared_browser_tab meriken
	_write_status manual 0 0 0 "manual_meriken_active" "manual meriken mode"
	exit 0
fi
if command -v scheduled_meriken_time_is_active >/dev/null 2>&1 && scheduled_meriken_time_is_active; then
	_activate_shared_browser_tab meriken
	_write_status scheduled 0 0 0 "scheduled_meriken_active" "scheduled meriken window"
	exit 0
fi

if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
	_monitor_log "improve idle but soren91 is active; calling existing soren91_stop"
	soren91_stop >/dev/null 2>&1 || soren91_cleanup >/dev/null 2>&1 || true
fi
_activate_shared_browser_tab china
_write_status idle 0 0 0 "layout_reconciled" "improve idle"
