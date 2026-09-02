#!/bin/bash
# codex_work_indicator.sh - keep the Codex work indicator inside eventOverlay.
set -euo pipefail
cd "$(dirname "$0")"

ELOOP_LIB_DIR="$(pwd)"
export ELOOP_LIB_DIR
# shellcheck source=/dev/null
source "$ELOOP_LIB_DIR/core/config.sh"

action="${1:-start}"
title="${2:-システム自動分析・修正作業中}"
body="${3:-}"

mkdir -p "$(dirname "$CODEX_WORK_OVERLAY_STATE_FILE")" "$(dirname "$EVENT_OVERLAY_HTML_FILE")"

case "$action" in
start|show|on)
	python3 - "$CODEX_WORK_OVERLAY_STATE_FILE" "$title" "$body" <<'PY'
import json
import os
import sys
import tempfile
import time
from pathlib import Path

path = Path(sys.argv[1])
state = {
    "active": True,
    "ts": int(time.time()),
    "title": sys.argv[2][:80],
    "body": sys.argv[3][:240],
}
fd, tmp = tempfile.mkstemp(prefix=".codex_work.", suffix=".json", dir=str(path.parent))
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    f.write("\n")
os.replace(tmp, path)
PY
	;;
stop|hide|off|clear)
	rm -f "$CODEX_WORK_OVERLAY_STATE_FILE"
	;;
status)
	if [ -s "$CODEX_WORK_OVERLAY_STATE_FILE" ]; then
		cat "$CODEX_WORK_OVERLAY_STATE_FILE"
	else
		echo '{"active":false}'
	fi
	exit 0
	;;
*)
	echo "usage: $0 start|stop|status [title] [body]" >&2
	exit 2
	;;
esac

EVENT_OVERLAY_STATE_BASE="$ELOOP_LIB_DIR" \
EVENT_OVERLAY_COMMENT_GEN_STATE="$COMMENT_GEN_STATE_FILE" \
EVENT_OVERLAY_RADIO_STATE="$RADIO_STATE_FILE" \
python3 "$ELOOP_LIB_DIR/generate_event_overlay.py" \
	"$EVENT_OVERLAY_EVENTS_FILE" \
	"$EVENT_OVERLAY_HTML_FILE" \
	"$EVENT_OVERLAY_KEEP_EVENTS" \
	"$EVENT_OVERLAY_VISIBLE_SEC" \
	"$CODEX_WORK_OVERLAY_STATE_FILE"

# ローカル実行時は VM 側のオーバーレイも同期（VM 上ではスキップ）
case "$ELOOP_LIB_DIR" in
	/home/ubuntu/soren) ;;
	*)
		if [ -f "$HOME/.ssh/id_rsa" ] && ssh -o ConnectTimeout=2 -o BatchMode=yes -i "$HOME/.ssh/id_rsa" ubuntu@129.146.54.105 "true" 2>/dev/null; then
			ssh -o ConnectTimeout=2 -i "$HOME/.ssh/id_rsa" ubuntu@129.146.54.105 "cd /home/ubuntu/soren && ./codex_work_indicator.sh $(printf '%q' "$action") $(printf '%q' "$title") $(printf '%q' "$body")" >/dev/null 2>&1 || true
		fi
		;;
esac

# --- VM 読み上げキュー連携（リポジトリルール §8） ---
# 作業中は VM 側の audio-worker に、body をそのまま自然な一文として読み上げさせる。
# バナーはフェーズごとに更新するが、音声は作業セッションの開始時だけに抑える。
_enqueue_work_audio() {
	local _w_action="$1" _w_title="$2" _w_body="$3" _w_text=""
	local _last_file="$ELOOP_LIB_DIR/tmp/state/work_audio_last.json"
	local _now _active _announced _start_ts _last_audio_ts _session_title
	_now=$(date +%s)
	_active=$(python3 -c "import json,sys; print(1 if json.load(open(sys.argv[1])).get('active') else 0)" "$_last_file" 2>/dev/null || echo 0)
	_announced=$(python3 -c "import json,sys; print(1 if json.load(open(sys.argv[1])).get('announced') else 0)" "$_last_file" 2>/dev/null || echo 0)
	_start_ts=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('start_ts',0))" "$_last_file" 2>/dev/null || echo 0)
	_last_audio_ts=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('last_audio_ts',0))" "$_last_file" 2>/dev/null || echo 0)
	_session_title=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('title',''))" "$_last_file" 2>/dev/null || echo "")
	case "$_start_ts" in ''|*[!0-9]*) _start_ts=0 ;; esac
	case "$_last_audio_ts" in ''|*[!0-9]*) _last_audio_ts=0 ;; esac
	# stop を実行できずセッション状態だけ残った場合も、表示と同じ期限で回収する。
	if [ "$_active" = "1" ] && [ "$_start_ts" -gt 0 ] \
		&& [ $(( _now - _start_ts )) -gt "${CODEX_WORK_OVERLAY_STALE_SEC:-3600}" ]; then
		_active=0
		_announced=0
	fi

	case "$_w_action" in
		start)
			# フェーズ更新はバナーだけ。既に作業中なら音声を追加しない。
			[ "$_active" = "1" ] && return 0
			# 短い作業が連続する場合も、開始音声は15分に1回まで。
			if [ $(( _now - _last_audio_ts )) -ge "${WORK_AUDIO_MIN_INTERVAL_SEC:-900}" ]; then
				_announced=1
				_w_text="${_w_body:-$_w_title}"
				case "$_w_text" in *[。！？!?]) ;; *) _w_text="${_w_text}。" ;; esac
				_last_audio_ts="$_now"
			else
				_announced=0
			fi
			_active=1
			_start_ts="$_now"
			_session_title="$_w_title"
			;;
		stop)
			# 重複 stop や、開始音声を省略した短いセッションでは完了音声も流さない。
			[ "$_active" = "1" ] || return 0
			_active=0
			if [ "$_announced" = "1" ] && [ $(( _now - _start_ts )) -ge "${WORK_AUDIO_COMPLETION_MIN_SEC:-180}" ]; then
				_w_text="${_w_body:-${_session_title}はここまでです。}"
				case "$_w_text" in *[。！？!?]) ;; *) _w_text="${_w_text}。" ;; esac
				_last_audio_ts="$_now"
			fi
			;;
		*) return 0 ;;
	esac
	# TTS 用に 240字に丸め
	_w_text=$(printf '%s' "$_w_text" | cut -c1-240)
	# セッション状態を更新（フェーズ名が変わっても同じ作業として扱う）
	mkdir -p "$(dirname "$ELOOP_LIB_DIR/tmp/state/work_audio_last.json")" 2>/dev/null || true
	python3 - "$_last_file" "$_active" "$_announced" "$_start_ts" "$_last_audio_ts" "$_session_title" <<'PY' 2>/dev/null || true
import json, sys, time
from pathlib import Path
path, active, announced, start_ts, last_audio_ts, title = sys.argv[1:]
data={
    "active": active == "1",
    "announced": announced == "1",
    "start_ts": int(start_ts),
    "last_audio_ts": int(last_audio_ts),
    "title": title,
}
tmp=str(path)+".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
    f.write("\n")
import os
os.replace(tmp, path)
PY
	[ -n "$_w_text" ] || return 0
	# ローカル（VM 上なら VM キュー、ローカルならローカルキュー）へ enqueue（workは300s dedupで大くくり）
	if [ -f "$ELOOP_LIB_DIR/lib/outbound_queue.sh" ]; then
		# shellcheck source=/dev/null
		source "$ELOOP_LIB_DIR/lib/outbound_queue.sh" 2>/dev/null || true
		if type enqueue_audio_text >/dev/null 2>&1; then
			COMMENT_AUDIO_DEDUP_TTL_SEC=300 enqueue_audio_text "$_w_text" "work_indicator" >/dev/null 2>&1 || true
		fi
	fi
	# ローカル実行時は VM 側にも enqueue（VM 上ではスキップ）
	case "$ELOOP_LIB_DIR" in
		/home/ubuntu/soren) ;;
		*)
			if [ -f "$HOME/.ssh/id_rsa" ] && ssh -o ConnectTimeout=2 -o BatchMode=yes -i "$HOME/.ssh/id_rsa" ubuntu@129.146.54.105 "true" 2>/dev/null; then
				ssh -o ConnectTimeout=2 -i "$HOME/.ssh/id_rsa" ubuntu@129.146.54.105 "cd /home/ubuntu/soren && source lib/outbound_queue.sh 2>/dev/null; COMMENT_AUDIO_DEDUP_TTL_SEC=300 enqueue_audio_text $(printf '%q' "$_w_text") work_indicator" >/dev/null 2>&1 || true
			fi
			;;
	esac
}
case "$action" in
	start|show|on) _enqueue_work_audio start "$title" "$body" ;;
	stop|hide|off|clear) _enqueue_work_audio stop "$title" "$body" ;;
esac

./obs_control.sh stack "${OBS_DASHBOARD_SCENE:-soren}" >/dev/null 2>&1 || true
