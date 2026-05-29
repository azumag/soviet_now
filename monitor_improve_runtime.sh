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
FAST_ESCAPE_STATE_ONLY_GRACE_SEC="${IMPROVE_MONITOR_FAST_ESCAPE_STATE_ONLY_GRACE_SEC:-180}"
LOCKDIR="tmp/.improve_monitor.lock"

case "$LONG_SEC" in ''|*[!0-9]*) LONG_SEC=3600 ;; esac
case "$STALE_SEC" in ''|*[!0-9]*) STALE_SEC=900 ;; esac
case "$FAST_ESCAPE_STATE_ONLY_GRACE_SEC" in ''|*[!0-9]*) FAST_ESCAPE_STATE_ONLY_GRACE_SEC=180 ;; esac

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

_improve_reason_get() {
	python3 - "${IMPROVE_STATE_FILE:-tmp/state/improve_state.json}" "${IMPROVE_LOCK_FILE:-tmp/improve.lock}" <<'PY'
import json
import sys

for path in sys.argv[1:]:
    try:
        reason = json.load(open(path, encoding="utf-8")).get("improve_reason") or ""
    except Exception:
        reason = ""
    if reason:
        print(reason)
        raise SystemExit(0)
print("")
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
	if [ "${SOREN_BROWSER_TAB_ACTIVATE:-0}" != "1" ]; then
		mkdir -p "$(dirname "${IMPROVE_MONITOR_LOG_FILE:-logs/improve_monitor.log}")" 2>/dev/null || true
		printf '%s [IMPROVE_ACTIVATE] event=skip_no_focus mode=%s prev_mode=disabled\n' \
			"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" \
			>>"${IMPROVE_MONITOR_LOG_FILE:-logs/improve_monitor.log}"
		return 0
	fi
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

_main_game_active_for_obs() {
	python3 - <<'PY' 2>/dev/null
import json
import sys

try:
    state = json.load(open("game_state.json", encoding="utf-8")).get("state", "")
except Exception:
    state = ""

if state and state != "STOP":
    sys.exit(0)
sys.exit(1)
PY
}

_cleanup_stale_soren91_player_if_present() {
	command -v _soren91_recovered_player_stale >/dev/null 2>&1 || return 0
	command -v _soren91_force_stop_recovered_player >/dev/null 2>&1 || return 0

	local stale_pid=""
	stale_pid=$(_soren91_recovered_player_stale 2>/dev/null || true)
	[ -n "$stale_pid" ] || return 0

	_monitor_log "improve idle but stale soren91 player remains pid=$stale_pid; cleaning normal-mode presentation"
	soren91_cleanup >/dev/null 2>&1 || true
	_soren91_force_stop_recovered_player "$stale_pid" >/dev/null 2>&1 || true
}

_reconcile_normal_obs_layout() {
	local scene="${OBS_DASHBOARD_SCENE:-soren}"
	local status_source="${STATUS_OVERLAY_SOURCE:-statsOverlay}"
	local show_status_source="${SHOW_STATUS_OVERLAY_SOURCE:-opsOverlay}"
	local game_source="${SOREN_GAME_OBS_SOURCE:-${OBS_GAME_SOURCE:-${SOREN_OBS_GAME_SOURCE_NAME:-sorengame}}}"
	local dashboard_source="${OBS_DASHBOARD_SOURCE:-dashboard}"
	local improve_source="${IMPROVE_OVERLAY_SOURCE:-improveOverlay}"
	local wildcard_overlay_source="${WILDCARD_PARALLEL_OVERLAY_SOURCE:-wildcardParallelOverlay}"
	local hide_sources="$improve_source,$wildcard_overlay_source"
	local show_sources="$status_source,$show_status_source,$game_source"

	if _main_game_active_for_obs; then
		hide_sources="$hide_sources,$dashboard_source"
	fi

	./obs_control.sh batch "$scene" show:"$show_sources" hide:"$hide_sources" >/dev/null 2>&1 || true
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

_maybe_queue_early_escape_from_monitor() {
	# The main loop queues this in post-game and next-game preflight. The monitor
	# repeats the guard so a missed threshold cannot drift into another normal game.
	[ "${WILDCARD_EARLY_ESCAPE_LOCK_ENABLED:-1}" = "1" ] || return 1
	[ "${WILDCARD_ENABLED:-0}" = "1" ] || return 1
	[ -f "${ACCUMULATED_GAMES_FILE:-tmp/state/accumulated_games.json}" ] || return 1
	[ ! -f "${IMPROVE_LOCK_FILE:-tmp/improve.lock}" ] || return 1
	[ ! -f "${TMP_STATE_DIR:-tmp/state}/rate_limit_backoff" ] || return 1
	! _is_improve_running || return 1

	command -v enrich_accumulated_game_metadata >/dev/null 2>&1 &&
		enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE" 2>/dev/null || true

	local probe action note stag rstreak acc_count batch_hash lock_file
	probe=$(
		python3 - \
			"${ACCUMULATED_GAMES_FILE:-tmp/state/accumulated_games.json}" \
			"${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" \
			"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
			"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
			"${TMP_STATE_DIR:-tmp/state}/last_rollback_pair.json" \
			"${WILDCARD_TRIGGER_STAGNATION:-3}" \
			"${WILDCARD_REGRESSION_STREAK:-2}" \
			"${WILDCARD_EARLY_ESCAPE_MIN_GAMES:-4}" \
			"${MIN_GAMES_BEFORE_IMPROVE:-12}" \
			"${MIN_GAMES_BEFORE_REGRESSION:-12}" \
			"${EARLY_COMP_TOP_GAP_MIN_RATIO:-0.85}" <<'PY' 2>/dev/null || echo "skip|unreadable|0|0|0|"
import json
import math
import os
import sys
import time

(
    acc_file,
    stagnation_file,
    rolling_file,
    current_file,
    pair_file,
    trigger_raw,
    rstreak_trigger_raw,
    early_min_raw,
    mature_raw,
    min_games_raw,
    min_ratio_raw,
) = sys.argv[1:12]

def load(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def comp(scores):
    xs = [as_int(x) for x in scores]
    if not xs:
        return 0.0
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    return 0.55 * p50 + 0.30 * p25 + 0.15 * lcb

def row_comp(row):
    explicit = as_float(row.get("comp", 0.0), 0.0)
    if explicit > 0:
        return explicit
    scores = row.get("scores", [])
    if isinstance(scores, str):
        scores = [x for x in scores.split() if x.strip()]
    if isinstance(scores, list):
        return comp(scores)
    return 0.0

acc = load(acc_file)
stagnation = load(stagnation_file)
current = load(current_file)
pair = load(pair_file)
trigger = as_int(trigger_raw, 3)
rstreak_trigger = as_int(rstreak_trigger_raw, 2)
early_min = max(1, as_int(early_min_raw, 4))
mature = max(1, as_int(mature_raw, 12))
min_games = max(1, as_int(min_games_raw, 12))
min_ratio = as_float(min_ratio_raw, 0.85)

acc_count = as_int(acc.get("count", 0), 0)
stag = as_int(stagnation.get("consecutive_no_improve", 0), 0)
rstreak = as_int(stagnation.get("regression_streak", 0), 0)
batch_hash = str(acc.get("hash", "") or "")
prefix = f"{stag}|{rstreak}|{acc_count}|{batch_hash}"

if acc_count < early_min:
    print(f"defer|early_min {acc_count}/{early_min}|{prefix}")
    raise SystemExit
if stag < trigger and rstreak < rstreak_trigger:
    print(f"skip|below_threshold {stag}/{trigger} {rstreak}/{rstreak_trigger}|{prefix}")
    raise SystemExit

russia = as_int(acc.get("russia_count", 0), 0)
soviet = 1 if bool(acc.get("soviet", False)) or as_int(acc.get("soviet_count", 0), 0) > 0 else 0
best_type = as_int(acc.get("best_max_type", 0), 0)
if soviet > 0 or russia > 0 or best_type >= 15:
    print(f"defer|progress R{russia} S{soviet} T{best_type}|{prefix}")
    raise SystemExit

scores = [as_int(x) for x in str(acc.get("scores", "") or "").split() if str(x).strip()]
batch_comp = comp(scores)
leader_comp = 0.0
rolling = load(rolling_file)
if rolling:
    for row in rolling.values():
        if not isinstance(row, dict):
            continue
        n = as_int(row.get("n", row.get("games_total", 0)), 0)
        if n < min_games:
            continue
        leader_comp = max(leader_comp, row_comp(row))
ratio = (batch_comp / leader_comp) if leader_comp > 0 else 0.0
if batch_comp > 0 and (leader_comp <= 0 or batch_comp >= leader_comp * min_ratio):
    if stagnation_file:
        stagnation["regression_streak"] = 0
        stagnation["last_event"] = "EARLY_ESCAPE_BATCH_OK"
        stagnation["updated_at"] = int(time.time())
        try:
            os.makedirs(os.path.dirname(stagnation_file) or ".", exist_ok=True)
            tmp = stagnation_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(stagnation, f, ensure_ascii=False)
            os.replace(tmp, stagnation_file)
        except Exception:
            pass
    print(f"defer|batch_ok comp={batch_comp:.1f} leader={leader_comp:.1f} ratio={ratio:.3f}|{prefix}")
    raise SystemExit

current_hash = str(current.get("hash", "") or "")
rollback_hash = str(pair.get("to_hash", "") or "")
current_n = as_int(current.get("games_total", 0) or len(current.get("scores", []) or []), 0)
if current_hash and rollback_hash and current_hash == rollback_hash and current_n < mature:
    print(f"defer|rollback_revalidate {current_hash[:8]} {current_n}/{mature}|{prefix}")
    raise SystemExit

print(f"queue|threshold {stag}/{trigger} {rstreak}/{rstreak_trigger}|{prefix}")
PY
	)
	action="${probe%%|*}"
	probe="${probe#*|}"
	note="${probe%%|*}"
	probe="${probe#*|}"
	stag="${probe%%|*}"
	probe="${probe#*|}"
	rstreak="${probe%%|*}"
	probe="${probe#*|}"
	acc_count="${probe%%|*}"
	batch_hash="${probe#*|}"

	case "$action" in
	queue)
		if [ "${HOT_STREAK_EXTEND_ENABLED:-1}" = "1" ] &&
			command -v _is_rank1_hot_streak >/dev/null 2>&1 && _is_rank1_hot_streak; then
			_monitor_log "early escape monitor defer: rank1 hot streak stagnation=${stag}/${WILDCARD_TRIGGER_STAGNATION:-3} regression_streak=${rstreak}/${WILDCARD_REGRESSION_STREAK:-2}"
			_write_status idle 0 0 0 "early_escape_deferred" "rank1 hot streak"
			return 1
		fi
		_monitor_log "early escape monitor queued: ${note} acc=${acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} hash=${batch_hash:0:12}"
		lock_file="${IMPROVE_LOCK_FILE:-tmp/improve.lock}"
		cp "${ACCUMULATED_GAMES_FILE:-tmp/state/accumulated_games.json}" "$lock_file" || return 1
		command -v enrich_accumulated_game_metadata >/dev/null 2>&1 &&
			enrich_accumulated_game_metadata "$lock_file" 2>/dev/null || true
		python3 - "$lock_file" "$stag" "$rstreak" <<'PY' 2>/dev/null || true
import json
import sys
import time

path, stag, rstreak = sys.argv[1:4]
data = json.load(open(path, encoding="utf-8"))
data["started_at"] = int(time.time())
data["improve_reason"] = "normal"
data["early_escape_lock"] = True
data["early_escape_source"] = "monitor_improve_runtime"
data["early_escape_stagnation"] = int(stag)
data["early_escape_regression_streak"] = int(rstreak)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
PY
		if [ -x ./overlay_notify.sh ]; then
			./overlay_notify.sh worker "早期脱出ロック queued (monitor)" "停滞 ${stag}/${WILDCARD_TRIGGER_STAGNATION:-3}・回帰 ${rstreak}/${WILDCARD_REGRESSION_STREAK:-2}・蓄積 ${acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12}。monitor が取りこぼし補助で改善ロックを作成" "warn" >/dev/null 2>&1 || true
		fi
		command -v enqueue_chat_message >/dev/null 2>&1 &&
			enqueue_chat_message "改善フロー: early escape queued。monitor が停滞 ${stag}/${WILDCARD_TRIGGER_STAGNATION:-3}・回帰 ${rstreak}/${WILDCARD_REGRESSION_STREAK:-2}・蓄積 ${acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} を検出し、通常満了を待たず改善ロックを作成しました。" "improve_flow" 4 || true
		command -v _clear_accumulated_data >/dev/null 2>&1 && _clear_accumulated_data
		_write_status idle 0 0 0 "early_escape_queued" "$note"
		return 0
		;;
	defer)
		_monitor_log "early escape monitor defer: ${note} stagnation=${stag}/${WILDCARD_TRIGGER_STAGNATION:-3} regression_streak=${rstreak}/${WILDCARD_REGRESSION_STREAK:-2} acc=${acc_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} hash=${batch_hash:0:12}"
		_write_status idle 0 0 0 "early_escape_deferred" "$note"
		return 1
		;;
	esac
	return 1
}

now=$(date +%s)
state_status=$(_json_get status idle)
state_pid=$(_json_get pid 0)
phase=$(_json_get phase "")
detail=$(_json_get detail "")
started_at=$(_json_get started_at 0)
updated_at=$(_json_get updated_at 0)
improve_reason=$(_improve_reason_get 2>/dev/null || echo "")
updated_age=$(( now - ${updated_at:-0} ))
log_age=$(_file_age_sec "$IMPROVE_AI_LOG_FILE" "$now")
live_pid=$(_find_live_improve_pid 2>/dev/null || true)
case "$live_pid" in ''|*[!0-9]*) live_pid=0 ;; esac
state_only_running=0

if [ "$state_status" = "running" ] && [ "$live_pid" -eq 0 ]; then
	[ "$updated_age" -lt "$log_age" ] && stale_age="$updated_age" || stale_age="$log_age"
	if { [ "$improve_reason" = "wildcard" ] || [ "$improve_reason" = "archive_restart" ]; } &&
		[ "$stale_age" -ge "$FAST_ESCAPE_STATE_ONLY_GRACE_SEC" ]; then
		_monitor_log "fast escape running state references no visible parent pid=${state_pid:-0} stale=${stale_age}s; harvesting immediately"
	elif [ "$stale_age" -lt "$STALE_SEC" ]; then
		_monitor_log "running state has no visible eloop_improve pid but activity is fresh; preserving active state stale=${stale_age}s"
		live_pid="$state_pid"
		case "$live_pid" in ''|*[!0-9]*) live_pid=0 ;; esac
		state_only_running=1
		if [ "$live_pid" -eq 0 ]; then
			_write_status running 0 0 "$stale_age" "state_activity_fresh" "improve state/log active; pid not visible"
			exit 0
		fi
	fi
	state_pid_alive=0
	if [ "$state_only_running" -ne 1 ]; then
		case "$state_pid" in
		''|0|*[!0-9]*) state_pid_alive=0 ;;
		*)
			if kill -0 "$state_pid" 2>/dev/null; then
				state_pid_alive=1
			fi
			;;
		esac
		if [ "$state_pid_alive" -eq 0 ]; then
			_monitor_log "running state references dead improve pid=${state_pid:-0}; harvesting immediately"
			check_and_harvest_improvement >/dev/null 2>&1 || true
			state_status=$(_json_get status idle)
			state_pid=$(_json_get pid 0)
			phase=$(_json_get phase "")
			detail=$(_json_get detail "")
			started_at=$(_json_get started_at 0)
			updated_at=$(_json_get updated_at 0)
			updated_age=$(( now - ${updated_at:-0} ))
			log_age=$(_file_age_sec "$IMPROVE_AI_LOG_FILE" "$now")
		else
			_monitor_log "running state has no visible eloop_improve pid; using state pid=${state_pid}"
			live_pid="$state_pid"
			state_only_running=1
		fi
	fi
fi

if [ "$live_pid" -ne 0 ]; then
	if [ "${started_at:-0}" -le 0 ]; then
		started_at=$(_pid_lstart_epoch "$live_pid")
	fi
	elapsed=$(( now - ${started_at:-now} ))
	[ "$updated_age" -lt "$log_age" ] && stale_age="$updated_age" || stale_age="$log_age"

	./generate_improve_overlay.sh once >/dev/null 2>&1 || true
	if [ "${phase:-}" = "wildcard_parallel" ]; then
		./obs_control.sh hide soren "$IMPROVE_OVERLAY_SOURCE" >/dev/null 2>&1 || true
	else
		./obs_control.sh show soren "$IMPROVE_OVERLAY_SOURCE" >/dev/null 2>&1 || true
	fi
	if [ -n "${IMPROVE_LEGACY_CONSOLE_SOURCE:-}" ]; then
		./obs_control.sh hide soren "$IMPROVE_LEGACY_CONSOLE_SOURCE" >/dev/null 2>&1 || true
	fi
	# 堅牢化: improve_state の reason/phase が空でも wildcard_parallel status が
	# アクティブなら param並列調整(隔離評価)とみなし soren91 を停止する。
	# 孤児化した wildcard_parallel で improve_state がidleに戻り、下の case が
	# *) にマッチして soren91_start(代打)してしまう事故を防ぐ。
	if command -v _wildcard_parallel_active >/dev/null 2>&1 && _wildcard_parallel_active 2>/dev/null; then
		# wildcard_parallel status がアクティブ → 隔離評価。soren91代打を立てず停止維持。
		if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
			_monitor_log "wildcard_parallel active (status file); forcing soren91_stop (no Meriken substitute)"
			SOREN91_STOP_TIMEOUT=0 soren91_stop >/dev/null 2>&1 || soren91_cleanup >/dev/null 2>&1 || true
		fi
	else
		case "${improve_reason}:${phase:-}:${detail:-}" in
		wildcard:*|archive_restart:*|*:wildcard_parallel:*|*:*:post_improve_param_parallel*)
			if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
				_monitor_log "isolated improve reason=${improve_reason} phase=${phase:-?} detail=${detail:-?}; forcing existing soren91_stop"
				SOREN91_STOP_TIMEOUT=0 soren91_stop >/dev/null 2>&1 || soren91_cleanup >/dev/null 2>&1 || true
			else
				_monitor_log "improve reason=${improve_reason} phase=${phase:-?} detail=${detail:-?}; leaving soren91 stopped and preserving non-Meriken presentation"
			fi
			;;
		*)
			if command -v soren91_is_running >/dev/null 2>&1 && ! soren91_is_running 2>/dev/null; then
				if command -v _soren91_stop_in_progress >/dev/null 2>&1 && _soren91_stop_in_progress; then
					_monitor_log "improve running but soren91 stop is in progress; leaving process control to soren91_stop"
				else
					_monitor_log "improve running but soren91 is not active; calling existing soren91_start"
					soren91_start >/dev/null 2>&1 || true
				fi
			fi
			_activate_shared_browser_tab meriken
			;;
		esac
	fi

	if [ "$elapsed" -ge "$LONG_SEC" ] || [ "$stale_age" -ge "$STALE_SEC" ]; then
		note="long_or_stale phase=${phase:-?} detail=${detail:-?}"
		_monitor_log "attention: improve pid=$live_pid elapsed=${elapsed}s stale=${stale_age}s ${note}"
		_write_status attention "$live_pid" "$elapsed" "$stale_age" "observe_only" "$note"
	else
		if [ "$state_only_running" -eq 1 ]; then
			_write_status running "$live_pid" "$elapsed" "$stale_age" "state_activity_fresh" "improve state/log active; parent pid not visible"
		else
			_write_status running "$live_pid" "$elapsed" "$stale_age" "layout_reconciled" "improve active"
		fi
	fi
	exit 0
fi

# 根本修正: improveのpidを見失っても(live_pid=0)、wildcard_parallel status が
# アクティブなら param並列(隔離評価)が前面を所有しているとみなし、normal-mode へ
# 復帰しない。pid消失→idle誤判定→メイン/soren91再開→param並列と三重並行＋OBS
# バインド孤児化、を防ぐ。_wildcard_parallel_active は phase=generating/running の
# 間のみ true で、started_at から WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC(既定3600s)
# 超過時は false に落ちるため、stale な running で永久ブロックされることはない。
if command -v _wildcard_parallel_active >/dev/null 2>&1 && _wildcard_parallel_active 2>/dev/null; then
	if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
		_monitor_log "wildcard_parallel active (status file) but improve pid lost; keeping soren91 stopped, NOT resuming normal mode"
		SOREN91_STOP_TIMEOUT=0 soren91_stop >/dev/null 2>&1 || soren91_cleanup >/dev/null 2>&1 || true
	else
		_monitor_log "wildcard_parallel active (status file) but improve pid lost; preserving isolated state, NOT resuming normal mode"
	fi
	_write_status running 0 0 0 "wildcard_parallel_active_pidless" "param parallel active per status; main/soren91 kept stopped"
	exit 0
fi

check_and_harvest_improvement >/dev/null 2>&1 || true

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

_maybe_queue_early_escape_from_monitor || true

if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
	_monitor_log "improve idle but soren91 is active; forcing existing soren91_stop for normal-mode return"
	SOREN91_STOP_TIMEOUT=0 soren91_stop >/dev/null 2>&1 || soren91_cleanup >/dev/null 2>&1 || true
fi
_cleanup_stale_soren91_player_if_present
_activate_shared_browser_tab china
_reconcile_normal_obs_layout
_write_status idle 0 0 0 "layout_reconciled" "improve idle"
