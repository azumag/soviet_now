#!/bin/bash
# workers/stream_noon_audit.sh - 配信開始位相の正午監査ワーカー
#
# 責務:
#   Twitch の 48時間強制切断を「正確な48時間タイマー」として利用し、
#   配信の開始時刻を毎日 JST 12:00 基準へ是正する。
#
# 動作:
#   - 毎日 JST 正午に1回だけ監査する (tmp/state/stream_noon_audit/<jst_day>.json)
#   - 現在の配信の started_at の JST 时刻が正午から
#     STREAM_NOON_AUDIT_TOLERANCE_SEC 以内なら何もしない
#     (Twitch カット直後の再接続開始 ≒ 正午、はこれで通過する)
#   - ずれていれば wiki 正規手順 (lib/direct_stream.py stop) で停止し、
#     supervisor 自動 respawn を待って張り直す (新しい開始 ≒ JST 12:00)
#
# 設計メモ:
#   - Twitch カット後の自動再接続は開始位相を保存するため、「正午に稼働34h以上なら
#     再起動」の閾値方式だけでは開始が 02:00〜12:00 帯の配信は永久に是正されない
#     (2026-08-22 解析)。本監査は開始時刻の位相そのものを見る。
#   - 是正の過渡期だけは配信が短縮されることがある (任意の乱れ後に開始時刻と
#     ラン長48hの両立は物理的に不可能。定常状態では両立する)。
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

[ -f .env ] && set -a && . ./.env && set +a

WORKER_NAME="stream_noon_audit"
PID_FILE="${STREAM_NOON_AUDIT_PID_FILE:-tmp/state/${WORKER_NAME}.pid}"
PAUSE_FILE="${STREAM_NOON_AUDIT_PAUSE_FILE:-tmp/state/${WORKER_NAME}.paused}"

ENABLED="${STREAM_NOON_AUDIT_ENABLED:-1}"
POLL_INTERVAL="${STREAM_NOON_AUDIT_POLL_SEC:-30}"
TOLERANCE_SEC="${STREAM_NOON_AUDIT_TOLERANCE_SEC:-600}"
RESPAWN_WAIT_SEC="${STREAM_NOON_AUDIT_RESPAWN_WAIT_SEC:-90}"
STOP_TIMEOUT_SEC="${STREAM_NOON_AUDIT_STOP_TIMEOUT_SEC:-45}"
STATE_DIR="${STREAM_NOON_AUDIT_STATE_DIR:-tmp/state/stream_noon_audit}"

NOW_CMD="${STREAM_NOON_AUDIT_NOW_CMD:-date +%s}"
STATUS_CMD="${STREAM_NOON_AUDIT_STATUS_CMD:-python3 lib/direct_stream.py status}"
STOP_CMD="${STREAM_NOON_AUDIT_STOP_CMD:-python3 lib/direct_stream.py stop}"
RUN_CMD="${STREAM_NOON_AUDIT_RUN_CMD:-./direct_stream.sh run}"
STREAM_PAUSE_MARKER="${STREAM_NOON_AUDIT_STREAM_PAUSE_MARKER:-tmp/state/direct_stream.paused}"
LOG_FILE="${STREAM_NOON_AUDIT_LOG_FILE:-logs/direct_stream.log}"

JST_OFFSET_SEC=32400 # +09:00 (JST, DSTなし)
NOON_SOD_SEC=43200   # 12:00:00 JST の時刻内秒
DAY_SEC=86400

_STOPPED=0
_RELOAD_REQUESTED=0
_WE_HOLD_PAUSE=0

_log() {
	# stderr へ出力: _restart_stream の stdout は new_started の受け渡しに使う
	echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*" >&2
}

_pid_alive() {
	local pid="${1:-}" err=""
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	case "$err" in
	*"operation not permitted"*|*"Operation not permitted"*) return 0 ;;
	esac
	return 1
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if [ "$active_pid" != "$$" ]; then
		_log "cleanup skipped: pidfile owner is ${active_pid:-none} (self=$$)"
		return 0
	fi
	# 異常終了時に配信が pause マーカーで固定される (dead air) ことを防ぐ。
	if [ "${_WE_HOLD_PAUSE:-0}" = "1" ] && [ -f "$STREAM_PAUSE_MARKER" ]; then
		rm -f "$STREAM_PAUSE_MARKER"
		_log "WARN: 異常終了のため stream pause marker を解除して復帰"
	fi
	rm -f "$PID_FILE"
	_log "停止完了"
}

_handle_signal() {
	_cleanup
	trap - EXIT
	exit 130
}
_request_reload() {
	_RELOAD_REQUESTED=1
	_log "reload requested (signal=$1)"
}
_reload_runtime() {
	[ "$_RELOAD_REQUESTED" -eq 1 ] || return 0
	_RELOAD_REQUESTED=0
	if [ -f .env ]; then
		set -a
		. ./.env
		set +a
	fi
	_log "reload complete"
}
trap '_cleanup' EXIT
trap '_handle_signal' INT TERM
trap '_request_reload HUP' HUP
trap '_request_reload USR1' USR1

if [ -f "$PAUSE_FILE" ]; then
	_log "paused by $PAUSE_FILE → exit"
	exit 0
fi

if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null)
	if _pid_alive "$old_pid"; then
		_log "ERROR: 既に起動中 (PID=$old_pid)"
		exit 1
	fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$STATE_DIR")" 2>/dev/null || true
echo $$ >"$PID_FILE"

_now() {
	$NOW_CMD 2>/dev/null || date +%s
}

_status_field() {
	local key="$1" val=""
	val=$($STATUS_CMD 2>/dev/null | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    v = d.get(sys.argv[1], "")
    print("True" if v is True else ("False" if v is False else v))
except Exception:
    print("")' "$key") || val=""
	printf '%s' "$val"
}

_status_flag() {
	[ "$(_status_field running)" = "True" ] && { echo 1; return; }
	echo 0
}

_status_int() {
	local val
	val=$(_status_field "$1")
	case "$val" in
	''|*[!0-9]*) echo "" ;;
	*) echo "$val" ;;
	esac
}

_write_marker_json() {
	local day="$1" path="$2" tmpf="$3"; shift 3
	{
		printf '{\n'
		printf '  "jst_day": %s,\n' "$day"
		local key val
		while [ $# -gt 0 ]; do
			key="$1"
			val="$2"
			shift 2
			if [ $# -gt 0 ]; then
				printf '  "%s": "%s",\n' "$key" "$val"
			else
				printf '  "%s": "%s"\n' "$key" "$val"
			fi
		done
		printf '}\n'
	} >"$tmpf" 2>/dev/null && mv -f "$tmpf" "$path"
}

_marker_update_field() {
	# 既存 marker JSON のフィールドを python で差し替え (失敗時は放置)
	local path="$1" key="$2" val="$3"
	python3 -c 'import json,sys,os
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(path, encoding="utf-8"))
    d[key] = val
    tmp = path + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)
except Exception:
    pass' "$path" "$key" "$val" 2>/dev/null || true
}

_wait_running_with_new_start() {
	# 新しい started_at の配信が立ち上がるまで待つ。見つかれば started_at を返す。
	local old_started="$1" wait_sec="$2" deadline now cand
	deadline=$(date +%s)
	deadline=$((deadline + wait_sec))
	while :; do
		now=$(date +%s)
		[ "$now" -ge "$deadline" ] && break
		if [ "$(_status_flag running)" = "1" ]; then
			cand=$(_status_int started_at)
			if [ -n "$cand" ] && [ "$cand" != "$old_started" ]; then
				echo "$cand"
				return 0
			fi
		fi
		sleep 2
	done
	echo ""
	return 1
}

_restart_stream() {
	local old_started="$1" new_started="" stop_rc=0 deadline
	_log "位相ずれ検出 → 配信を正午へ張り直します (old_started=${old_started})"
	touch "$STREAM_PAUSE_MARKER"
	_WE_HOLD_PAUSE=1
	if $STOP_CMD >>"$LOG_FILE" 2>&1; then
		stop_rc=0
	else
		stop_rc=$?
		_log "WARN: stop コマンド rc=${stop_rc} (停止確認へ継続)"
	fi
	deadline=$(date +%s)
	deadline=$((deadline + STOP_TIMEOUT_SEC))
	while :; do
		[ "$(_status_flag running)" != "1" ] && break
		[ "$(date +%s)" -ge "$deadline" ] && break
		sleep 1
	done
	rm -f "$STREAM_PAUSE_MARKER"
	_WE_HOLD_PAUSE=0
	new_started=$(_wait_running_with_new_start "$old_started" "$RESPAWN_WAIT_SEC")
	if [ -z "$new_started" ]; then
		_log "WARN: supervisor respawn 未検出 (${RESPAWN_WAIT_SEC}s) → 自前起動を試行"
		( nohup $RUN_CMD >>"$LOG_FILE" 2>&1 & ) || true
		new_started=$(_wait_running_with_new_start "$old_started" 30)
	fi
	if [ -n "$new_started" ]; then
		_log "配信を再開しました (new_started=${new_started})"
		echo "$new_started"
		return 0
	fi
	_log "ERROR: 再開を確認できませんでした (supervisor/ffmpeg の状態を確認すること)"
	return 1
}

_audit_once() {
	local jst_day="$1"
	local marker="$STATE_DIR/${jst_day}.json"
	local decision="" detail="" old_started="" new_started="" outcome=""
	local noon_epoch sod diff circ_diff

	noon_epoch=$(( jst_day * DAY_SEC + NOON_SOD_SEC - JST_OFFSET_SEC ))

	if [ -f "$STREAM_PAUSE_MARKER" ]; then
		decision="skipped_paused"
		detail="stream pause marker 存在 (意図的停止のため触れない)"
	elif [ "$(_status_flag running)" != "1" ]; then
		decision="skipped_not_running"
		detail="配信が稼働していない (supervisor の再起動機構に任せる)"
	else
		old_started=$(_status_int started_at)
		if [ -z "$old_started" ]; then
			decision="skipped_bad_status"
			detail="started_at を解釈できず安全側でスキップ"
		else
			sod=$(( (old_started % DAY_SEC + JST_OFFSET_SEC) % DAY_SEC ))
			diff=$(( sod - NOON_SOD_SEC ))
			circ_diff=$(( (diff + NOON_SOD_SEC) % DAY_SEC - NOON_SOD_SEC ))
			if [ "${circ_diff#-}" -le "$TOLERANCE_SEC" ]; then
				decision="no_action"
				detail="offset_diff=${circ_diff}s tolerance=${TOLERANCE_SEC}s 以内"
			else
				decision="restart_required"
				detail="offset_diff=${circ_diff}s > tolerance=${TOLERANCE_SEC}s"
			fi
		fi
	fi

	_log "監査 jst_day=${jst_day}: ${decision} (${detail})"
	mkdir -p "$STATE_DIR" 2>/dev/null || true
	_write_marker_json "$jst_day" "$marker" "$marker.tmp" \
		"decided_at" "$(date +%s)" \
		"decision" "$decision" \
		"detail" "$detail" \
		"started_before" "$old_started"

	if [ "$decision" != "restart_required" ]; then
		return 0
	fi

	if new_started=$(_restart_stream "$old_started"); then
		outcome="restarted"
	else
		outcome="restart_failed"
	fi
	_marker_update_field "$marker" "outcome" "$outcome"
	_marker_update_field "$marker" "started_after" "$new_started"
	return 0
}

_log "起動 (PID=$$, poll=${POLL_INTERVAL}s tolerance=${TOLERANCE_SEC}s enabled=${ENABLED})"

while true; do
	_reload_runtime
	if [ -f "$PAUSE_FILE" ]; then
		_log "pause file detected → exit"
		break
	fi
	if [ -f tmp/stop ]; then
		_log "stop ファイル検出 → 終了"
		break
	fi
	if [ "$ENABLED" != "1" ]; then
		_log "STREAM_NOON_AUDIT_ENABLED != 1 → exit"
		break
	fi

	now=$(_now)
	case "$now" in
	''|*[!0-9]*)
		sleep "$POLL_INTERVAL"
		continue
		;;
	esac

	jst_day=$(( (now + JST_OFFSET_SEC) / DAY_SEC ))
	marker="$STATE_DIR/${jst_day}.json"
	if [ ! -f "$marker" ]; then
		noon_epoch=$(( jst_day * DAY_SEC + NOON_SOD_SEC - JST_OFFSET_SEC ))
		if [ "$now" -ge "$noon_epoch" ]; then
			_audit_once "$jst_day"
		fi
	fi

	_sleep_remaining="$POLL_INTERVAL"
	while [ "${_sleep_remaining:-0}" -gt 0 ]; do
		[ -f tmp/stop ] && break 2
		sleep 1
		_sleep_remaining=$((_sleep_remaining - 1))
	done
done

_cleanup
_log "メインループ終了"
exit 0
