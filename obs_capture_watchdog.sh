#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では OBS キャプチャ watchdog を実行しない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# obs_capture_watchdog.sh — periodic self-heal for the OBS game window capture.
#
# Every OBS_CAPTURE_WATCHDOG_INTERVAL seconds (default 90): if the `sorengame`
# source is visible, run obs_capture_watchdog_check.mjs which (1) rebinds the
# capture to the live Chrome window if the bound macOS window is stale/dead/wrong
# for the current display mode (the classic symptom after a Chrome crash/restart;
# mode = china/meriken from tmp/state/soren_display_mode), and (2) bounces the
# macOS screen_capture if the stream is frozen while the game advances. It only
# acts when the binding is actually wrong or the output is actually frozen, so
# there is no flicker during normal operation, and it never blanks a last-good
# capture when no live window matches yet.
#
# Background daemon: singleton via pidfile, honors tmp/stop. Started by
# soren_loop.sh / start_all.sh; can also be run standalone.
set -u
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

INTERVAL="${OBS_CAPTURE_WATCHDOG_INTERVAL:-90}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=90 ;; esac
SCENE="${OBS_DASHBOARD_SCENE:-soren}"
GAME_SOURCE="${SOREN_GAME_OBS_SOURCE:-${OBS_GAME_SOURCE:-${SOREN_OBS_GAME_SOURCE_NAME:-sorengame}}}"
GAME_STATE_MAX_AGE="${OBS_CAPTURE_WATCHDOG_GAME_MAX_AGE:-90}"
PIDFILE="tmp/state/obs_capture_watchdog.pid"
LOG="logs/obs_capture_watchdog.log"
mkdir -p tmp/state logs 2>/dev/null || true

# --- クラッシュ後の Safe Mode ダイアログ自動対処 ---
# OBSはクラッシュ後の再起動時に「セーフモードで起動/通常モードで起動」ダイアログで
# 止まることがある。これを検出して OBS を --disable-shutdown-check 付きで再起動し、
# ダイアログを出さず通常モードで自動復帰させる(アクセシビリティ権限不要)。
OBS_SAFE_MODE_AUTOFIX="${OBS_SAFE_MODE_AUTOFIX:-1}"
OBS_SAFE_MODE_STREAK="${OBS_SAFE_MODE_STREAK:-2}"          # 連続検出tick数(誤検出回避の猶予)
case "$OBS_SAFE_MODE_STREAK" in ''|*[!0-9]*) OBS_SAFE_MODE_STREAK=2 ;; esac
OBS_SAFE_MODE_RELAUNCH_COOLDOWN="${OBS_SAFE_MODE_RELAUNCH_COOLDOWN:-300}"  # 再起動の連打防止
case "$OBS_SAFE_MODE_RELAUNCH_COOLDOWN" in ''|*[!0-9]*) OBS_SAFE_MODE_RELAUNCH_COOLDOWN=300 ;; esac
OBS_APP_PATH="${OBS_APP_PATH:-/Applications/OBS.app}"
OBS_LOG_DIR="${OBS_LOG_DIR:-$HOME/Library/Application Support/obs-studio/logs}"
OBS_SAFE_RELAUNCH_TS="tmp/state/obs_safe_relaunch_last.ts"

# singleton
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
	exit 0
fi
echo $$ >"$PIDFILE"
trap 'rm -f "$PIDFILE" 2>/dev/null || true' EXIT
# INT/TERM must actually terminate the daemon (not just run the EXIT trap and keep
# looping), otherwise a restart leaves the old loop alive with stale code/interval.
trap 'exit 143' TERM
trap 'exit 130' INT

_log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true; }

_game_state_fresh() {
	[ -f game_state.json ] || return 1
	local age now mt
	now=$(date +%s)
	mt=$(stat -f %m game_state.json 2>/dev/null || echo 0)
	age=$(( now - mt ))
	[ "$age" -le "$GAME_STATE_MAX_AGE" ]
}

_sorengame_visible() {
	[ -x ./obs_control.sh ] || return 1
	./obs_control.sh status "$SCENE" "$GAME_SOURCE" 2>/dev/null | grep -q "${GAME_SOURCE}=on"
}

# OBS 本体プロセス(ヘルパー除く)が生きているか
_obs_running() { pgrep -x OBS >/dev/null 2>&1; }

_latest_obs_log() { ls -t "$OBS_LOG_DIR"/*.txt 2>/dev/null | head -n1; }

# クラッシュ後の「セーフモード/通常モード」ダイアログが今まさに表示中か。
# OBSログ先頭に Unclean shutdown 検出があり、かつユーザー選択(通常/セーフ)行がまだ無い状態を指す。
_obs_safe_mode_dialog_open() {
	local log head8
	log=$(_latest_obs_log)
	[ -n "$log" ] || return 1
	head8=$(head -c 8192 "$log" 2>/dev/null)
	printf '%s' "$head8" | grep -q '\[Safe Mode\] Unclean shutdown detected!' || return 1
	printf '%s' "$head8" | grep -q '\[Safe Mode\] User elected to launch normally\.' && return 1
	printf '%s' "$head8" | grep -q '\[Safe Mode\] User has launched in Safe Mode\.' && return 1
	return 0
}

# OBS WebSocket が応答しない(=ダイアログでブロックされ未起動)か
_obs_ws_down() {
	[ -x ./obs_control.sh ] || return 0
	local s
	s=$(OBS_WEBSOCKET_TIMEOUT_MS=3000 ./obs_control.sh stream-status 2>/dev/null)
	case "$s" in streaming=*) return 1 ;; *) return 0 ;; esac
}

# OBSを終了して --disable-shutdown-check 付きで再起動(ダイアログを出さず通常起動)
_obs_relaunch_normal() {
	local now last waited=0
	now=$(date +%s)
	last=$(cat "$OBS_SAFE_RELAUNCH_TS" 2>/dev/null || echo 0)
	case "$last" in ''|*[!0-9]*) last=0 ;; esac
	if [ $((now - last)) -lt "$OBS_SAFE_MODE_RELAUNCH_COOLDOWN" ]; then
		_log "safe-mode: relaunch skipped (cooldown $((now - last))s < ${OBS_SAFE_MODE_RELAUNCH_COOLDOWN}s)"
		return 0
	fi
	echo "$now" >"$OBS_SAFE_RELAUNCH_TS" 2>/dev/null || true
	_log "safe-mode: ダイアログ検出 → OBSを終了して通常モードで再起動する"
	# モーダルダイアログでは graceful quit が効かないことが多いので、待って強制終了
	osascript -e 'tell application "OBS" to quit' >/dev/null 2>&1 || true
	while _obs_running && [ "$waited" -lt 4 ]; do sleep 1; waited=$((waited + 1)); done
	if _obs_running; then
		pkill -x OBS 2>/dev/null || true
		pkill -f 'OBS\.app/Contents/Frameworks/OBS Helper' 2>/dev/null || true
	fi
	waited=0
	while _obs_running && [ "$waited" -lt 6 ]; do sleep 1; waited=$((waited + 1)); done
	if _obs_running; then
		pkill -9 -x OBS 2>/dev/null || true
		sleep 1
	fi
	if open -a "$OBS_APP_PATH" --args --disable-shutdown-check >/dev/null 2>&1; then
		_log "safe-mode: OBS を通常モードで再起動しました (--disable-shutdown-check)"
	else
		_log "safe-mode: OBS 再起動に失敗しました"
	fi
}

_log "watchdog start interval=${INTERVAL}s source=${GAME_SOURCE}"
safe_streak=0
while true; do
	sleep "$INTERVAL" &
	wait $! 2>/dev/null || true
	[ -f tmp/stop ] && { _log "tmp/stop detected; exiting"; break; }

	# クラッシュ後の Safe Mode ダイアログを最優先で処理(この間 WebSocket は落ちている)
	if [ "$OBS_SAFE_MODE_AUTOFIX" = "1" ]; then
		if _obs_running && _obs_safe_mode_dialog_open && _obs_ws_down; then
			safe_streak=$((safe_streak + 1))
			_log "safe-mode dialog 検出 (streak=${safe_streak}/${OBS_SAFE_MODE_STREAK})"
			if [ "$safe_streak" -ge "$OBS_SAFE_MODE_STREAK" ]; then
				_obs_relaunch_normal
				safe_streak=0
			fi
			continue
		fi
		safe_streak=0
	fi

	if ! _sorengame_visible; then
		_log "skip: ${GAME_SOURCE} not visible (improve/param session?)"
		continue
	fi
	out=$(node obs_capture_watchdog_check.mjs 2>&1)
	rc=$?
	case "$rc" in
		10) _log "FROZEN -> bounced. ${out##*$'\n'}" ;;
		11) _log "STALE -> rebound. ${out##*$'\n'}" ;;
		0)  _log "ok. ${out##*$'\n'}" ;;
		*)  _log "check rc=$rc: ${out##*$'\n'}" ;;
	esac
done
