#!/bin/bash
# youtube_chat.sh - YouTube Live Chat polling + pending queue
#
# 使い方:
#   ./youtube_chat.sh poll                 - YouTube API から新着を raw.log に追記
#   ./youtube_chat.sh fetch                - raw.log 差分を pending.log に反映 → tmp/youtube_comments.txt
#   ./youtube_chat.sh ack-batch <file>     - 処理済みコメント行のみ pending.log から削除
#   ./youtube_chat.sh send <message>       - OAuth 設定があり、明示有効化時のみ YouTube chat へ投稿
#   ./youtube_chat.sh status               - 動作状況表示
#   ./youtube_chat.sh ingest-fixture <json> - テスト用: API JSON fixture を raw.log に取り込み

cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a

CHAT_DIR="${YOUTUBE_CHAT_DIR:-tmp/.youtube_chat}"
mkdir -p "$CHAT_DIR"

RAW_LOG="$CHAT_DIR/raw.log"
PENDING_LOG="$CHAT_DIR/pending.log"
OUTFILE="${YOUTUBE_CHAT_OUTFILE:-tmp/youtube_comments.txt}"
SEEN_ID_FILE="$CHAT_DIR/seen_msg_ids.log"
SEEN_LINE_HASH_FILE="$CHAT_DIR/seen_line_hashes.log"
PAGE_TOKEN_FILE="$CHAT_DIR/page_token"
LIVE_CHAT_ID_FILE="$CHAT_DIR/live_chat_id"
POLL_INTERVAL_FILE="$CHAT_DIR/poll_interval_sec"
LAST_POLL_FILE="$CHAT_DIR/last_poll_epoch"
LAST_ERROR_FILE="$CHAT_DIR/last_error.txt"
LOCK_DIR="$CHAT_DIR/.op_lock"
TAB=$'\t'
SEEN_ID_MAX="${YOUTUBE_SEEN_ID_MAX:-4000}"
SEEN_LINE_MAX="${YOUTUBE_SEEN_LINE_MAX:-4000}"
SEEN_LINE_TTL_SEC="${YOUTUBE_FETCH_LINE_HASH_TTL_SEC:-60}"
LOCK_TIMEOUT_SEC=8
LOCK_STALE_SEC=120

CMD="${1:-fetch}"

_log() { echo "[youtube_chat $(date '+%H:%M:%S')] $*" >&2; }

_release_lock() {
	[ -d "$LOCK_DIR" ] || return 0
	local lock_pid
	lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
	if [ -z "$lock_pid" ] || [ "$lock_pid" = "$$" ]; then
		rm -rf "$LOCK_DIR" 2>/dev/null || true
	fi
}

_acquire_lock() {
	local op_name="${1:-op}"
	local start_ts now_ts lock_age lock_pid
	start_ts=$(date +%s)
	while ! mkdir "$LOCK_DIR" 2>/dev/null; do
		if [ -f "$LOCK_DIR/pid" ]; then
			lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
			if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
				rm -rf "$LOCK_DIR" 2>/dev/null || true
				continue
			fi
		fi
		now_ts=$(date +%s)
		lock_age=$((now_ts - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "$now_ts")))
		if [ "$lock_age" -ge "$LOCK_STALE_SEC" ]; then
			rm -rf "$LOCK_DIR" 2>/dev/null || true
			continue
		fi
		if [ $((now_ts - start_ts)) -ge "$LOCK_TIMEOUT_SEC" ]; then
			_log "${op_name}: lock timeout"
			return 1
		fi
		sleep 0.1
	done
	echo "$$" >"$LOCK_DIR/pid" 2>/dev/null || true
	return 0
}

_with_chat_lock() {
	local op_name="$1"
	shift
	_acquire_lock "$op_name" || return 1
	"$@"
	local rc=$?
	_release_lock
	return $rc
}

_hash_text() {
	if command -v md5 >/dev/null 2>&1; then
		printf '%s' "$1" | md5 -q 2>/dev/null
	else
		printf '%s' "$1" | md5sum 2>/dev/null | awk '{print $1}'
	fi
}

_compact_seen_ids() {
	[ -f "$SEEN_ID_FILE" ] || return 0
	local tmpf
	tmpf=$(mktemp "$CHAT_DIR/.seen_ids.XXXXXXXX")
	awk 'NF && !seen[$0]++' "$SEEN_ID_FILE" | tail -n "$SEEN_ID_MAX" >"$tmpf"
	cat "$tmpf" >"$SEEN_ID_FILE"
	rm -f "$tmpf"
}

_compact_seen_line_hashes() {
	[ -f "$SEEN_LINE_HASH_FILE" ] || return 0
	local tmpf now_ts
	now_ts=$(date +%s)
	tmpf=$(mktemp "$CHAT_DIR/.seen_lines.XXXXXXXX")
	awk -F'|' -v now_ts="$now_ts" -v ttl="$SEEN_LINE_TTL_SEC" '
		NF >= 2 && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl && !seen[$2]++ { print $1 "|" $2 }
	' "$SEEN_LINE_HASH_FILE" | tail -n "$SEEN_LINE_MAX" >"$tmpf"
	cat "$tmpf" >"$SEEN_LINE_HASH_FILE"
	rm -f "$tmpf"
}

_line_hash_recently_seen() {
	local line_hash="$1"
	[ -n "$line_hash" ] || return 1
	[ -f "$SEEN_LINE_HASH_FILE" ] || return 1
	local now_ts
	now_ts=$(date +%s)
	awk -F'|' -v target="$line_hash" -v now_ts="$now_ts" -v ttl="$SEEN_LINE_TTL_SEC" '
		$2 == target && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl { found = 1; exit }
		END { exit(found ? 0 : 1) }
	' "$SEEN_LINE_HASH_FILE"
}

_sanitize_comment_line() {
	local line="$1"
	[ -n "$line" ] || return 1
	line=$(printf '%s' "$line" | tr -d '`$\\{}|;<>&')
	line=$(printf '%s' "$line" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
	[ -n "$line" ] || return 1

	if printf '%s\n' "$line" | grep -Eiq 'ignore.*instruction|forget.*instruction|override.*prompt|pretend.*you|act as ai|無視.*指示|指示.*無視|命令.*無視|忘れ.*指示|ふりをし|なりきり|プロンプトインジェクション'; then
		return 1
	fi
	if printf '%s\n' "$line" | grep -Eiq 'sudo|chmod|rm -rf|eval\(|exec\(|ファイル.*削除|コマンド.*実行|スクリプト.*実行|上書き.*ファイル'; then
		return 1
	fi
	printf '%s' "$line"
	return 0
}

_notify_chat_overlay() {
	local source="$1"
	local line="$2"
	local title="${3:-${source} コメント受信}"
	[ "${CHAT_INGEST_OVERLAY_NOTIFY:-1}" = "1" ] || return 0
	[ -n "$line" ] || return 0
	[ -x ./overlay_notify.sh ] || return 0
	./overlay_notify.sh chat "$title" "$line" "info" >/dev/null 2>&1 || true
}

_urlencode() {
	python3 - "$1" <<'PY'
import sys
from urllib.parse import quote
print(quote(sys.argv[1], safe=""))
PY
}

_api_get() {
	local url="$1"
	curl -fsS --max-time "${YOUTUBE_API_TIMEOUT_SEC:-12}" "$url"
}

_resolve_live_chat_id() {
	if [ -n "${YOUTUBE_LIVE_CHAT_ID:-}" ]; then
		printf '%s' "$YOUTUBE_LIVE_CHAT_ID" >"$LIVE_CHAT_ID_FILE"
		printf '%s' "$YOUTUBE_LIVE_CHAT_ID"
		return 0
	fi
	if [ -s "$LIVE_CHAT_ID_FILE" ]; then
		cat "$LIVE_CHAT_ID_FILE"
		return 0
	fi
	if [ -z "${YOUTUBE_VIDEO_ID:-}" ] || [ -z "${YOUTUBE_API_KEY:-}" ]; then
		_log "poll: YOUTUBE_VIDEO_ID/YOUTUBE_LIVE_CHAT_ID and YOUTUBE_API_KEY are required"
		return 1
	fi
	local video_id key url resp chat_id
	video_id=$(_urlencode "$YOUTUBE_VIDEO_ID")
	key=$(_urlencode "$YOUTUBE_API_KEY")
	url="https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id=${video_id}&key=${key}"
	resp=$(_api_get "$url") || {
		_log "poll: videos.list failed"
		return 1
	}
	chat_id=$(python3 -c '
import json
import sys
try:
	data = json.load(sys.stdin)
except Exception:
	raise SystemExit(1)
for item in data.get("items") or []:
	chat_id = ((item.get("liveStreamingDetails") or {}).get("activeLiveChatId") or "").strip()
	if chat_id:
		print(chat_id)
		raise SystemExit(0)
raise SystemExit(1)
' <<<"$resp")
	if [ -z "$chat_id" ]; then
		_log "poll: activeLiveChatId not found"
		return 1
	fi
	printf '%s' "$chat_id" >"$LIVE_CHAT_ID_FILE"
	printf '%s' "$chat_id"
}

_append_api_messages() {
	local input_file="$1"
	[ -f "$input_file" ] || return 1
	python3 - "$input_file" "$RAW_LOG" "$PAGE_TOKEN_FILE" "$POLL_INTERVAL_FILE" <<'PY'
import json
import os
import re
import sys

input_file, raw_log, page_token_file, poll_interval_file = sys.argv[1:5]
with open(input_file, encoding="utf-8", errors="ignore") as f:
	data = json.load(f)

def clean(text: str) -> str:
	text = re.sub(r"[\x00-\x08\x0b-\x1f\r]", "", text or "")
	text = text.translate(str.maketrans("", "", "`$\\{}|;<>&"))
	text = re.sub(r"\s+", " ", text).strip()
	return text

def ignored_authors() -> set[str]:
	names = os.environ.get("YOUTUBE_IGNORE_AUTHORS", "")
	out = set()
	for name in re.split(r"[, \n\t]+", names):
		name = clean(name)
		if not name:
			continue
		out.add(name)
		out.add(name.lstrip("@"))
		out.add("@" + name.lstrip("@"))
	return out

ignored_author_names = ignored_authors()

lines = []
for item in data.get("items") or []:
	msg_id = clean(str(item.get("id") or ""))
	snippet = item.get("snippet") or {}
	author = item.get("authorDetails") or {}
	if author.get("isChatOwner") and os.environ.get("YOUTUBE_IGNORE_OWNER_MESSAGES", "0") in {"1", "true", "TRUE", "yes", "YES"}:
		continue
	display = clean(str(author.get("displayName") or "YouTube"))
	if display in ignored_author_names:
		continue
	text = clean(str(snippet.get("displayMessage") or snippet.get("textMessageDetails", {}).get("messageText") or ""))
	if not text:
		continue
	if not msg_id:
		msg_id = f"{display}:{text}"
	lines.append(f"id={msg_id}\t{display}: {text}")

if lines:
	os.makedirs(os.path.dirname(raw_log) or ".", exist_ok=True)
	with open(raw_log, "a", encoding="utf-8") as f:
		for line in lines:
			f.write(line + "\n")

token = str(data.get("nextPageToken") or "")
if token:
	with open(page_token_file, "w", encoding="utf-8") as f:
		f.write(token)

interval_ms = data.get("pollingIntervalMillis")
try:
	interval_sec = max(2, int(interval_ms) // 1000)
except Exception:
	interval_sec = 10
with open(poll_interval_file, "w", encoding="utf-8") as f:
	f.write(str(interval_sec))

print(len(lines))
for line in lines:
	print(line)
PY
}

_poll_nolock() {
	if [ "${YOUTUBE_CHAT_ENABLED:-0}" != "1" ]; then
		_log "poll: disabled (set YOUTUBE_CHAT_ENABLED=1)"
		return 0
	fi
	local chat_id key token url resp_file count
	chat_id=$(_resolve_live_chat_id) || return 1
	key=$(_urlencode "$YOUTUBE_API_KEY")
	chat_id=$(_urlencode "$chat_id")
	token=""
	[ -s "$PAGE_TOKEN_FILE" ] && token=$(cat "$PAGE_TOKEN_FILE" 2>/dev/null || true)
	url="https://www.googleapis.com/youtube/v3/liveChat/messages?liveChatId=${chat_id}&part=id,snippet,authorDetails&maxResults=${YOUTUBE_CHAT_MAX_RESULTS:-200}&key=${key}"
	if [ -n "$token" ]; then
		url="${url}&pageToken=$(_urlencode "$token")"
	fi
	resp_file=$(mktemp "$CHAT_DIR/.poll_response.XXXXXXXX")
	if ! _api_get "$url" >"$resp_file" 2>"$LAST_ERROR_FILE"; then
		_log "poll: liveChatMessages.list failed ($(head -1 "$LAST_ERROR_FILE" 2>/dev/null))"
		rm -f "$resp_file"
		return 1
	fi
	local append_out notify_line
	append_out=$(_append_api_messages "$resp_file" 2>>"$LAST_ERROR_FILE" || echo 0)
	rm -f "$resp_file"
	count=$(printf '%s\n' "$append_out" | head -n1)
	printf '%s\n' "$append_out" | tail -n +2 | while IFS= read -r notify_line; do
		[ -n "$notify_line" ] || continue
		case "$notify_line" in
			id=*"${TAB}"*) notify_line="${notify_line#*"${TAB}"}" ;;
		esac
		_notify_chat_overlay "YouTube" "$notify_line"
	done
	echo "$(date +%s)" >"$LAST_POLL_FILE"
	_log "poll: ${count:-0}件取得"
}

_poll() {
	_with_chat_lock "poll" _poll_nolock
}

_ingest_fixture_nolock() {
	local fixture="$1"
	[ -f "$fixture" ] || {
		echo "Usage: $0 ingest-fixture <youtube_api_json>" >&2
		return 1
	}
	local count
	local append_out notify_line
	append_out=$(_append_api_messages "$fixture" || echo 0)
	count=$(printf '%s\n' "$append_out" | head -n1)
	printf '%s\n' "$append_out" | tail -n +2 | while IFS= read -r notify_line; do
		[ -n "$notify_line" ] || continue
		case "$notify_line" in
			id=*"${TAB}"*) notify_line="${notify_line#*"${TAB}"}" ;;
		esac
		_notify_chat_overlay "YouTube" "[TEST/DUMMY] $notify_line" "YouTube TEST/DUMMY コメント受信"
	done
	_log "ingest-fixture: ${count:-0}件取り込み"
}

_ingest_fixture() {
	_with_chat_lock "ingest-fixture" _ingest_fixture_nolock "$1"
}

_fetch_nolock() {
	if [ -f "$RAW_LOG" ] && [ -s "$RAW_LOG" ]; then
		local scan_tmp seen_batch_tmp seen_line_batch_tmp dedup_tmp
		local skipped_by_id=0 skipped_by_sanitize=0 skipped_by_line=0 skipped_by_recent_line=0 added_count=0
		scan_tmp=$(mktemp "$CHAT_DIR/.new_scan.XXXXXXXX")
		seen_batch_tmp=$(mktemp "$CHAT_DIR/.seen_batch.XXXXXXXX")
		seen_line_batch_tmp=$(mktemp "$CHAT_DIR/.seen_line_batch.XXXXXXXX")
		dedup_tmp=$(mktemp "$CHAT_DIR/.new_dedup.XXXXXXXX")
		: >"$scan_tmp"
		: >"$seen_batch_tmp"
		: >"$seen_line_batch_tmp"
		[ -f "$SEEN_ID_FILE" ] || : >"$SEEN_ID_FILE"
		[ -f "$SEEN_LINE_HASH_FILE" ] || : >"$SEEN_LINE_HASH_FILE"
		_compact_seen_line_hashes

		while IFS= read -r raw_line; do
			[ -n "$raw_line" ] || continue
			local msg_id="" comment_line="$raw_line"
			if [[ "$raw_line" == id=*"$TAB"* ]]; then
				msg_id="${raw_line%%"$TAB"*}"
				msg_id="${msg_id#id=}"
				comment_line="${raw_line#*"$TAB"}"
				case "$msg_id" in
				''|*[!0-9A-Za-z._:-]*) msg_id="" ;;
				esac
			fi

			local clean_line=""
			clean_line=$(_sanitize_comment_line "$comment_line")
			if [ -z "$clean_line" ]; then
				skipped_by_sanitize=$((skipped_by_sanitize + 1))
				continue
			fi
			if [ -n "$msg_id" ]; then
				if grep -qxF "$msg_id" "$seen_batch_tmp" 2>/dev/null || grep -qxF "$msg_id" "$SEEN_ID_FILE" 2>/dev/null; then
					skipped_by_id=$((skipped_by_id + 1))
					continue
				fi
				echo "$msg_id" >>"$seen_batch_tmp"
			fi

			local line_hash=""
			line_hash=$(_hash_text "$clean_line")
			if [ -n "$line_hash" ]; then
				if grep -qxF "$line_hash" "$seen_line_batch_tmp" 2>/dev/null || _line_hash_recently_seen "$line_hash"; then
					skipped_by_recent_line=$((skipped_by_recent_line + 1))
					continue
				fi
				echo "$line_hash" >>"$seen_line_batch_tmp"
			fi
			echo "$clean_line" >>"$scan_tmp"
		done <"$RAW_LOG"
		: >"$RAW_LOG"

		if [ -s "$scan_tmp" ]; then
			local before_count after_count
			before_count=$(wc -l <"$scan_tmp" | tr -d ' ')
			awk 'NF && !seen[$0]++' "$scan_tmp" >"$dedup_tmp"
			after_count=$(wc -l <"$dedup_tmp" | tr -d ' ')
			skipped_by_line=$((before_count - after_count))
			if [ "$after_count" -gt 0 ]; then
				cat "$dedup_tmp" >>"$PENDING_LOG"
				added_count="$after_count"
			fi
		fi
		if [ -s "$seen_batch_tmp" ]; then
			cat "$seen_batch_tmp" >>"$SEEN_ID_FILE"
			_compact_seen_ids
		fi
		if [ -s "$seen_line_batch_tmp" ]; then
			local seen_line_ts
			seen_line_ts=$(date +%s)
			while IFS= read -r line_hash; do
				[ -n "$line_hash" ] || continue
				printf '%s|%s\n' "$seen_line_ts" "$line_hash" >>"$SEEN_LINE_HASH_FILE"
			done <"$seen_line_batch_tmp"
			_compact_seen_line_hashes
		fi
		rm -f "$scan_tmp" "$seen_batch_tmp" "$seen_line_batch_tmp" "$dedup_tmp"
		if [ "${added_count:-0}" -gt 0 ] || [ "$skipped_by_id" -gt 0 ] || [ "$skipped_by_recent_line" -gt 0 ]; then
			_log "fetch: ${added_count}件追加 (id重複:${skipped_by_id}, 内容重複:${skipped_by_line}, 履歴重複:${skipped_by_recent_line}, sanitize除外:${skipped_by_sanitize})"
		fi
	fi

	if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
		local before_count after_count pending_tmp
		before_count=$(wc -l <"$PENDING_LOG" | tr -d ' ')
		pending_tmp=$(mktemp "$CHAT_DIR/.pending_dedup.XXXXXXXX")
		awk 'NF && !seen[$0]++' "$PENDING_LOG" >"$pending_tmp"
		cat "$pending_tmp" >"$PENDING_LOG"
		rm -f "$pending_tmp"
		after_count=$(wc -l <"$PENDING_LOG" | tr -d ' ')
		if [ "${after_count:-0}" -lt "${before_count:-0}" ]; then
			_log "fetch: pending重複を$((before_count - after_count))件除去"
		fi
		head -10 "$PENDING_LOG" >"$OUTFILE"
		_log "fetch: pending $(wc -l <"$OUTFILE" | tr -d ' ')件を出力"
	else
		rm -f "$OUTFILE"
		_log "fetch: 未読コメントなし"
	fi
}

_fetch() {
	_with_chat_lock "fetch" _fetch_nolock
}

_ack_batch_nolock() {
	local batch_file="$1"
	if [ -z "$batch_file" ] || [ ! -f "$batch_file" ]; then
		_log "ack_batch: batch fileが見つかりません"
		return 0
	fi
	if [ ! -f "$PENDING_LOG" ] || [ ! -s "$PENDING_LOG" ]; then
		_log "ack_batch: pending.logは空"
		return 0
	fi
	local batch_tmp out_tmp before_count after_count removed_count
	batch_tmp=$(mktemp "$CHAT_DIR/.ack_batch.XXXXXXXX")
	out_tmp=$(mktemp "$CHAT_DIR/.ack_out.XXXXXXXX")
	awk 'NF && !seen[$0]++' "$batch_file" >"$batch_tmp"
	if [ ! -s "$batch_tmp" ]; then
		rm -f "$batch_tmp" "$out_tmp"
		_log "ack_batch: 対象行なし"
		return 0
	fi
	before_count=$(wc -l <"$PENDING_LOG" | tr -d ' ')
	grep -vxF -f "$batch_tmp" "$PENDING_LOG" >"$out_tmp" || true
	cat "$out_tmp" >"$PENDING_LOG"
	after_count=$(wc -l <"$PENDING_LOG" | tr -d ' ')
	removed_count=$((before_count - after_count))
	_log "ack_batch: ${removed_count}件をpendingから削除"
	rm -f "$batch_tmp" "$out_tmp"
}

_ack_batch() {
	_with_chat_lock "ack_batch" _ack_batch_nolock "$1"
}

_oauth_access_token() {
	[ -n "${YOUTUBE_OAUTH_CLIENT_ID:-}" ] && [ -n "${YOUTUBE_OAUTH_CLIENT_SECRET:-}" ] && [ -n "${YOUTUBE_OAUTH_REFRESH_TOKEN:-}" ] || return 1
	curl -fsS --max-time "${YOUTUBE_API_TIMEOUT_SEC:-12}" \
		--data-urlencode "client_id=${YOUTUBE_OAUTH_CLIENT_ID}" \
		--data-urlencode "client_secret=${YOUTUBE_OAUTH_CLIENT_SECRET}" \
		--data-urlencode "refresh_token=${YOUTUBE_OAUTH_REFRESH_TOKEN}" \
		-d "grant_type=refresh_token" \
		"https://oauth2.googleapis.com/token" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))'
}

_send() {
	local msg="$1"
	[ -n "$msg" ] || {
		echo "Usage: $0 send <message>" >&2
		return 1
	}
	if [ "${YOUTUBE_CHAT_SEND_ENABLED:-0}" != "1" ]; then
		echo "YouTube send disabled (set YOUTUBE_CHAT_SEND_ENABLED=1)" >&2
		return 1
	fi
	local chat_id access_token payload_file resp_file insert_url
	chat_id=$(_resolve_live_chat_id) || return 1
	access_token=$(_oauth_access_token) || {
		echo "YouTube OAuth refresh settings are missing or invalid" >&2
		return 1
	}
	payload_file=$(mktemp "$CHAT_DIR/.send_payload.XXXXXXXX")
	resp_file=$(mktemp "$CHAT_DIR/.send_response.XXXXXXXX")
	python3 - "$chat_id" "$msg" >"$payload_file" <<'PY'
import json
import sys
chat_id, msg = sys.argv[1:3]
msg = " ".join(msg.split())
if len(msg.encode("utf-8")) > 200:
	data = msg.encode("utf-8")[:197]
	while True:
		try:
			msg = data.decode("utf-8")
			break
		except UnicodeDecodeError:
			data = data[:-1]
	msg = msg.rstrip() + "..."
print(json.dumps({
	"snippet": {
		"liveChatId": chat_id,
		"type": "textMessageEvent",
		"textMessageDetails": {"messageText": msg},
	}
}, ensure_ascii=False))
PY
	insert_url="https://www.googleapis.com/youtube/v3/liveChat/messages?part=snippet"
	if [ -n "${YOUTUBE_API_KEY:-}" ]; then
		insert_url="${insert_url}&key=$(_urlencode "$YOUTUBE_API_KEY")"
	fi
	if curl -fsS --max-time "${YOUTUBE_API_TIMEOUT_SEC:-12}" \
		-X POST "$insert_url" \
		-H "Authorization: Bearer ${access_token}" \
		-H "Content-Type: application/json; charset=UTF-8" \
		--data-binary "@${payload_file}" >"$resp_file"; then
		rm -f "$payload_file" "$resp_file"
		return 0
	fi
	head -5 "$resp_file" >&2 2>/dev/null || true
	rm -f "$payload_file" "$resp_file"
	return 1
}

_status() {
	local pending=0 raw=0 poll_interval="-" effective_poll_interval="-" last_poll="-"
	[ -f "$PENDING_LOG" ] && pending=$(wc -l <"$PENDING_LOG" 2>/dev/null | tr -d ' ')
	[ -f "$RAW_LOG" ] && raw=$(wc -l <"$RAW_LOG" 2>/dev/null | tr -d ' ')
	[ -f "$POLL_INTERVAL_FILE" ] && poll_interval=$(cat "$POLL_INTERVAL_FILE" 2>/dev/null || echo "-")
	effective_poll_interval="$poll_interval"
	if [ "${effective_poll_interval:-0}" -lt "${YOUTUBE_CHAT_POLL_INTERVAL_SEC:-10}" ] 2>/dev/null; then
		effective_poll_interval="${YOUTUBE_CHAT_POLL_INTERVAL_SEC:-10}"
	fi
	[ -f "$LAST_POLL_FILE" ] && last_poll=$(cat "$LAST_POLL_FILE" 2>/dev/null || echo "-")
	echo "enabled=${YOUTUBE_CHAT_ENABLED:-0} raw=${raw:-0} pending=${pending:-0} api_poll_interval=${poll_interval}s effective_poll_interval=${effective_poll_interval}s last_poll=${last_poll}"
}

case "$CMD" in
	poll) _poll ;;
	fetch) _fetch ;;
	ack-batch) _ack_batch "$2" ;;
	send) _send "$2" ;;
	ingest-fixture) _ingest_fixture "$2" ;;
	status) _status ;;
	*) echo "Usage: $0 {poll|fetch|ack-batch|send|ingest-fixture|status} [file|message]" >&2; exit 1 ;;
esac
