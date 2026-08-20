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

# --- VM 読み上げキュー連携（リポジトリルール） ---
# 作業中は適宜 VM 側の audio-worker に読み上げさせる。`enqueue_audio_text` の
# 120s dedup により同一タイトルの連続 spam は抑止される。
_enqueue_work_audio() {
	local _w_title="$1" _w_body="$2" _w_text=""
	[ -n "$_w_title" ] || return 0
	_w_text="作業中: $_w_title"
	[ -n "$_w_body" ] && _w_text="$_w_text $_w_body"
	# TTS 用に 80 字に丸め（本文が長い場合は先頭のみ）
	_w_text=$(printf '%s' "$_w_text" | cut -c1-80)
	# ローカル（VM 上なら VM キュー、ローカルならローカルキュー）へ enqueue
	if [ -f "$ELOOP_LIB_DIR/lib/outbound_queue.sh" ]; then
		# shellcheck source=/dev/null
		source "$ELOOP_LIB_DIR/lib/outbound_queue.sh" 2>/dev/null || true
		if type enqueue_audio_text >/dev/null 2>&1; then
			enqueue_audio_text "$_w_text" "work_indicator" >/dev/null 2>&1 || true
		fi
	fi
	# ローカル実行時は VM 側にも enqueue（VM 上では ELOOP_LIB_DIR=/home/ubuntu/soren なのでスキップ）
	case "$ELOOP_LIB_DIR" in
		/home/ubuntu/soren) ;;
		*)
			if [ -f "$HOME/.ssh/id_rsa" ] && ssh -o ConnectTimeout=2 -o BatchMode=yes -i "$HOME/.ssh/id_rsa" ubuntu@129.146.54.105 "true" 2>/dev/null; then
				# %q で安全にエスケープして VM 側で enqueue
				ssh -o ConnectTimeout=2 -i "$HOME/.ssh/id_rsa" ubuntu@129.146.54.105 "cd /home/ubuntu/soren && source lib/outbound_queue.sh 2>/dev/null; enqueue_audio_text $(printf '%q' "$_w_text") work_indicator" >/dev/null 2>&1 || true
			fi
			;;
	esac
}
case "$action" in
	start|show|on) _enqueue_work_audio "$title" "$body" ;;
	stop|hide|off|clear) _enqueue_work_audio "作業完了" "$title" ;;
esac

./obs_control.sh stack "${OBS_DASHBOARD_SCENE:-soren}" >/dev/null 2>&1 || true
