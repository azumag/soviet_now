# lib/outbound_queue.sh - outbound chat queue helpers
#
# 全 worker は配信チャットに直接投稿せず、この queue にメッセージを積む。
# 送信は chat_worker (暫定: twitch_chat_daemon.sh) が一元的に行う。

OUTBOUND_CHAT_QUEUE_DIR="${OUTBOUND_CHAT_QUEUE_DIR:-tmp/.outbound_chat_queue}"
OUTBOUND_CHAT_PENDING_DIR="$OUTBOUND_CHAT_QUEUE_DIR/pending"
OUTBOUND_CHAT_PROCESSING_DIR="$OUTBOUND_CHAT_QUEUE_DIR/processing"
OUTBOUND_CHAT_SENT_DIR="$OUTBOUND_CHAT_QUEUE_DIR/sent"
OUTBOUND_CHAT_DEDUP_DIR="$OUTBOUND_CHAT_QUEUE_DIR/dedup"

mkdir -p "$OUTBOUND_CHAT_PENDING_DIR" "$OUTBOUND_CHAT_PROCESSING_DIR" "$OUTBOUND_CHAT_SENT_DIR" "$OUTBOUND_CHAT_DEDUP_DIR" 2>/dev/null || true

_outbound_chat_hash() {
	if command -v md5 >/dev/null 2>&1; then
		printf '%s' "$1" | md5 -q 2>/dev/null
	else
		printf '%s' "$1" | md5sum 2>/dev/null | awk '{print $1}'
	fi
}

_outbound_chat_truthy() {
	case "${1:-}" in
	1|true|TRUE|yes|YES|on|ON) return 0 ;;
	esac
	return 1
}

_outbound_chat_load_env_for_youtube_mirror() {
	[ -f .env ] || return 0
	set -a
	. ./.env
	set +a
}

_outbound_chat_youtube_mirror_enabled() {
	if [ -n "${OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED:-}" ]; then
		_outbound_chat_truthy "$OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED"
		return $?
	fi
	_outbound_chat_truthy "${YOUTUBE_CHAT_SEND_ENABLED:-0}"
}

_outbound_chat_youtube_mirror_configured() {
	_outbound_chat_load_env_for_youtube_mirror
	_outbound_chat_youtube_mirror_enabled || return 1
	[ -n "${YOUTUBE_OAUTH_CLIENT_ID:-}" ] || return 1
	[ -n "${YOUTUBE_OAUTH_CLIENT_SECRET:-}" ] || return 1
	[ -n "${YOUTUBE_OAUTH_REFRESH_TOKEN:-}" ] || return 1
	return 0
}

_outbound_chat_youtube_backoff_active() {
	local backoff_file="${YOUTUBE_CHAT_DIR:-tmp/.youtube_chat}/api_backoff_until"
	local until now
	[ -f "$backoff_file" ] || return 1
	until=$(cat "$backoff_file" 2>/dev/null || true)
	case "$until" in
		''|*[!0-9]*) return 1 ;;
	esac
	now=$(date +%s)
	[ "$until" -gt "$now" ]
}

_outbound_chat_log_youtube_mirror_failure() {
	local basename="$1"
	local err_file="$2"
	local log_dir="${TMP_DEBUG_DIR:-tmp/debug}"
	local log_file="$log_dir/outbound_chat_youtube.log"
	mkdir -p "$log_dir" 2>/dev/null || true
	{
		printf '[%s] youtube mirror failed: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$basename"
		if [ -f "$err_file" ]; then
			head -5 "$err_file" 2>/dev/null
		fi
	} >>"$log_file" 2>/dev/null || true
}

_outbound_chat_log_twitch_failure() {
	local basename="$1"
	local err_file="$2"
	local log_dir="${TMP_DEBUG_DIR:-tmp/debug}"
	local log_file="$log_dir/outbound_chat_twitch.log"
	mkdir -p "$log_dir" 2>/dev/null || true
	{
		printf '[%s] twitch send failed: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$basename"
		if [ -f "$err_file" ]; then
			head -8 "$err_file" 2>/dev/null
		fi
	} >>"$log_file" 2>/dev/null || true
	cp "$err_file" "$log_dir/last_twitch_send_error.txt" 2>/dev/null || true
}

_outbound_chat_twitch_backoff_file() {
	printf '%s/twitch_backoff_until\n' "$OUTBOUND_CHAT_QUEUE_DIR"
}

_outbound_chat_twitch_backoff_count_file() {
	printf '%s/twitch_backoff_count\n' "$OUTBOUND_CHAT_QUEUE_DIR"
}

_outbound_chat_twitch_backoff_active() {
	local backoff_file until now
	backoff_file=$(_outbound_chat_twitch_backoff_file)
	[ -f "$backoff_file" ] || return 1
	until=$(cat "$backoff_file" 2>/dev/null || true)
	case "$until" in
		''|*[!0-9]*) return 1 ;;
	esac
	now=$(date +%s)
	[ "$until" -gt "$now" ]
}

_outbound_chat_maybe_backoff_twitch_failure() {
	local err_file="$1"
	[ -f "$err_file" ] || return 0
	if grep -Eiq "Invalid OAuth token|Login authentication failed|Improperly formatted auth|Error logging in" "$err_file" 2>/dev/null; then
		local backoff_base="${OUTBOUND_CHAT_TWITCH_AUTH_BACKOFF_SEC:-60}"
		local backoff_max="${OUTBOUND_CHAT_TWITCH_AUTH_BACKOFF_MAX_SEC:-900}"
		case "$backoff_base" in
			''|*[!0-9]*) backoff_base=60 ;;
		esac
		case "$backoff_max" in
			''|*[!0-9]*) backoff_max=900 ;;
		esac
		[ "$backoff_max" -ge "$backoff_base" ] || backoff_max="$backoff_base"
		local count_file count backoff_sec
		count_file=$(_outbound_chat_twitch_backoff_count_file)
		count=$(cat "$count_file" 2>/dev/null || echo 0)
		case "$count" in
			''|*[!0-9]*) count=0 ;;
		esac
		count=$((count + 1))
		backoff_sec=$((backoff_base * count))
		[ "$backoff_sec" -le "$backoff_max" ] || backoff_sec="$backoff_max"
		mkdir -p "$OUTBOUND_CHAT_QUEUE_DIR" 2>/dev/null || true
		printf '%s\n' "$(( $(date +%s) + backoff_sec ))" > "$(_outbound_chat_twitch_backoff_file)" 2>/dev/null || true
		printf '%s\n' "$count" > "$count_file" 2>/dev/null || true
	fi
}

_outbound_chat_clear_twitch_failure_state() {
	local log_dir="${TMP_DEBUG_DIR:-tmp/debug}"
	rm -f "$(_outbound_chat_twitch_backoff_file)" "$(_outbound_chat_twitch_backoff_count_file)" "$log_dir/last_twitch_send_error.txt" 2>/dev/null || true
}

_outbound_chat_source_from_basename() {
	local basename="$1"
	basename="${basename%.msg}"
	basename="${basename#*_}"
	printf '%s' "${basename%_*}"
}

_outbound_chat_source_is_youtube_mirror_excluded() {
	local source="$1"
	local excludes="${OUTBOUND_CHAT_YOUTUBE_MIRROR_EXCLUDE_SOURCES:-}"
	local item
	for item in $excludes; do
		[ "$source" = "$item" ] && return 0
	done
	return 1
}

_outbound_chat_send_youtube_mirror() {
	local message="$1"
	local basename="$2"
	_outbound_chat_youtube_mirror_configured || return 0
	[ -x ./youtube_chat.sh ] || return 0
	# Poll/send share the same YouTube quota backoff. Avoid retrying every Twitch
	# outbound message while the mirror is known to be unavailable.
	_outbound_chat_youtube_backoff_active && return 0
	local source
	source=$(_outbound_chat_source_from_basename "$basename")
	_outbound_chat_source_is_youtube_mirror_excluded "$source" && return 0

	local err_file
	err_file=$(mktemp "${OUTBOUND_CHAT_QUEUE_DIR}/.youtube_send_err.XXXXXXXX" 2>/dev/null || echo "${OUTBOUND_CHAT_QUEUE_DIR}/.youtube_send_err_${RANDOM}")
	if ./youtube_chat.sh send "$message" >/dev/null 2>"$err_file"; then
		rm -f "$err_file"
		return 0
	fi
	_outbound_chat_log_youtube_mirror_failure "$basename" "$err_file"
	rm -f "$err_file"
	return 0
}

_outbound_chat_cleanup_dedup_markers() {
	local ttl="${1:-30}"
	local now marker mt age
	case "$ttl" in
	''|*[!0-9]*) return 0 ;;
	esac
	now=$(date +%s)
	for marker in "$OUTBOUND_CHAT_DEDUP_DIR"/*; do
		[ -d "$marker" ] || continue
		mt=$(stat -f %m "$marker" 2>/dev/null || echo "$now")
		age=$((now - mt))
		[ "$age" -gt "$ttl" ] && rm -rf "$marker" 2>/dev/null || true
	done
}

_outbound_chat_claim_enqueue_key() {
	local message="$1"
	local source="$2"
	local priority="$3"
	local ttl="${OUTBOUND_CHAT_ENQUEUE_DEDUP_TTL_SEC:-30}"
	case "$ttl" in
	''|*[!0-9]*) ttl=30 ;;
	esac
	[ "$ttl" -gt 0 ] || return 0

	mkdir -p "$OUTBOUND_CHAT_DEDUP_DIR" 2>/dev/null || true
	local key marker now mt age
	key=$(_outbound_chat_hash "${source}"$'\037'"${priority}"$'\037'"${message}")
	[ -n "$key" ] || return 0
	marker="$OUTBOUND_CHAT_DEDUP_DIR/$key"
	now=$(date +%s)
	if mkdir "$marker" 2>/dev/null; then
		printf '%s\n' "$now" > "$marker/ts" 2>/dev/null || true
		return 0
	fi

	mt=$(stat -f %m "$marker" 2>/dev/null || echo "$now")
	age=$((now - mt))
	if [ "$age" -le "$ttl" ]; then
		return 1
	fi
	rm -rf "$marker" 2>/dev/null || true
	mkdir "$marker" 2>/dev/null || return 1
	printf '%s\n' "$now" > "$marker/ts" 2>/dev/null || true
	_outbound_chat_cleanup_dedup_markers "$ttl" 2>/dev/null || true
	return 0
}

# enqueue_chat_message MESSAGE [SOURCE] [PRIORITY]
#   MESSAGE  : 投稿する本文 (1行)
#   SOURCE   : 呼び出し元の識別子 (default: "unknown")
#   PRIORITY : 数値 (小さいほど高優先, default: 5)
#
# ファイル名: {epoch_ns}_{source}_{priority}.msg
# 内容: メッセージ本文のみ
enqueue_chat_message() {
	local message="${1:-}"
	local source="${2:-unknown}"
	local priority="${3:-5}"

	if [ -z "$message" ]; then
		return 1
	fi

	if ! _outbound_chat_claim_enqueue_key "$message" "$source" "$priority"; then
		return 0
	fi

	mkdir -p "$OUTBOUND_CHAT_PENDING_DIR" 2>/dev/null || true

	# nanosecond timestamp for uniqueness (fallback to epoch + random)
	local ts
	ts=$(date +%s%N 2>/dev/null || echo "$(date +%s)${RANDOM}")

	local filename="${ts}_${source}_${priority}.msg"
	local tmpfile="${OUTBOUND_CHAT_PENDING_DIR}/.${filename}.tmp"
	local destfile="${OUTBOUND_CHAT_PENDING_DIR}/${filename}"

	# atomic write: tmp → rename
	printf '%s\n' "$message" > "$tmpfile" 2>/dev/null || return 1
	mv "$tmpfile" "$destfile" 2>/dev/null || return 1
	return 0
}

# outbound_queue_consume_once
#   pending/ から最も古い1件を取得して送信し、sent/ に移動する。
#   Twitch 送信には ./twitch_chat.sh send を使用。
#   YOUTUBE_CHAT_SEND_ENABLED=1 か OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED=1 の場合、
#   Twitch 送信成功後に ./youtube_chat.sh send にも同じ本文を送る。
#   戻り値: 0=送信成功, 1=キューが空 or 送信失敗
outbound_queue_consume_once() {
	mkdir -p "$OUTBOUND_CHAT_PENDING_DIR" "$OUTBOUND_CHAT_PROCESSING_DIR" "$OUTBOUND_CHAT_SENT_DIR" 2>/dev/null || true
	_outbound_chat_twitch_backoff_active && return 1

	local msg_file
	msg_file=$(ls -1t "$OUTBOUND_CHAT_PENDING_DIR"/*.msg 2>/dev/null | tail -1)
	[ -n "$msg_file" ] && [ -f "$msg_file" ] || return 1

	local basename claim_file
	basename=$(basename "$msg_file")
	claim_file="$OUTBOUND_CHAT_PROCESSING_DIR/$basename"
	if ! mv "$msg_file" "$claim_file" 2>/dev/null; then
		return 1
	fi

	local message
	message=$(cat "$claim_file" 2>/dev/null)
	[ -n "$message" ] || { rm -f "$claim_file"; return 1; }

	local err_file
	err_file=$(mktemp "${OUTBOUND_CHAT_QUEUE_DIR}/.twitch_send_err.XXXXXXXX" 2>/dev/null || echo "${OUTBOUND_CHAT_QUEUE_DIR}/.twitch_send_err_${RANDOM}")
	if ./twitch_chat.sh send "$message" >/dev/null 2>"$err_file"; then
		rm -f "$err_file"
		_outbound_chat_clear_twitch_failure_state
		_outbound_chat_send_youtube_mirror "$message" "$basename"
		mv "$claim_file" "$OUTBOUND_CHAT_SENT_DIR/$basename" 2>/dev/null || rm -f "$claim_file"
		return 0
	else
		_outbound_chat_log_twitch_failure "$basename" "$err_file"
		_outbound_chat_maybe_backoff_twitch_failure "$err_file"
		rm -f "$err_file"
		# 送信失敗: pending に戻す (次回リトライ)
		mv "$claim_file" "$OUTBOUND_CHAT_PENDING_DIR/$basename" 2>/dev/null || true
		return 1
	fi
}

# outbound_queue_cleanup_sent [MAX_AGE_SEC]
#   sent/ 内の古いファイルを削除する。
#   MAX_AGE_SEC: default 3600 (1時間)
# === Audio playback queue ===
# audio_worker が消化する comment queue にファイルを積む。
# soren_loop / eloop / improve から直接 say_enqueue.sh を呼ばず、
# この関数で queue に書いて audio_worker に再生を委譲する。

# enqueue_audio_text TEXT [SOURCE] [SPEAKER_OVERRIDE]
#   テキストを comment queue に積む。audio_worker の _play_comment_queue が再生する。
enqueue_audio_text() {
	local text="${1:-}"
	local source="${2:-unknown}"
	local speaker_override="${3:-}"
	[ -n "$text" ] || return 1

	local queue_dir="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
	mkdir -p "$queue_dir" 2>/dev/null || true

	local ts
	ts=$(date +%s%N 2>/dev/null || echo "$(date +%s)${RANDOM}")
	local filename="comment_announce_${ts}_${source}.txt"
	local tmpfile="${queue_dir}/.${filename}.tmp"
	local destfile="${queue_dir}/${filename}"

	printf '%s\n' "$text" > "$tmpfile" 2>/dev/null || return 1
	mv "$tmpfile" "$destfile" 2>/dev/null || return 1

	# speaker override が指定されていたらサイドカーファイルに保存
	if [ -n "$speaker_override" ]; then
		printf '%s' "$speaker_override" > "${destfile}.speaker" 2>/dev/null || true
	fi
	return 0
}

# enqueue_audio_file FILE [SOURCE] [SPEAKER_OVERRIDE]
#   既存のテキストファイルを comment queue にコピーして積む。
enqueue_audio_file() {
	local file="${1:-}"
	local source="${2:-unknown}"
	local speaker_override="${3:-}"
	[ -f "$file" ] && [ -s "$file" ] || return 1

	local queue_dir="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
	mkdir -p "$queue_dir" 2>/dev/null || true

	local ts
	ts=$(date +%s%N 2>/dev/null || echo "$(date +%s)${RANDOM}")
	local filename="comment_announce_${ts}_${source}.txt"
	local destfile="${queue_dir}/${filename}"

	cp "$file" "$destfile" 2>/dev/null || return 1

	if [ -n "$speaker_override" ]; then
		printf '%s' "$speaker_override" > "${destfile}.speaker" 2>/dev/null || true
	fi
	return 0
}

outbound_queue_cleanup_sent() {
	local max_age="${1:-3600}"
	local now
	now=$(date +%s)
	for f in "$OUTBOUND_CHAT_SENT_DIR"/*.msg; do
		[ -f "$f" ] || continue
		local mtime
		mtime=$(stat -f %m "$f" 2>/dev/null || echo "$now")
		local age=$((now - mtime))
		if [ "$age" -gt "$max_age" ]; then
			rm -f "$f"
		fi
	done
}
