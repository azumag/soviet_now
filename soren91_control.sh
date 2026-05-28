# soren91_control.sh - soren91 (メリケンAI) の起動・停止・改善キック管理
#
# eloop_lib.sh から source される。
# SOREN91_ENABLED=1 (.env) でなければ全関数は即 return 0。

# --- 定数 ---
_soren91_env_get() {
	local key="$1"
	local env_file="$ELOOP_LIB_DIR/.env"
	[ -f "$env_file" ] || return 1
	local value=""
	value=$(grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n 1 | cut -d= -f2-)
	[ -n "$value" ] || return 1
	value="${value#\"}"
	value="${value%\"}"
	printf '%s' "$value"
}

SOREN91_ENABLED="$(_soren91_env_get SOREN91_ENABLED 2>/dev/null || printf '%s' "${SOREN91_ENABLED:-0}")"
SOREN91_STOP_TIMEOUT="${SOREN91_STOP_TIMEOUT:-300}"
SOREN91_DIR="$ELOOP_LIB_DIR/soren91"
SOREN91_PID_FILE="$SOREN91_DIR/tmp/soren91.pid"
SOREN91_MAIN_PID_FILE="$SOREN91_DIR/tmp/main.pid"
SOREN91_IMPROVE_PID_FILE="$SOREN91_DIR/tmp/soren91_improve.pid"
SOREN91_IMPROVE_LOCK="$SOREN91_DIR/tmp/soren91_improve.lock"
SOREN91_SESSION_FILE="$SOREN91_DIR/tmp/session_games.json"
SOREN91_STOP_FILE="$SOREN91_DIR/tmp/stop"
SOREN91_STOPPING_FILE="$SOREN91_DIR/tmp/stopping"
SOREN91_RUNNER_SCRIPT="$SOREN91_DIR/run_player_loop.sh"
SOREN91_VOICEVOX_SPEAKER="$(_soren91_env_get SOREN91_VOICEVOX_SPEAKER 2>/dev/null || printf '%s' "${SOREN91_VOICEVOX_SPEAKER:-46}")"
SOREN91_OBS_CONTROL="$ELOOP_LIB_DIR/obs_control.sh"
SOREN91_OBS_INPUT_NAME="$(_soren91_env_get SOREN91_OBS_INPUT_NAME 2>/dev/null || _soren91_env_get SOREN91_OBS_SOURCE 2>/dev/null || printf '%s' "${SOREN91_OBS_INPUT_NAME:-${SOREN91_OBS_SOURCE:-}}")"
SOREN91_AUDIO_GAIN_MULTIPLIER="$(_soren91_env_get SOREN91_AUDIO_GAIN_MULTIPLIER 2>/dev/null || printf '%s' "${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}")"
SOREN91_TEXT_FALLBACKS="$(_soren91_env_get SOREN91_TEXT_FALLBACKS 2>/dev/null || printf '%s' "${SOREN91_TEXT_FALLBACKS:-claude}")"
SOREN91_SHARED_BROWSER="$(_soren91_env_get SOREN91_SHARED_BROWSER 2>/dev/null || printf '%s' "${SOREN91_SHARED_BROWSER:-1}")"
MANUAL_MERIKEN_MODE_FILE="${MANUAL_MERIKEN_MODE_FILE:-$TMP_STATE_DIR/manual_meriken_mode.json}"
SOREN91_MERIKEN_IMPROVE_INTERVAL="${SOREN91_MERIKEN_IMPROVE_INTERVAL:-12}"
SOREN91_CAPITALISM_CORNER_ENABLED="${SOREN91_CAPITALISM_CORNER_ENABLED:-1}"
MERIKEN_TIME_START_HOUR="${MERIKEN_TIME_START_HOUR:-20}"
MERIKEN_TIME_END_HOUR="${MERIKEN_TIME_END_HOUR:-21}"
MERIKEN_TIME_STATE_FILE="${MERIKEN_TIME_STATE_FILE:-$TMP_STATE_DIR/meriken_time_state.json}"
SOREN91_MODE_FLAG_FILE="${SOREN91_MODE_FLAG_FILE:-$ELOOP_LIB_DIR/tmp/.soren91_mode_active}"
SOREN91_LAST_ACTIVATE_MODE=""
SOREN91_LAST_ACTIVATE_STATE_FILE="${SOREN91_LAST_ACTIVATE_STATE_FILE:-$ELOOP_LIB_DIR/tmp/.soren91_last_activate_mode}"
SOREN91_ACTIVATE_LOG_FILE="${SOREN91_ACTIVATE_LOG_FILE:-$ELOOP_LIB_DIR/tmp/soren91_activate.log}"

# 改善モード中か判定 (soren91_control.sh 単独起動でも壊れないようガード付き)
_soren91_improve_active() {
	if command -v _is_improve_running >/dev/null 2>&1; then
		_is_improve_running
		return $?
	fi
	local lock="${IMPROVE_LOCK_FILE:-$ELOOP_LIB_DIR/tmp/improve.lock}"
	local state="${IMPROVE_STATE_FILE:-$ELOOP_LIB_DIR/tmp/state/improve_state.json}"
	[ -f "$lock" ] || return 1
	grep -q '"status"[[:space:]]*:[[:space:]]*"running"\|"status"[[:space:]]*:[[:space:]]*"manual"' "$state" 2>/dev/null
}

_soren91_switch_obs_layout() {
	local mode="${1:-}"
	local status_source="${STATUS_OVERLAY_SOURCE:-statsOverlay}"
	local show_status_source="${SHOW_STATUS_OVERLAY_SOURCE:-opsOverlay}"
	local dashboard_source="${OBS_DASHBOARD_SOURCE:-dashboard}"
	local game_source="${SOREN_GAME_OBS_SOURCE:-${OBS_GAME_SOURCE:-${SOREN_OBS_GAME_SOURCE_NAME:-sorengame}}}"
	local china_show_sources="$dashboard_source"
	local meriken_show_sources="$status_source,$show_status_source"
	local meriken_hide_sources="$dashboard_source"
	local s91_show_op=""
	local s91_hide_op=""
	if [ -n "$game_source" ]; then
		china_show_sources="$game_source,$china_show_sources"
		meriken_show_sources="$meriken_show_sources,$game_source"
	fi
	if [ -n "$SOREN91_OBS_INPUT_NAME" ] && [ "$SOREN91_OBS_INPUT_NAME" != "$game_source" ]; then
		s91_show_op="show:$SOREN91_OBS_INPUT_NAME"
		s91_hide_op="hide:$SOREN91_OBS_INPUT_NAME"
	fi
	[ -x "$SOREN91_OBS_CONTROL" ] || return 0
	case "$mode" in
	meriken)
		if [ -n "$game_source" ] && [ -x "$ELOOP_LIB_DIR/obs_window_capture_source.sh" ]; then
			OBS_WINDOW_CAPTURE_AUDIO=0 OBS_WINDOW_AUDIO_SOURCE="${SOREN91_OBS_AUDIO_SOURCE:-soren91Audio}" OBS_WINDOW_AUDIO_SOURCE_ENABLED=1 "$ELOOP_LIB_DIR/obs_window_capture_source.sh" ensure soren "$game_source" '91人対戦|ソ連ゲーム91' com.google.chrome.for.testing show >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/obs_control.err.log" || true
		fi
		"$SOREN91_OBS_CONTROL" batch soren show:"$meriken_show_sources" $s91_show_op hide:"$meriken_hide_sources" >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/obs_control.err.log" &
		_soren91_activate_shared_browser_tab meriken
		;;
	china)
		if [ -n "$game_source" ] && [ -x "$ELOOP_LIB_DIR/obs_window_capture_source.sh" ]; then
			OBS_WINDOW_CAPTURE_AUDIO=0 OBS_WINDOW_AUDIO_SOURCE="${SOREN91_OBS_AUDIO_SOURCE:-soren91Audio}" OBS_WINDOW_AUDIO_SOURCE_ENABLED=0 "$ELOOP_LIB_DIR/obs_window_capture_source.sh" ensure soren "$game_source" 'Unity WebGL Player \| soren-game' com.google.chrome.for.testing show >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/obs_control.err.log" || true
		fi
		# 改善中も stats/ops は監視用に維持し、dashboard/game だけを
		# china レイアウトへ戻す。改善オーバーレイは別途管理される。
		if _soren91_improve_active; then
			"$SOREN91_OBS_CONTROL" batch soren show:"$status_source","$show_status_source","$china_show_sources" $s91_hide_op >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/obs_control.err.log" &
		else
			"$SOREN91_OBS_CONTROL" batch soren show:"$status_source","$show_status_source","$china_show_sources" $s91_hide_op >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/obs_control.err.log" &
		fi
		_soren91_activate_shared_browser_tab china
		;;
	*)
		return 1
		;;
	esac
}

_soren91_cdp_base_url() {
	local cdp_port="${SOREN_CDP_PORT:-9222}"
	local base="http://127.0.0.1:$cdp_port"
	if [ -f "$ELOOP_LIB_DIR/tmp/cdp_endpoint.json" ]; then
		local parsed_base=""
		parsed_base=$(
			python3 - "$ELOOP_LIB_DIR/tmp/cdp_endpoint.json" "$base" <<'PY' 2>/dev/null
import json
import sys

path, default = sys.argv[1:3]
try:
    url = json.load(open(path, encoding="utf-8")).get("url") or default
except Exception:
	    url = default
print(url.replace("localhost", "127.0.0.1"))
PY
		)
		[ -n "$parsed_base" ] && base="$parsed_base"
	fi
	printf '%s' "$base"
}

_soren91_log_activate_state() {
	local event="$1"
	local mode="${2:-}"
	local prev_mode="${3:-}"
	mkdir -p "$(dirname "$SOREN91_ACTIVATE_LOG_FILE")" 2>/dev/null || true
	printf '%s [SOREN91_ACTIVATE] event=%s mode=%s prev_mode=%s\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$event" "$mode" "$prev_mode" \
		>> "$SOREN91_ACTIVATE_LOG_FILE"
}

_soren91_activate_shared_browser_tab() {
	local mode="${1:-meriken}"
	mode="$(printf '%s' "$mode" | tr -d '[:space:]')"
	if [ "${SOREN_BROWSER_TAB_ACTIVATE:-0}" != "1" ]; then
		_soren91_log_activate_state "skip_no_focus" "$mode" "disabled"
		return 0
	fi
	local base
	local last_mode
	last_mode="$(cat "$SOREN91_LAST_ACTIVATE_STATE_FILE" 2>/dev/null || printf '')"
	if [ "$SOREN91_LAST_ACTIVATE_MODE" = "$mode" ] || [ "$last_mode" = "$mode" ]; then
		_soren91_log_activate_state "skip" "$mode" "$last_mode"
		return 0
	fi
	_soren91_log_activate_state "activate" "$mode" "$last_mode"
	base=$(_soren91_cdp_base_url)
	node - "$base" "$mode" <<'NODE' >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/soren91_cdp.err.log" &
const base = process.argv[2];
const mode = process.argv[3] || 'meriken';

function matches(target) {
  const text = `${target.title || ''} ${target.url || ''}`;
  if (mode === 'meriken') {
    return /91人対戦|ソ連ゲーム91|sorengame91|play\.unityroom\.com/.test(text);
  }
  return /^https?:\/\/(localhost|127\.0\.0\.1):8080\b/.test(target.url || '') ||
    /Unity WebGL Player \| soren-game/.test(target.title || '');
}

(async () => {
  const targets = await fetch(`${base}/json`).then(r => r.json());
  const page = targets.find(t => t.type === 'page' && matches(t));
  if (page?.id) {
    await fetch(`${base}/json/activate/${encodeURIComponent(page.id)}`, { method: 'PUT' });
  }
})().catch(() => {});
NODE
	SOREN91_LAST_ACTIVATE_MODE="$mode"
	mkdir -p "$(dirname "$SOREN91_LAST_ACTIVATE_STATE_FILE")" 2>/dev/null || true
	printf '%s\n' "$mode" > "$SOREN91_LAST_ACTIVATE_STATE_FILE"
}

_soren91_close_shared_game_tabs() {
	local base
	base=$(_soren91_cdp_base_url)
	node - "$base" <<'NODE' >/dev/null 2>>"$ELOOP_LIB_DIR/tmp/soren91_cdp.err.log" || true
const base = process.argv[2];
(async () => {
  const targets = await fetch(`${base}/json`).then(r => r.json());
  for (const target of targets) {
    const text = `${target.title || ''} ${target.url || ''}`;
    if (target.type !== 'page') continue;
    if (!/91人対戦|ソ連ゲーム91|sorengame91|play\.unityroom\.com/.test(text)) continue;
    if (target.id) {
      await fetch(`${base}/json/close/${encodeURIComponent(target.id)}`).catch(() => {});
    }
  }
})().catch(() => {});
NODE
}

_soren91_standalone_user_data_dir() {
	printf '%s' "${SOREN91_STANDALONE_USER_DATA_DIR:-$SOREN91_DIR/tmp/standalone_chromium_profile}"
}

_soren91_pid_is_alive() {
	local pid="${1:-}"
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	if kill -0 "$pid" 2>/dev/null; then
		return 0
	fi
	if [ -f "$SOREN91_DIR/tmp/soren91.log" ] &&
		lsof -nP "$SOREN91_DIR/tmp/soren91.log" 2>/dev/null |
			awk -v target="$pid" 'NR > 1 && $2 == target { found=1 } END { exit(found ? 0 : 1) }'; then
		return 0
	fi
	if [ "$(tmux display-message -p -t soren91_runner '#{pane_pid}' 2>/dev/null || true)" = "$pid" ]; then
		return 0
	fi
	ps -p "$pid" -o pid= 2>/dev/null | awk 'NF { found=1 } END { exit(found ? 0 : 1) }'
}

_soren91_kill_runner_session() {
	command -v tmux >/dev/null 2>&1 || return 0
	tmux has-session -t soren91_runner 2>/dev/null || return 0
	log "[SOREN91] Killing stray tmux runner session"
	tmux kill-session -t soren91_runner 2>/dev/null || true
}

_soren91_scan_standalone_browser_pids() {
	[ "${SOREN91_SHARED_BROWSER:-0}" = "1" ] && return 0
	local user_data_dir cdp_port
	user_data_dir=$(_soren91_standalone_user_data_dir)
	cdp_port="${SOREN91_STANDALONE_CDP_PORT:-9223}"
	ps -Ao pid=,command= 2>/dev/null | while IFS= read -r line; do
		local pid cmd
		pid="${line%% *}"
		cmd="${line#"$pid"}"
		cmd="${cmd# }"
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		[ "$pid" = "$$" ] && continue
		printf '%s' "$cmd" | grep -F -- "$user_data_dir" >/dev/null 2>&1 || \
			printf '%s' "$cmd" | grep -F -- "--remote-debugging-port=${cdp_port}" >/dev/null 2>&1 || continue
		printf '%s' "$cmd" | grep -Eq 'Google Chrome for Testing|Chromium|chrome-mac-arm64' || continue
		printf '%s\n' "$pid"
	done | awk '!seen[$0]++'
}

_soren91_stop_standalone_browser() {
	[ "${SOREN91_SHARED_BROWSER:-0}" = "1" ] && return 0
	local pid stopped=0
	while IFS= read -r pid; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		if _soren91_pid_is_alive "$pid"; then
			log "[SOREN91] Cleaning stale standalone Chrome for Testing (PID=$pid)"
			_stop_loop_descendants "$pid"
			_stop_pid_with_fallback "$pid" "soren91_standalone_browser"
			stopped=1
		fi
	done <<EOF
$(_soren91_scan_standalone_browser_pids 2>/dev/null || true)
EOF
	[ "$stopped" -eq 1 ] && log "[SOREN91] Standalone Chrome cleanup complete"
	return 0
}

_soren91_enabled() {
	[ "${SOREN91_ENABLED:-0}" = "1" ]
}

_scheduled_meriken_time_enabled() {
	[ "${MERIKEN_SCHEDULED_TIME_ENABLED:-1}" = "1" ]
}

_clear_soren91_mode_flag() {
	rm -f "$SOREN91_MODE_FLAG_FILE" 2>/dev/null || true
}

_set_soren91_mode_flag() {
	mkdir -p "$(dirname "$SOREN91_MODE_FLAG_FILE")" 2>/dev/null || true
	touch "$SOREN91_MODE_FLAG_FILE" 2>/dev/null || true
}

_soren91_scan_alive_runner_pids() {
	local pid="" ppid="" cmd=""
	pid=$(tmux display-message -p -t soren91_runner '#{pane_pid}' 2>/dev/null || true)
	case "$pid" in
	''|*[!0-9]*) ;;
	*)
		if _soren91_pid_is_alive "$pid"; then
			printf '%s\n' "$pid"
		fi
		;;
	esac
	pid=$(sed -n 's/^pid=//p' "$SOREN91_DIR/tmp/.runner.lock/owner" 2>/dev/null | head -n 1)
	case "$pid" in
	''|*[!0-9]*) ;;
	*)
		if _soren91_pid_is_alive "$pid"; then
			printf '%s\n' "$pid"
		fi
		;;
	esac
	ps -Ao pid=,ppid=,command= 2>/dev/null | while read -r pid ppid cmd; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		[ "$pid" = "$$" ] && continue
		_soren91_pid_is_alive "$pid" || continue
		printf '%s' "$cmd" | grep -F -- "$SOREN91_RUNNER_SCRIPT" >/dev/null 2>&1 || \
			printf '%s' "$cmd" | grep -F -- "$SOREN91_DIR/run_player_loop.sh" >/dev/null 2>&1 || \
			printf '%s' "$cmd" | grep -F -- "soren91/run_player_loop.sh" >/dev/null 2>&1 || continue
		printf '%s\n' "$pid"
	done | awk '!seen[$0]++'
}

_soren91_scan_log_writer_pids() {
	local log_file="$SOREN91_DIR/tmp/soren91.log"
	[ -f "$log_file" ] || return 0
	lsof -nP "$log_file" 2>/dev/null | awk '
		NR > 1 && $1 == "node" && $2 ~ /^[0-9]+$/ { print $2 }
	' | awk '!seen[$0]++'
}

_soren91_clear_stale_runner_lock() {
	local lock_dir="$SOREN91_DIR/tmp/.runner.lock"
	local owner=""
	[ -d "$lock_dir" ] || return 0
	owner=$(sed -n 's/^pid=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
	case "$owner" in
	''|*[!0-9]*) ;;
	*)
		_soren91_pid_is_alive "$owner" && return 0
		;;
	esac
	rm -rf "$lock_dir" 2>/dev/null || true
}

_soren91_scan_alive_main_pids() {
	local runner_pids="" pid="" ppid="" cmd="" runner_pid=""
	_soren91_scan_log_writer_pids 2>/dev/null
	runner_pids="$(_soren91_scan_alive_runner_pids 2>/dev/null | tr '\n' ' ')"
	[ -n "$runner_pids" ] || return 0
	ps -Ao pid=,ppid=,command= 2>/dev/null | while read -r pid ppid cmd; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		[ "$pid" = "$$" ] && continue
		_soren91_pid_is_alive "$pid" || continue
		printf '%s' "$cmd" | grep -Eq '(^|[ /])node([[:space:]].*)?main\.mjs([[:space:]]|$)' || continue
		for runner_pid in $runner_pids; do
			[ "$ppid" = "$runner_pid" ] || continue
			printf '%s\n' "$pid"
			break
		done
	done | awk '!seen[$0]++'
}

_soren91_read_alive_player_pid() {
	local pid="" f="" cmd=""
	for f in "$SOREN91_MAIN_PID_FILE" "$SOREN91_PID_FILE"; do
		[ -f "$f" ] || continue
		pid=$(cat "$f" 2>/dev/null)
		case "$pid" in ''|*[!0-9]*) pid="" ;; esac
		[ -n "$pid" ] || continue
		_soren91_pid_is_alive "$pid" || continue
		cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
		if [ -z "$cmd" ]; then
			printf '%s' "$pid"
			return 0
		fi
		if [ "$f" = "$SOREN91_MAIN_PID_FILE" ]; then
			echo "$cmd" | grep -q "main\.mjs" || continue
		else
			echo "$cmd" | grep -q "run_player_loop\.sh" || continue
		fi
		printf '%s' "$pid"
		return 0
	done

	pid=$(sed -n 's/^pid=//p' "$SOREN91_DIR/tmp/.runner.lock/owner" 2>/dev/null | head -n 1)
	case "$pid" in ''|*[!0-9]*) pid="" ;; esac
	if [ -n "$pid" ] && _soren91_pid_is_alive "$pid"; then
		printf '%s' "$pid"
		return 0
	fi

	# PIDファイルは停止処理の途中で消えることがある。実プロセスが残っていると
	# "Not running" と誤判定して stop file を出せないため、実プレイヤーをプロセス表から復旧する。
	pid=$(_soren91_scan_alive_main_pids | head -n 1)
	if [ -n "$pid" ]; then
		printf '%s' "$pid"
		return 0
	fi
	pid=$(_soren91_scan_log_writer_pids | head -n 1)
	if [ -n "$pid" ]; then
		printf '%s' "$pid"
		return 0
	fi
	pid=$(_soren91_scan_alive_runner_pids | head -n 1)
	if [ -n "$pid" ]; then
		printf '%s' "$pid"
		return 0
	fi
	return 1
}

_soren91_observable_fresh() {
	local now="" log_mtime="" log_age="" mode_mtime="" mode_age=""
	now=$(date +%s)
	log_mtime=$(stat -f '%m' "$SOREN91_DIR/tmp/soren91.log" 2>/dev/null || echo 0)
	case "$log_mtime" in ''|*[!0-9]*) log_mtime=0 ;; esac
	log_age=$((now - log_mtime))
	if [ "$log_mtime" -gt 0 ] && [ "$log_age" -le "${SOREN91_OBSERVABLE_FRESH_SEC:-120}" ] && [ -f "$SOREN91_DIR/tmp/in_game" ]; then
		return 0
	fi

	mode_mtime=$(stat -f '%m' "$SOREN91_MODE_FLAG_FILE" 2>/dev/null || echo 0)
	case "$mode_mtime" in ''|*[!0-9]*) mode_mtime=0 ;; esac
	mode_age=$((now - mode_mtime))
	if [ "$log_mtime" -gt 0 ] && [ "$log_age" -le "${SOREN91_OBSERVABLE_FRESH_SEC:-120}" ] && [ "$mode_mtime" -gt 0 ] && [ "$mode_age" -le "${SOREN91_OBSERVABLE_FRESH_SEC:-120}" ]; then
		return 0
	fi

	return 1
}

_soren91_has_runtime_marker() {
	[ -f "$SOREN91_PID_FILE" ] && return 0
	[ -f "$SOREN91_MAIN_PID_FILE" ] && return 0
	[ -d "$SOREN91_DIR/tmp/.runner.lock" ] && return 0
	command -v tmux >/dev/null 2>&1 && tmux has-session -t soren91_runner 2>/dev/null && return 0
	return 1
}

_soren91_recovered_player_stale() {
	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)
	[ -n "$pid" ] || return 1
	_soren91_observable_fresh && return 1
	_soren91_has_runtime_marker && return 1
	printf '%s' "$pid"
	return 0
}

_soren91_force_stop_recovered_player() {
	local stale_pid="${1:-}" pid="" player_pids=""
	player_pids="$stale_pid"
	player_pids="$player_pids $(_soren91_scan_alive_main_pids 2>/dev/null | tr '\n' ' ')"
	player_pids="$player_pids $(_soren91_scan_log_writer_pids 2>/dev/null | tr '\n' ' ')"
	player_pids="$player_pids $(_soren91_scan_alive_runner_pids 2>/dev/null | tr '\n' ' ')"
	for pid in $player_pids; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		_soren91_pid_is_alive "$pid" || continue
		log "[SOREN91] Force stopping stale recovered player PID=$pid"
		_stop_loop_descendants "$pid"
		_stop_pid_with_fallback "$pid" "soren91_stale_recovered"
	done
	_soren91_kill_runner_session
}

_write_manual_meriken_mode_state() {
	local enabled="$1" note="${2:-}"
	mkdir -p "$(dirname "$MANUAL_MERIKEN_MODE_FILE")" 2>/dev/null || true
	python3 - "$MANUAL_MERIKEN_MODE_FILE" "$enabled" "$note" <<'PY' >/dev/null 2>&1
import json
import sys
from datetime import datetime, timezone

out_file, enabled_raw, note = sys.argv[1:4]
enabled = enabled_raw == "1"
payload = {
    "enabled": enabled,
    "note": note,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
}

manual_meriken_mode_is_enabled() {
	[ -f "$MANUAL_MERIKEN_MODE_FILE" ] || return 1
	python3 - "$MANUAL_MERIKEN_MODE_FILE" <<'PY' >/dev/null 2>&1
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if data.get("enabled") else 1)
PY
}

manual_meriken_mode_enable() {
	_soren91_enabled || return 0
	_write_manual_meriken_mode_state "1" "manual_override"
	log "[SOREN91] manual_meriken_mode=on"
	soren91_start
}

manual_meriken_mode_disable() {
	_soren91_enabled || return 0
	if [ -f "$MANUAL_MERIKEN_MODE_FILE" ]; then
		rm -f "$MANUAL_MERIKEN_MODE_FILE"
	fi
	log "[SOREN91] manual_meriken_mode=off"
	if command -v _is_improve_running >/dev/null 2>&1 && _is_improve_running; then
		log "[SOREN91] 改善中のため、メリケンAIは継続"
		return 0
	fi
	soren91_stop
}

manual_meriken_mode_status() {
	if manual_meriken_mode_is_enabled; then
		printf 'on'
	else
		printf 'off'
	fi
}

_meriken_time_slot_end_epoch() {
	python3 - "$MERIKEN_TIME_END_HOUR" <<'PY' 2>/dev/null
import sys
from datetime import datetime

try:
    end_hour = int(sys.argv[1])
except Exception:
    raise SystemExit(1)

now = datetime.now().astimezone()
end_dt = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
if now >= end_dt:
    print(0)
else:
    print(int(end_dt.timestamp()))
PY
}

_write_meriken_time_state() {
	local end_epoch="$1" reason="${2:-scheduled}"
	mkdir -p "$(dirname "$MERIKEN_TIME_STATE_FILE")" 2>/dev/null || true
	python3 - "$MERIKEN_TIME_STATE_FILE" "$end_epoch" "$reason" <<'PY' >/dev/null 2>&1
import json
import sys
from datetime import datetime, timezone

out_file, end_epoch_raw, reason = sys.argv[1:4]
try:
    end_epoch = int(end_epoch_raw)
except Exception:
    raise SystemExit(1)
payload = {
    "active": True,
    "reason": reason,
    "end_epoch": end_epoch,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
}

_clear_meriken_time_state() {
	rm -f "$MERIKEN_TIME_STATE_FILE" 2>/dev/null || true
}

_meriken_time_state_end_epoch() {
	[ -f "$MERIKEN_TIME_STATE_FILE" ] || return 1
	python3 - "$MERIKEN_TIME_STATE_FILE" <<'PY' 2>/dev/null
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    print(int(data.get("end_epoch", 0) or 0))
except Exception:
    raise SystemExit(1)
PY
}

scheduled_meriken_time_is_active() {
	if ! _scheduled_meriken_time_enabled; then
		_clear_meriken_time_state
		return 1
	fi
	local end_epoch=0
	end_epoch=$(_meriken_time_state_end_epoch 2>/dev/null || echo 0)
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	if [ "${end_epoch:-0}" -le 0 ]; then
		_clear_meriken_time_state
		return 1
	fi
	if [ "$(date +%s)" -lt "$end_epoch" ]; then
		return 0
	fi
	_clear_meriken_time_state
	return 1
}

scheduled_meriken_time_begin() {
	_scheduled_meriken_time_enabled || return 1
	local reason="${1:-scheduled}"
	local end_epoch=0
	end_epoch=$(_meriken_time_slot_end_epoch 2>/dev/null || echo 0)
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	if [ "${end_epoch:-0}" -le "$(date +%s)" ]; then
		return 1
	fi
	_write_meriken_time_state "$end_epoch" "$reason" || return 1
	printf '%s' "$end_epoch"
}

scheduled_meriken_time_end_label() {
	local end_epoch="${1:-0}"
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	[ "$end_epoch" -gt 0 ] || return 1
	python3 - "$end_epoch" <<'PY' 2>/dev/null
import sys
from datetime import datetime

try:
    end_epoch = int(sys.argv[1])
except Exception:
    raise SystemExit(1)
print(datetime.fromtimestamp(end_epoch).astimezone().strftime('%H:%M %Z'))
PY
}

soren91_is_running() {
	_soren91_enabled || return 1
	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)
	if [ -z "$pid" ]; then
		_soren91_observable_fresh || return 1
		return 0
	fi
	if ! _soren91_observable_fresh && ! _soren91_has_runtime_marker; then
		return 1
	fi
	# pid file は soren91 起動用の bash subshell を指すことがあり、
	# 実行環境によっては ps でそのコマンドラインを安定取得できない。
	# start/stop で専用 PID ファイルを管理しているため、生存中なら稼働中とみなす。
	return 0
}

_soren91_stop_in_progress() {
	[ -f "$SOREN91_STOPPING_FILE" ] || return 1

	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)

	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		return 0
	fi
	if [ -f "$SOREN91_STOP_FILE" ] || [ -f "$SOREN91_DIR/tmp/in_game" ]; then
		return 0
	fi

	rm -f "$SOREN91_STOPPING_FILE" 2>/dev/null || true
	return 1
}

_soren91_is_improve_process() {
	# PIDが soren91 improve プロセスかどうか確認
	local pid="$1"
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	kill -0 "$pid" 2>/dev/null || return 1
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	echo "$cmd" | grep -q "improve.mjs" && return 0
	return 1
}

_soren91_record_improve_stale_cleanup() {
	local reason="$1" pid="${2:-}" lock_age="${3:-0}" cmd="${4:-}" session_range="${5:-unknown}" out_file log_file
	out_file="${SOREN91_IMPROVE_HUNG_QUARANTINE_FILE:-$TMP_STATE_DIR/soren91_improve_hung_quarantine.jsonl}"
	log_file="$SOREN91_DIR/tmp/soren91_improve.log"
	mkdir -p "$(dirname "$out_file")" 2>/dev/null || true
	python3 - "$out_file" "$reason" "$pid" "$lock_age" "$cmd" "$session_range" "$log_file" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

out, reason, pid, lock_age, cmd, session_range, log_file = sys.argv[1:8]
try:
    pid_value = int(pid)
except Exception:
    pid_value = None
try:
    lock_age_value = int(lock_age)
except Exception:
    lock_age_value = 0
tail = []
try:
    if log_file and os.path.exists(log_file):
        with open(log_file, encoding="utf-8", errors="replace") as f:
            tail = [line.rstrip("\n") for line in f.readlines()[-24:]]
except Exception:
    tail = []
row = {
    "epoch": int(time.time()),
    "event": "soren91_improve_stale_cleanup",
    "reason": reason,
    "pid": pid_value,
    "raw_pid": pid,
    "lock_age": lock_age_value,
    "session_range": session_range,
    "command": cmd,
    "log_tail": tail,
}
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
}

soren91_harvest_hung_improve() {
	_soren91_enabled || return 0
	[ "${SOREN91_IMPROVE_HUNG_HARVEST_ENABLED:-1}" = "1" ] || return 0
	[ -f "$SOREN91_IMPROVE_LOCK" ] || return 0
	[ -f "$SOREN91_IMPROVE_PID_FILE" ] || return 0

	local pid threshold now lock_mtime lock_age log_file log_mtime log_age eval_age eval_mtime cmd session_range
	pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null || true)
	now=$(date +%s)
	lock_mtime=$(stat -f '%m' "$SOREN91_IMPROVE_LOCK" 2>/dev/null || echo 0)
	lock_age=$((now - ${lock_mtime:-0}))
	case "$pid" in
	''|*[!0-9]*)
		log "[SOREN91] stale improve lock: invalid pid='${pid:-}' → cleanup"
		_soren91_record_improve_stale_cleanup "invalid_pid" "${pid:-}" "$lock_age" "" "unknown"
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
		return 0
		;;
	esac
	if ! _soren91_is_improve_process "$pid"; then
		cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
		log "[SOREN91] stale improve lock: pid=$pid not alive/improve → cleanup"
		_soren91_record_improve_stale_cleanup "pid_not_alive_or_not_improve" "$pid" "$lock_age" "$cmd" "unknown"
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
		enqueue_audio_text "メリケンAI改善が途中終了したため、ロックを回収して通常運転を継続します。" "soren91_improve_stale_cleanup" "${SOREN91_VOICEVOX_SPEAKER:-46}" || true
		return 0
	fi

	threshold="${SOREN91_IMPROVE_HUNG_SEC:-900}"
	case "$threshold" in ''|*[!0-9]*) threshold=900 ;; esac
	[ "$threshold" -gt 0 ] || return 0
	log_file="$SOREN91_DIR/tmp/soren91_improve.log"
	log_age="$lock_age"
	if [ -f "$log_file" ]; then
		log_mtime=$(stat -f '%m' "$log_file" 2>/dev/null || echo 0)
		if [ "${log_mtime:-0}" -gt 0 ]; then
			log_age=$((now - log_mtime))
		fi
	fi
	eval_age="$lock_age"
	if [ -f "${EVAL_SCORE_HISTORY_FILE:-eval_score_history.txt}" ]; then
		eval_mtime=$(stat -f '%m' "${EVAL_SCORE_HISTORY_FILE:-eval_score_history.txt}" 2>/dev/null || echo 0)
		if [ "${eval_mtime:-0}" -gt 0 ]; then
			eval_age=$((now - eval_mtime))
		fi
	fi
	[ "$lock_age" -ge "$threshold" ] || return 0
	[ "$log_age" -ge "$threshold" ] || return 0
	if [ "${IMPROVE_HUNG_REQUIRE_EVAL_STALE:-1}" = "1" ] && [ "$eval_age" -lt "$threshold" ]; then
		log "[SOREN91] hung improve harvest defer: lock/log stale but eval_score_history is moving (${eval_age}s < ${threshold}s, pid=$pid)"
		return 0
	fi

	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	session_range=$(python3 - "$SOREN91_SESSION_FILE" <<'PY' 2>/dev/null || true
import json
import sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    print(f"{int(data.get('start_game', 0) or 0)}-{int(data.get('end_game', 0) or 0)}")
except Exception:
    print("unknown")
PY
)
	log "[SOREN91] hung improve harvest: pid=$pid lock_age=${lock_age}s log_age=${log_age}s eval_age=${eval_age}s threshold=${threshold}s session=${session_range:-unknown}"
	mkdir -p "$(dirname "${SOREN91_IMPROVE_HUNG_QUARANTINE_FILE:-$TMP_STATE_DIR/soren91_improve_hung_quarantine.jsonl}")" 2>/dev/null || true
	python3 - "$SOREN91_IMPROVE_HUNG_QUARANTINE_FILE" "$pid" "$lock_age" "$log_age" "$eval_age" "$threshold" "${session_range:-unknown}" "$cmd" <<'PY' 2>/dev/null || true
import json
import sys
import time

out, pid, lock_age, log_age, eval_age, threshold, session_range, cmd = sys.argv[1:9]
row = {
    "epoch": int(time.time()),
    "event": "soren91_improve_hung_harvest",
    "pid": int(pid),
    "lock_age": int(lock_age),
    "log_age": int(log_age),
    "eval_age": int(eval_age),
    "threshold": int(threshold),
    "session_range": session_range,
    "command": cmd,
}
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
	_stop_loop_descendants "$pid"
	_stop_pid_with_fallback "$pid" "soren91_improve_hung"
	rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	enqueue_audio_text "メリケンAI改善が無音で固まったため、改善プロセスを回収して中華AIの進行を優先します。" "soren91_improve_hung" "${SOREN91_VOICEVOX_SPEAKER:-46}" || true
	return 0
}

_soren91_text_has_japanese() {
	printf '%s' "$1" | grep -q '[ぁ-んァ-ヶ一-龠々ー]'
}

_soren91_normalize_spoken_uppercase() {
	printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

_soren91_text_is_meta_failure() {
	local text
	text=$(printf '%s' "$1" | tr '\r\n' '  ' | sed 's/[[:space:]]\+/ /g')
	[ -n "$text" ] || return 0
	printf '%s' "$text" | grep -Eiq '申し訳(ありません|ございません|ない).*(エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)' && return 0
	printf '%s' "$text" | grep -Eiq '(エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(提供されてい|見当たりません|ありません|ない|不足)' && return 0
	printf '%s' "$text" | grep -Eiq '(入力|依頼|プロンプト|コンテキスト|戦略ヘッダー|本文).*(提供されてい|与えられてい|見当たりません|ありません|ない|不足)' && return 0
	printf '%s' "$text" | grep -Eiq '(エラー|エラーメッセージ).*(詳細|内容|原因|情報).*(提供されてい|見当たりません|ありません|ない|不足|不明)' && return 0
	printf '%s' "$text" | grep -Eiq '(ツール|権限|許可|WebFetch|検索|外部アクセス).*(確認|必要|できません|ありません|ない)' && return 0
	printf '%s' "$text" | grep -Eiq '(何も言えません|語ることはできません|控えておくべき|確認させてください|どうすればよい|何を.*すれば)' && return 0
	printf '%s' "$text" | grep -Eiq '(テキスト|文章|説明|解説).*(生成|作成).*(失敗|できません|できない|無理)' && return 0
	printf '%s' "$text" | grep -Eiq '(戦略|strategy).*(説明|解説).*(できません|できない|無理)' && return 0
	printf '%s' "$text" | grep -Eiq '(日本語|話し言葉).*(直す|言い換え|変換).*(できません|できない|無理|失敗)' && return 0
	return 1
}

_soren91_fallback_strategy_explanation() {
	local strategy_header="${1:-}"
	local details=""
	if printf '%s' "$strategy_header" | grep -Eiq '高さ|height|deadline|デッドライン|高積み'; then
		details="${details}高く積み上がる前に置き場所を絞り、"
	fi
	if printf '%s' "$strategy_header" | grep -Eiq '先読み|look.?ahead|next|次'; then
		details="${details}次のピースまで見て、"
	fi
	if printf '%s' "$strategy_header" | grep -Eiq 'merge|併合|chain|pipeline|パイプライン'; then
		details="${details}併合の流れを残しながら、"
	fi
	if [ -z "$details" ]; then
		details="置き場所を慎重に選び、"
	fi
	printf '%s\n' "今のメリケンAIは、${details}盤面を薄く保つ方針です。派手な勝負より生存率を買う、いかにも資本主義らしい臆病な投資判断ですね。まあ、その臆病さで最後まで残れば勝ちです。"
}

_soren91_provider_error_preview() {
	printf '%s' "$1" | tr '\r\n' '  ' | sed 's/[[:space:]]\+/ /g' | cut -c1-160
}

_soren91_text_generation_debug_file() {
	local tag="$1"
	local debug_dir="$ELOOP_LIB_DIR/tmp/debug/soren91_strategy_explanation"
	local safe_tag
	safe_tag=$(printf '%s' "$tag" | tr -c '[:alnum:]_-' '_')
	mkdir -p "$debug_dir" 2>/dev/null || return 0
	printf '%s/%s_%s_error.txt' "$debug_dir" "$(date +%Y%m%d_%H%M%S)" "$safe_tag"
}

_soren91_generate_text_with_shared_fallback() {
	local tag="$1" prompt="$2" fallback_mode="${3:-${SOREN91_TEXT_FALLBACKS:-claude}}"
	local prompt_file="" err_file="" debug_file="" result="" err_preview=""

	command -v node >/dev/null 2>&1 || return 1
	prompt_file=$(mktemp /tmp/soren91_ai_prompt.XXXXXX) || return 1
	err_file=$(mktemp /tmp/soren91_ai_err.XXXXXX) || {
		rm -f "$prompt_file"
		return 1
	}
	printf '%s' "$prompt" >"$prompt_file"
	debug_file=$(_soren91_text_generation_debug_file "$tag")

	if result=$(SOREN91_TEXT_CLAUDE_TIMEOUT="${SOREN91_TEXT_CLAUDE_TIMEOUT:-60}" node "$SOREN91_DIR/text_ai.mjs" --tag "$tag" --prompt-file "$prompt_file" --fallbacks "$fallback_mode" 2>"$err_file"); then
		rm -f "$prompt_file" "$err_file"
		rm -f "$debug_file" 2>/dev/null || true
		printf '%s' "$result"
		return 0
	fi

	err_preview=$(_soren91_provider_error_preview "$(cat "$err_file" 2>/dev/null || true)")
	if [ -n "$debug_file" ]; then
		{
			printf 'tag=%s\n' "$tag"
			printf 'fallbacks=%s\n' "$fallback_mode"
			printf 'time=%s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
			cat "$err_file" 2>/dev/null || true
		} >"$debug_file" 2>/dev/null || true
	fi
	rm -f "$prompt_file" "$err_file"
	[ -n "$err_preview" ] && log "[SOREN91] ${tag}: text generation failed (${err_preview})" >&2
	return 1
}

_soren91_dump_strategy_explanation_debug() {
	local phase="$1" text="$2"
	local debug_dir="$ELOOP_LIB_DIR/tmp/debug/soren91_strategy_explanation"
	local debug_file
	mkdir -p "$debug_dir" 2>/dev/null || return 0
	debug_file="$debug_dir/$(date +%Y%m%d_%H%M%S)_${phase}.txt"
	{
		printf 'phase=%s\n' "$phase"
		printf 'time=%s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf '%s\n' "$text"
	} >"$debug_file" 2>/dev/null || true
}

_soren91_start_capitalism_corner() {
	[ "${SOREN91_CAPITALISM_CORNER_ENABLED:-1}" = "1" ] || return 0
	command -v start_radio_corner_capitalism >/dev/null 2>&1 || return 0

	local radio_game_num="${GAME_NUM:-}"
	case "$radio_game_num" in
	''|*[!0-9]*) radio_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0) ;;
	esac
	case "$radio_game_num" in
	''|*[!0-9]*) radio_game_num=0 ;;
	esac

	local radio_score=""
	if command -v _last_score >/dev/null 2>&1; then
		radio_score=$(_last_score 2>/dev/null || true)
	fi
	case "$radio_score" in
	''|*[!0-9]*) radio_score=0 ;;
	esac

	log "[SOREN91] 資本主義ネタコーナー開始 (game=${radio_game_num}, score=${radio_score})"
	start_radio_corner_capitalism "$radio_game_num" "$radio_score"
}

_soren91_generate_strategy_explanation() {
	local strategy_header="$1"
	[ -n "$strategy_header" ] || return 1
	local prompt_file="$SOREN91_DIR/prompts/explain_strategy.md"
	if [ ! -f "$prompt_file" ]; then
		log "[SOREN91] Warning: prompt file not found: $prompt_file"
		return 1
	fi
	local prompt_text
	prompt_text=$(cat "$prompt_file")
	prompt_text="${prompt_text//\{\{STRATEGY_HEADER\}\}/$strategy_header}"
	_soren91_generate_text_with_shared_fallback "strategy_explanation" "$prompt_text" "${SOREN91_TEXT_FALLBACKS:-claude}"
}

_soren91_rewrite_strategy_explanation_to_japanese() {
	local raw_text="$1"
	[ -n "$raw_text" ] || return 1
	local prompt_file="$SOREN91_DIR/prompts/explain_strategy_japanese.md"
	if [ ! -f "$prompt_file" ]; then
		log "[SOREN91] Warning: prompt file not found: $prompt_file"
		return 1
	fi
	local prompt_text
	prompt_text=$(cat "$prompt_file")
	prompt_text="${prompt_text//\{\{STRATEGY_EXPLANATION\}\}/$raw_text}"
	_soren91_generate_text_with_shared_fallback "strategy_explanation_rewrite" "$prompt_text" "${SOREN91_TEXT_FALLBACKS:-claude}"
}

soren91_start() {
	_soren91_enabled || return 0
	local stale_pid=""
	stale_pid=$(_soren91_recovered_player_stale 2>/dev/null || true)
	if [ -n "$stale_pid" ]; then
		log "[SOREN91] stale recovered player detected (PID=$stale_pid, no runtime marker/fresh log) → cleanup before start"
		soren91_cleanup || true
		_soren91_force_stop_recovered_player "$stale_pid"
	fi
	if soren91_is_running; then
		log "[SOREN91] Already running, skip start"
		return 0
	fi
	if _soren91_stop_in_progress; then
		log "[SOREN91] Stop in progress, skip start"
		return 0
	fi

	log "[SOREN91] Starting soren91 (メリケンAI)..."
	rm -f "$SOREN91_LAST_ACTIVATE_STATE_FILE"
	rm -f "$SOREN91_STOP_FILE" "$SOREN91_MAIN_PID_FILE" "$TMP_STATE_DIR/.soren91_bye_sent"
	mkdir -p "$SOREN91_DIR/tmp" 2>/dev/null || true
	_soren91_stop_standalone_browser

	# 前回の soren91 improve がまだ実行中なら session_games.json を上書きしない
	if [ -f "$SOREN91_IMPROVE_LOCK" ] && [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local prev_imp_pid
		prev_imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		if _soren91_is_improve_process "$prev_imp_pid"; then
			log "[SOREN91] Previous improve still running (PID=$prev_imp_pid), keeping session_games.json"
		fi
	fi

	# セッション開始時のゲーム番号を記録
	local start_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		start_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	printf '{"start_game":%d,"start_time":"%s"}\n' "$start_game" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		> "$SOREN91_SESSION_FILE"

	# メリケンAIモード判定 (手動発火 or 定時メリケン枠の継続中)
	# メリケンモードでは内部改善を有効化 (12ゲームごと、env override可)
	local _meriken_mode=0
	if manual_meriken_mode_is_enabled; then
		_meriken_mode=1
	elif scheduled_meriken_time_is_active; then
		_meriken_mode=1
	elif _scheduled_meriken_time_enabled && [ "$(date +%H)" = "$MERIKEN_TIME_START_HOUR" ]; then
		# 定時メリケン枠の開始時刻に起動されたセッションはメリケンモード扱いにする。
		# 継続判定は state file の end_epoch を優先する。
		_meriken_mode=1
	fi

	local _ext_improve=1
	local _improve_interval=""
	if [ "$_meriken_mode" -eq 1 ]; then
		_ext_improve=0
		_improve_interval="$SOREN91_MERIKEN_IMPROVE_INTERVAL"
		log "[SOREN91] メリケンAIモード: 内部改善有効 (${_improve_interval}ゲームごと)"
	fi

	# 再試行付きランナーを完全 detach 起動。
	# Playwright + 共有ChromeはTTYなしnohupで起動するとタイトル直後に消えることがあるため、
	# tmux が使える環境では専用セッションでTTYを保持する。
	local pid=""
	if command -v tmux >/dev/null 2>&1; then
		tmux has-session -t soren91_runner 2>/dev/null && tmux kill-session -t soren91_runner 2>/dev/null || true
		tmux new-session -d -s soren91_runner \
			"cd '$SOREN91_DIR' && export SOREN91_SHARED_BROWSER='${SOREN91_SHARED_BROWSER:-1}' SOREN91_AUDIO_GAIN_MULTIPLIER='${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}' SOREN91_EXTERNAL_IMPROVE='$_ext_improve' IMPROVEMENT_INTERVAL_GAMES='${_improve_interval:-}' && exec /bin/bash '$SOREN91_RUNNER_SCRIPT'" \
			>/dev/null 2>&1 || true
		pid=$(tmux display-message -p -t soren91_runner '#{pane_pid}' 2>/dev/null || echo "")
	else
		pid=$(
			cd "$SOREN91_DIR" || exit 1
			SOREN91_SHARED_BROWSER="${SOREN91_SHARED_BROWSER:-1}" \
			SOREN91_AUDIO_GAIN_MULTIPLIER="${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}" \
			SOREN91_EXTERNAL_IMPROVE="$_ext_improve" \
			IMPROVEMENT_INTERVAL_GAMES="${_improve_interval:-}" \
				/usr/bin/nohup /bin/bash "$SOREN91_RUNNER_SCRIPT" </dev/null >/dev/null 2>&1 &
			echo $!
		)
	fi
	case "$pid" in
	''|*[!0-9]*)
		log "[SOREN91] Failed to launch detached runner"
		return 1
		;;
	esac
	echo "$pid" > "$SOREN91_PID_FILE"

	# 5秒後に生存チェック
	sleep 5
	local live_pid_after_start=""
	live_pid_after_start=$(_soren91_read_alive_player_pid 2>/dev/null || true)
	if _soren91_pid_is_alive "$pid" || [ -n "$live_pid_after_start" ]; then
		log "[SOREN91] Started successfully (PID=$pid, live=${live_pid_after_start:-$pid}, start_game=$start_game)"
		# 中華AI側のBGMをミュート（改善中は不要）
		touch "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
		log "[SOREN91] Muted local game BGM (flag file)"
		log "[SOREN91] soren91 browser audio gain=${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}"
		# 読み上げアナウンス + 戦略解説 (バックグラウンド)
		{
			local announce_file
			announce_file=$(mktemp /tmp/eloop_soren91_announce.XXXXXX)
			printf '%s\n' "中華AIが戦略を改善中。その間、メリケンAIがソ連ゲーム91で同志を迎え撃ちます。挑戦お待ちしています" > "$announce_file"
			SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:announce" ./say_enqueue.sh "$announce_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
			rm -f "$announce_file"

			# soren91の現在の戦略を解説
				local strategy_header=""
				strategy_header=$(sed -n '1,/\*\//p' "$SOREN91_DIR/strategy.mjs" 2>/dev/null)
				if [ -n "$strategy_header" ]; then
					local strategy_explain=""
					if strategy_explain=$(_soren91_generate_strategy_explanation "$strategy_header"); then
						_soren91_dump_strategy_explanation_debug "raw" "$strategy_explain"
					else
						_soren91_dump_strategy_explanation_debug "raw_failed" "$strategy_explain"
						log "[SOREN91] 戦略解説の生成に失敗したため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ] && _soren91_text_is_meta_failure "$strategy_explain"; then
						log "[SOREN91] 戦略解説がメタ失敗文のため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ] && ! _soren91_text_has_japanese "$strategy_explain"; then
						local rewritten_strategy_explain=""
						log "[SOREN91] 戦略解説が英語寄りのため日本語へ再生成"
						if rewritten_strategy_explain=$(_soren91_rewrite_strategy_explanation_to_japanese "$strategy_explain"); then
							strategy_explain="$rewritten_strategy_explain"
						else
							log "[SOREN91] 戦略解説の日本語化生成に失敗したため読み上げをスキップ"
							strategy_explain=""
						fi
					fi
					if [ -n "$strategy_explain" ] && _soren91_text_is_meta_failure "$strategy_explain"; then
						log "[SOREN91] 戦略解説の日本語化結果がメタ失敗文のため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ] && ! _soren91_text_has_japanese "$strategy_explain"; then
						log "[SOREN91] 戦略解説の日本語化に失敗したため読み上げをスキップ"
						strategy_explain=""
					fi
					if _soren91_text_is_meta_failure "$strategy_explain"; then
						log "[SOREN91] 戦略解説の最終ガードでメタ失敗文を検出したため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ]; then
						strategy_explain=$(_soren91_normalize_spoken_uppercase "$strategy_explain")
						_soren91_dump_strategy_explanation_debug "final" "$strategy_explain"
						local explain_file
						explain_file=$(mktemp /tmp/eloop_soren91_strategy.XXXXXX)
						printf '%s\n' "$strategy_explain" > "$explain_file"
					SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:strategy" ./say_enqueue.sh "$explain_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
					rm -f "$explain_file"
					log "[SOREN91] 戦略解説を読み上げ"
				fi
			fi
		} &
		_soren91_start_capitalism_corner >/dev/null 2>&1 &
		_set_soren91_mode_flag
		# ダッシュボードHTMLを即時クリア（前ゲームGAMEOVER状態の残骸を消す）
		(cd "$ELOOP_LIB_DIR" && ./generate_dashboard.sh MOVE >/dev/null 2>&1) || true
		_soren91_switch_obs_layout meriken || true
	else
		log "[SOREN91] WARNING: Process died immediately (PID=$pid)"
		rm -f "$SOREN91_PID_FILE"
		return 1
	fi
	return 0
}

_soren91_record_end_game() {
	# セッション終了時のゲーム番号を記録 (stop/早期終了の両方から呼ばれる)
	local end_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		end_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	if [ -f "$SOREN91_SESSION_FILE" ]; then
		python3 -c "
import json, sys
with open('$SOREN91_SESSION_FILE') as f:
    sess = json.load(f)
sess['end_game'] = $end_game
sess['end_time'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open('$SOREN91_SESSION_FILE', 'w') as f:
    json.dump(sess, f)
" 2>/dev/null || true
	fi
	echo "$end_game"
}

# 戦略改善から復帰した直後 (soren91_stop の unmute 後) に soviet_local を
# 再起動する。改善 PAUSE 中に suspend した Unity AudioContext は、復帰時の
# in-page resume() が Chrome の autoplay/visibility gating で安定して効かず
# (実測: unmute 後も state=suspended のまま → ゲーム音が BlackHole に乗らない)。
# sink-at-construction により bridge を作り直せば AudioContext は生成時点で
# running かつ BlackHole バインドになる (実証済) ため、復帰のたびに確実に
# 音声を回復させる。ユーザー指示 (#90 関連) による恒久対応。
# 引数 $1=1 のとき (=直前まで改善mute中だった) のみ実行。
_soren91_restart_bridge_after_improve() {
	[ "${1:-0}" = "1" ] || return 0
	if ! command -v _br_relaunch >/dev/null 2>&1; then
		log "[SOREN91] bridge再起動スキップ (_br_relaunch 未ロード)"
		return 0
	fi
	log "[SOREN91] 改善復帰: soviet_local を再起動し AudioContext を running+BlackHole で作り直す"
	# _br_relaunch は Fix0 lease を取得し他復旧アクターと競合しない。
	# lease 他者保持(=2) や一過性ロックは soviet_watchdog が後追い復旧するので
	# ここでは soren91_stop を失敗させない。
	local retries="${SOREN91_BRIDGE_RESTART_RETRIES:-2}"
	case "$retries" in ''|*[!0-9]*) retries=2 ;; esac
	local attempt=1 rc=1
	while [ "$attempt" -le "$retries" ]; do
		if _br_relaunch; then
			log "[SOREN91] bridge再起動 成功 (改善復帰 attempt=$attempt)"
			return 0
		fi
		rc=$?
		log "[SOREN91] bridge再起動 失敗 (attempt=$attempt/$retries rc=$rc)"
		[ "$attempt" -lt "$retries" ] && sleep "${SOREN91_BRIDGE_RESTART_RETRY_SLEEP:-5}"
		attempt=$((attempt + 1))
	done
	log "[SOREN91] bridge再起動 譲渡/失敗 (rc=$rc → watchdog委譲)"
	return 0
}

soren91_stop() {
	_soren91_enabled || return 0
	touch "$SOREN91_STOPPING_FILE"

	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)

	if [ -z "$pid" ] || ! _soren91_pid_is_alive "$pid"; then
		# プロセスが既に終了 → end_game だけ記録して終了
		log "[SOREN91] Not running, recording end_game"
		local eg
		eg=$(_soren91_record_end_game)
		_soren91_kill_runner_session
		_clear_meriken_time_state
		_clear_soren91_mode_flag
		rm -f "$SOREN91_PID_FILE" "$SOREN91_MAIN_PID_FILE" "$SOREN91_STOP_FILE" "$SOREN91_STOPPING_FILE" "$SOREN91_DIR/tmp/in_game"
		_soren91_clear_stale_runner_lock
		local _had_mute=0; [ -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm" ] && _had_mute=1
		rm -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
		_soren91_close_shared_game_tabs
		_soren91_stop_standalone_browser
		_soren91_switch_obs_layout china || true
		log "[SOREN91] Unmuted local game BGM (flag file removed)"
		_soren91_restart_bridge_after_improve "$_had_mute"
		log "[SOREN91] Stopped (already exited, end_game=$eg)"
		return 0
	fi

	log "[SOREN91] Stopping soren91 (PID=$pid)..."

	# graceful stop: stop ファイルを作成して現在のゲーム終了を待つ
	touch "$SOREN91_STOP_FILE"

	local in_game_file="$SOREN91_DIR/tmp/in_game"

	# Phase 1: 試合中なら試合終了を待つ。長すぎる居残りを避けるため、
	# 固定600秒ではなく SOREN91_STOP_TIMEOUT に従う。
	local game_waited=0
	local max_game_wait="${SOREN91_STOP_TIMEOUT:-300}"
	case "$max_game_wait" in
	''|*[!0-9]*) max_game_wait=300 ;;
	esac
	while [ -f "$in_game_file" ] && _soren91_pid_is_alive "$pid" && [ "$game_waited" -lt "$max_game_wait" ]; do
		log "[SOREN91] Game in progress, waiting for round to end... (${game_waited}s/${max_game_wait}s)"
		sleep 5
		game_waited=$((game_waited + 5))
	done

	# Phase 2: 試合終了後、graceful exit を短時間待つ
	local waited=0
	local post_game_timeout=30
	while [ "$waited" -lt "$post_game_timeout" ]; do
		if ! _soren91_pid_is_alive "$pid"; then
			log "[SOREN91] Stopped gracefully after game ended"
			break
		fi
		sleep 2
		waited=$((waited + 2))
	done

	# Phase 3: それでも生きていたら強制停止 (従来通り)
	if _soren91_pid_is_alive "$pid"; then
		log "[SOREN91] Post-game timeout, force stopping..."
		_stop_loop_descendants "$pid"
		_stop_pid_with_fallback "$pid" "soren91"
		_soren91_kill_runner_session
	fi

	# Phase 4: run_player_loop.sh (runner) の確実な終了を待つ
	local runner_pid=""
	[ -f "$SOREN91_PID_FILE" ] && runner_pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
	case "$runner_pid" in ''|*[!0-9]*) runner_pid="" ;; esac
	if [ -n "$runner_pid" ] && _soren91_pid_is_alive "$runner_pid"; then
		local runner_waited=0
		while _soren91_pid_is_alive "$runner_pid" && [ "$runner_waited" -lt 10 ]; do
			sleep 1
			runner_waited=$((runner_waited + 1))
		done
		if _soren91_pid_is_alive "$runner_pid"; then
			log "[SOREN91] Killing stray runner process (PID=$runner_pid)"
			kill "$runner_pid" 2>/dev/null || true
		fi
	fi
	_soren91_kill_runner_session

	local eg
	eg=$(_soren91_record_end_game)

	rm -f "$SOREN91_PID_FILE" "$SOREN91_MAIN_PID_FILE" "$SOREN91_STOP_FILE" "$SOREN91_STOPPING_FILE" "$SOREN91_DIR/tmp/in_game"
	rm -f "$SOREN91_LAST_ACTIVATE_STATE_FILE"
	_soren91_clear_stale_runner_lock
	_clear_meriken_time_state
	_clear_soren91_mode_flag
	# 中華AI側のBGMをアンミュート（改善終了・復帰）
	local _had_mute=0; [ -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm" ] && _had_mute=1
	rm -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
	_soren91_close_shared_game_tabs
	_soren91_stop_standalone_browser
	_soren91_switch_obs_layout china || true
	log "[SOREN91] Unmuted local game BGM (flag file removed)"
	_soren91_restart_bridge_after_improve "$_had_mute"

	# メリケンAI終了あいさつ (TTS + Twitch) — 重複防止
	local _bye_guard="$TMP_STATE_DIR/.soren91_bye_sent"
	if [ ! -f "$_bye_guard" ]; then
		touch "$_bye_guard"
		{
			local _bye_file
			_bye_file=$(mktemp /tmp/eloop_soren91_bye.XXXXXX)
			printf '%s\n' "対戦ありがとうございました。メリケンAIはここで退場しますね、またね！" > "$_bye_file"
			SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:bye" ./say_enqueue.sh "$_bye_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
			rm -f "$_bye_file"
		} &
		enqueue_chat_message "対戦ありがとうございました。メリケンAIはここで退場しますね、またね！" "soren91"
	fi

	log "[SOREN91] Stopped (end_game=$eg)"
	return 0
}

soren91_improve() {
	_soren91_enabled || return 0

	# 直前の改善終了・プロセス再利用で stale lock が残ることがある。
	# 起動判断の前に「本当に improve.mjs か」を確認して、誤って skip しない。
	soren91_harvest_hung_improve || true

	# ロック + PID生存チェック
	if [ -f "$SOREN91_IMPROVE_LOCK" ] && [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if _soren91_is_improve_process "$imp_pid"; then
				log "[SOREN91] Improvement already running (PID=$imp_pid), skip"
				return 0
			fi
			;;
		esac
		# stale lock cleanup
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	fi

	# セッションデータからゲーム範囲を取得
	if [ ! -f "$SOREN91_SESSION_FILE" ]; then
		log "[SOREN91] No session file, skip improve"
		return 0
	fi

	local start_game end_game
	start_game=$(python3 -c "import json; print(json.load(open('$SOREN91_SESSION_FILE')).get('start_game',0))" 2>/dev/null || echo 0)
	end_game=$(python3 -c "import json; print(json.load(open('$SOREN91_SESSION_FILE')).get('end_game',0))" 2>/dev/null || echo 0)

	local games_played=$((end_game - start_game))
	if [ "$games_played" -le 0 ]; then
		log "[SOREN91] No games played in session (start=$start_game, end=$end_game), skip improve"
		return 0
	fi

	log "[SOREN91] Starting improvement for games $start_game-$end_game ($games_played games)..."
	touch "$SOREN91_IMPROVE_LOCK"

	(
		cd "$SOREN91_DIR" && \
		node improve.mjs --standalone "$start_game" "$end_game" \
			>> "$SOREN91_DIR/tmp/soren91_improve.log" 2>&1
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	) &
	local pid=$!
	echo "$pid" > "$SOREN91_IMPROVE_PID_FILE"
	log "[SOREN91] Improvement started (PID=$pid, games=$start_game-$end_game)"
	return 0
}

soren91_cleanup() {
	_soren91_enabled || return 0

	# プレイヤープロセス停止 (コマンド名を検証して誤kill防止)
	local player_pids="" pid=""
	if [ -f "$SOREN91_PID_FILE" ]; then
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in
		''|*[!0-9]*) ;;
		*) player_pids="$player_pids $pid" ;;
		esac
	fi
	player_pids="$player_pids $(_soren91_scan_alive_main_pids 2>/dev/null | tr '\n' ' ')"
	player_pids="$player_pids $(_soren91_scan_log_writer_pids 2>/dev/null | tr '\n' ' ')"
	player_pids="$player_pids $(_soren91_scan_alive_runner_pids 2>/dev/null | tr '\n' ' ')"
	for pid in $player_pids; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		if _soren91_pid_is_alive "$pid"; then
			local cmd
			cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
			if [ -z "$cmd" ] || echo "$cmd" | grep -Eq 'main\.mjs|run_player_loop\.sh|soren_loop\.sh'; then
				log "[SOREN91] Cleanup: stopping player (PID=$pid)"
				_stop_loop_descendants "$pid"
				_stop_pid_with_fallback "$pid" "soren91_player"
			else
				log "[SOREN91] Cleanup: PID=$pid is not soren91 player ($cmd), skipping"
			fi
		fi
	done
	_soren91_kill_runner_session

	# 改善プロセス停止 (コマンド名を検証)
	if [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$imp_pid" 2>/dev/null; then
				local cmd
				cmd=$(ps -p "$imp_pid" -o command= 2>/dev/null || echo "")
				if echo "$cmd" | grep -q "improve.mjs"; then
					log "[SOREN91] Cleanup: stopping improve (PID=$imp_pid)"
					_stop_loop_descendants "$imp_pid"
					_stop_pid_with_fallback "$imp_pid" "soren91_improve"
				else
					log "[SOREN91] Cleanup: PID=$imp_pid is not soren91 improve ($cmd), skipping"
				fi
			fi
			;;
		esac
	fi

	# ファイルクリーンアップ
	rm -f "$SOREN91_PID_FILE" "$SOREN91_IMPROVE_PID_FILE" \
		"$SOREN91_IMPROVE_LOCK" "$SOREN91_STOP_FILE" \
		"$SOREN91_MAIN_PID_FILE" \
		"$SOREN91_DIR/tmp/in_game" \
		"$ELOOP_LIB_DIR/tmp/mute_local_bgm"
	_soren91_clear_stale_runner_lock
	_clear_meriken_time_state
	_soren91_close_shared_game_tabs
	_soren91_stop_standalone_browser
	_soren91_switch_obs_layout china || true
}
