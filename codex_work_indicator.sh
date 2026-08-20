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
# 作業中は適宜 VM 側の audio-worker に適切な丁寧さ（です・ます調で簡潔、へりくだりすぎない）で読み上げさせる。
# バナーは粒度細かく更新するが、音声は大くくり（5分以上間隔または大きな区切り）で抑止。
_enqueue_work_audio() {
	local _w_title="$1" _w_body="$2" _w_text="" _w_is_stop=0
	[ -n "$_w_title" ] || return 0
	# stop 時の特別扱い
	case "$_w_title" in
		作業完了*) _w_is_stop=1 ;;
	esac
	# 大くくり判定: 直近の音声から 300s 以内かつ軽微な更新ならスキップ（stop は常に読む）
	if [ "$_w_is_stop" -eq 0 ]; then
		local _last_file="$ELOOP_LIB_DIR/tmp/state/work_audio_last.json"
		local _now _last_ts _last_title
		_now=$(date +%s)
		_last_ts=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('ts',0))" "$_last_file" 2>/dev/null || echo 0)
		_last_title=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('title',''))" "$_last_file" 2>/dev/null || echo "")
		case "$_last_ts" in ''|*[!0-9]*) _last_ts=0 ;; esac
		if [ -n "$_last_title" ] && [ "$_w_title" = "$_last_title" ] && [ $(( _now - _last_ts )) -lt 300 ]; then
			return 0
		fi
		if [ $(( _now - _last_ts )) -lt 180 ] && [ -n "$_last_title" ]; then
			# 3分以内でタイトルが似ている（包含）ならスキップ
			case "$_w_title" in
				*"$_last_title"*|"$_last_title"*) return 0 ;;
			esac
		fi
	fi
	# 定型文に固定せず、呼び出しごとに自然な表現を変えて読ませる（す・です調・へりくだりすぎない）
	# 内容ハッシュに時刻を加えてシード化し、毎回少し異なるテンプレートを決定的でなく選ぶ。
	local _seed
	_seed=$(printf '%s|%s' "$_w_title" "$_w_body" \
		| python3 -c "import sys,time; s=sys.stdin.buffer.read(); print((sum(s) + time.time_ns()//10**8) % 100000)" 2>/dev/null || echo 0)
	case "$_seed" in ''|*[!0-9]*) _seed=0 ;; esac
	if [ "$_w_is_stop" -eq 1 ]; then
		local _stop_tmpl=(
			"%sの作業が完了しました。ご確認ください。"
			"%sが完了しました。ご確認ください。"
			"%sの対応が完了しました。ご確認ください。"
			"%sの作業を終えました。ご確認ください。"
		)
		_w_text=$(printf "${_stop_tmpl[$(( _seed % ${#_stop_tmpl[@]} ))]}" "${_w_body:-$_w_title}")
	else
		local _start_tmpl=(
			"現在、%sの作業を進めています。"
			"ただいま%sを進めています。"
			"%sに取りかかっています。"
			"%sの作業に着手しました。"
			"%sを進めています。"
		)
		_w_text=$(printf "${_start_tmpl[$(( _seed % ${#_start_tmpl[@]} ))]}" "$_w_title")
		if [ -n "$_w_body" ]; then
			local _detail_tmpl=(
				" 詳細は「%s」です。"
				" 具体的には「%s」です。"
				" 内容は「%s」です。"
				" 進めているのは「%s」です。"
			)
			_w_text="${_w_text}$(printf "${_detail_tmpl[$(( (_seed / 7) % ${#_detail_tmpl[@]} ))]}" "$_w_body")"
		else
			_w_text="${_w_text} 進捗があり次第お知らせします。"
		fi
	fi
	# TTS 用に 240字に丸め
	_w_text=$(printf '%s' "$_w_text" | cut -c1-240)
	# 最終送信記録を更新（次の大くくり判定用）
	mkdir -p "$(dirname "$ELOOP_LIB_DIR/tmp/state/work_audio_last.json")" 2>/dev/null || true
	python3 - "$ELOOP_LIB_DIR/tmp/state/work_audio_last.json" "$_w_title" "$_w_body" <<'PY' 2>/dev/null || true
import json, sys, time
from pathlib import Path
path, title, body = sys.argv[1], sys.argv[2], sys.argv[3]
data={"ts": int(time.time()), "title": title, "body": body}
tmp=str(path)+".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
    f.write("\n")
import os
os.replace(tmp, path)
PY
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
	start|show|on) _enqueue_work_audio "$title" "$body" ;;
	stop|hide|off|clear) _enqueue_work_audio "作業完了: $title" "$title" ;;
esac

./obs_control.sh stack "${OBS_DASHBOARD_SCENE:-soren}" >/dev/null 2>&1 || true
