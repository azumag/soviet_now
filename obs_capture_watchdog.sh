#!/bin/bash
# obs_capture_watchdog.sh — periodic self-heal for the OBS game window capture.
#
# Every OBS_CAPTURE_WATCHDOG_INTERVAL seconds (default 90): if the `sorengame`
# source is visible, run obs_capture_watchdog_check.mjs. Window-capture families
# validate/rebind a stale Chrome window; macOS may also bounce a frozen source and
# Linux XComposite does so only after explicit opt-in. XSHM has no window binding:
# it samples screenshots and only warns on a freeze signal, without mutating the
# full-display source. No path blanks a last-good capture when no live window
# matches yet.
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

OBS_CAPTURE_PLATFORM="${SOREN_OBS_PLATFORM:-}"
if [ -z "$OBS_CAPTURE_PLATFORM" ]; then
	case "$(uname -s 2>/dev/null || true)" in
		Linux) OBS_CAPTURE_PLATFORM=linux ;;
		Darwin) OBS_CAPTURE_PLATFORM=darwin ;;
		*) OBS_CAPTURE_PLATFORM=unknown ;;
	esac
else
	OBS_CAPTURE_PLATFORM=$(printf '%s' "$OBS_CAPTURE_PLATFORM" | tr '[:upper:]' '[:lower:]')
fi

case "$OBS_CAPTURE_PLATFORM" in
	linux)
		_OBS_SAFE_MODE_AUTOFIX_DEFAULT=0
		_OBS_PROCESS_NAME_DEFAULT=obs
		_OBS_APP_PATH_DEFAULT=obs
		_OBS_LOG_DIR_DEFAULT="$HOME/.config/obs-studio/logs"
		;;
	*)
		# Keep every existing macOS default unchanged.
		_OBS_SAFE_MODE_AUTOFIX_DEFAULT=1
		_OBS_PROCESS_NAME_DEFAULT=OBS
		_OBS_APP_PATH_DEFAULT=/Applications/OBS.app
		_OBS_LOG_DIR_DEFAULT="$HOME/Library/Application Support/obs-studio/logs"
		;;
esac

# --- クラッシュ後の Safe Mode ダイアログ自動対処 ---
# OBSはクラッシュ後の再起動時に「セーフモードで起動/通常モードで起動」ダイアログで
# 止まることがある。これを検出して OBS を --disable-shutdown-check 付きで再起動し、
# ダイアログを出さず通常モードで自動復帰させる(アクセシビリティ権限不要)。
OBS_SAFE_MODE_AUTOFIX="${OBS_SAFE_MODE_AUTOFIX:-$_OBS_SAFE_MODE_AUTOFIX_DEFAULT}"
OBS_SAFE_MODE_STREAK="${OBS_SAFE_MODE_STREAK:-2}"          # 連続検出tick数(誤検出回避の猶予)
case "$OBS_SAFE_MODE_STREAK" in ''|*[!0-9]*) OBS_SAFE_MODE_STREAK=2 ;; esac
OBS_SAFE_MODE_RELAUNCH_COOLDOWN="${OBS_SAFE_MODE_RELAUNCH_COOLDOWN:-300}"  # 再起動の連打防止
case "$OBS_SAFE_MODE_RELAUNCH_COOLDOWN" in ''|*[!0-9]*) OBS_SAFE_MODE_RELAUNCH_COOLDOWN=300 ;; esac
OBS_PROCESS_NAME="${OBS_PROCESS_NAME:-$_OBS_PROCESS_NAME_DEFAULT}"
OBS_APP_PATH="${OBS_APP_PATH:-$_OBS_APP_PATH_DEFAULT}"
OBS_LOG_DIR="${OBS_LOG_DIR:-$_OBS_LOG_DIR_DEFAULT}"
OBS_SAFE_RELAUNCH_TS="tmp/state/obs_safe_relaunch_last.ts"
OBS_LINUX_RESTART_MODE="${OBS_LINUX_RESTART_MODE:-systemd}"
OBS_SYSTEMD_UNIT="${OBS_SYSTEMD_UNIT:-obs.service}"
OBS_SYSTEMD_SCOPE="${OBS_SYSTEMD_SCOPE:-user}"
OBS_DISPLAY="${OBS_DISPLAY:-${DISPLAY:-:99}}"
OBS_LINUX_RELAUNCH_LOG="${OBS_LINUX_RELAUNCH_LOG:-logs/obs_linux_relaunch.log}"

if [ "${1:-}" = "--print-config" ] || [ "${OBS_CAPTURE_WATCHDOG_PRINT_CONFIG:-0}" = "1" ]; then
	printf 'platform=%s\nprocess_name=%s\nlog_dir=%s\nsafe_mode_autofix=%s\napp_path=%s\nrestart_mode=%s\ndisplay=%s\n' \
		"$OBS_CAPTURE_PLATFORM" "$OBS_PROCESS_NAME" "$OBS_LOG_DIR" "$OBS_SAFE_MODE_AUTOFIX" \
		"$OBS_APP_PATH" "$OBS_LINUX_RESTART_MODE" "$OBS_DISPLAY"
	exit 0
fi

mkdir -p tmp/state logs 2>/dev/null || true

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
	mt=$(stat -f %m game_state.json 2>/dev/null) \
		|| mt=$(stat -c %Y game_state.json 2>/dev/null) \
		|| mt=0
	age=$(( now - mt ))
	[ "$age" -le "$GAME_STATE_MAX_AGE" ]
}

_sorengame_visible() {
	[ -x ./obs_control.sh ] || return 1
	./obs_control.sh status "$SCENE" "$GAME_SOURCE" 2>/dev/null | grep -q "${GAME_SOURCE}=on"
}

# OBS 本体プロセス(ヘルパー除く)が生きているか
_obs_running() { pgrep -x "$OBS_PROCESS_NAME" >/dev/null 2>&1; }

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

# Linux のみ: Xvfb 上でゲーム(Chromium)ウィンドウが最小化(Iconic)されると Unity WebGL の
# 描画が止まり、XSHM の画面全体が静止する。FROZEN 検出時に Iconic なら復元する。
# OBS ウィンドウは触らない(--minimize-to-tray 運用のまま)。
_restore_game_window_linux() {
	[ "$OBS_CAPTURE_PLATFORM" = "linux" ] || return 0
	command -v xdotool >/dev/null 2>&1 || return 0
	command -v xprop >/dev/null 2>&1 || return 0
	local wid
	wid=$(DISPLAY="$OBS_DISPLAY" xdotool search --class 'Chromium' 2>/dev/null | head -n1)
	[ -n "$wid" ] || wid=$(DISPLAY="$OBS_DISPLAY" xdotool search --name 'soren-game' 2>/dev/null | head -n1)
	[ -n "$wid" ] || return 0
	if DISPLAY="$OBS_DISPLAY" xprop -id "$wid" WM_STATE 2>/dev/null | grep -q 'Iconic'; then
		DISPLAY="$OBS_DISPLAY" xdotool windowmap "$wid" >/dev/null 2>&1 || true
		DISPLAY="$OBS_DISPLAY" xdotool windowactivate "$wid" >/dev/null 2>&1 || true
		_log "restore: game window ${wid} was Iconic -> restored (WM_CLASS=Chromium)"
	fi
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

	if [ "$OBS_CAPTURE_PLATFORM" = "linux" ]; then
		case "$OBS_LINUX_RESTART_MODE" in
			systemd)
				if ! command -v systemctl >/dev/null 2>&1; then
					_log "safe-mode: systemctl not found; Linux OBS restart left untouched"
					return 1
				fi
				case "$OBS_SYSTEMD_SCOPE" in
					user|system)
						if ! systemctl --"$OBS_SYSTEMD_SCOPE" is-active --quiet "$OBS_SYSTEMD_UNIT" 2>/dev/null; then
							_log "safe-mode: specified $OBS_SYSTEMD_SCOPE unit is not active (${OBS_SYSTEMD_UNIT}); Linux OBS left untouched"
							return 1
						fi
						if systemctl --"$OBS_SYSTEMD_SCOPE" restart "$OBS_SYSTEMD_UNIT" >>"$OBS_LINUX_RELAUNCH_LOG" 2>&1; then
							_log "safe-mode: systemd $OBS_SYSTEMD_SCOPE unit ${OBS_SYSTEMD_UNIT} を再起動しました"
							return 0
						fi
						_log "safe-mode: systemd restart failed (${OBS_SYSTEMD_UNIT}); no process fallback attempted"
						return 1
						;;
					*)
						_log "safe-mode: invalid OBS_SYSTEMD_SCOPE=${OBS_SYSTEMD_SCOPE}"
						return 1
						;;
				esac
				;;
			disabled|off|0)
				_log "safe-mode: Linux OBS restart disabled; OBS left untouched"
				return 1
				;;
			*)
				_log "safe-mode: invalid OBS_LINUX_RESTART_MODE=${OBS_LINUX_RESTART_MODE}"
				return 1
				;;
		esac
		# Never fall through from Linux into the macOS process-control path.
		return 1
	fi

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
		10) _log "FROZEN -> bounced. ${out##*$'\n'}"
		    _restore_game_window_linux
		    ;;
		11) _log "STALE -> rebound. ${out##*$'\n'}" ;;
		0)
			_log "ok. ${out##*$'\n'}"
			case "$out" in
				*FROZEN*) _restore_game_window_linux ;;
			esac
			;;
		*)  _log "check rc=$rc: ${out##*$'\n'}" ;;
	esac
done
