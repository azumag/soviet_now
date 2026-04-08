# lib/outbound_queue.sh - Twitch outbound chat queue helpers
#
# 全 worker は Twitch に直接投稿せず、この queue にメッセージを積む。
# 送信は chat_worker (暫定: twitch_chat_daemon.sh) が一元的に行う。

OUTBOUND_CHAT_QUEUE_DIR="${OUTBOUND_CHAT_QUEUE_DIR:-tmp/.outbound_chat_queue}"
OUTBOUND_CHAT_PENDING_DIR="$OUTBOUND_CHAT_QUEUE_DIR/pending"
OUTBOUND_CHAT_SENT_DIR="$OUTBOUND_CHAT_QUEUE_DIR/sent"

mkdir -p "$OUTBOUND_CHAT_PENDING_DIR" "$OUTBOUND_CHAT_SENT_DIR" 2>/dev/null || true

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
#   送信には ./twitch_chat.sh send を使用。
#   戻り値: 0=送信成功, 1=キューが空 or 送信失敗
outbound_queue_consume_once() {
	local msg_file
	msg_file=$(ls -1t "$OUTBOUND_CHAT_PENDING_DIR"/*.msg 2>/dev/null | tail -1)
	[ -n "$msg_file" ] && [ -f "$msg_file" ] || return 1

	local message
	message=$(cat "$msg_file" 2>/dev/null)
	[ -n "$message" ] || { rm -f "$msg_file"; return 1; }

	local basename
	basename=$(basename "$msg_file")

	if ./twitch_chat.sh send "$message" >/dev/null 2>&1; then
		mv "$msg_file" "$OUTBOUND_CHAT_SENT_DIR/$basename" 2>/dev/null || rm -f "$msg_file"
		return 0
	else
		# 送信失敗: ファイルは pending に残す (次回リトライ)
		return 1
	fi
}

# outbound_queue_cleanup_sent [MAX_AGE_SEC]
#   sent/ 内の古いファイルを削除する。
#   MAX_AGE_SEC: default 3600 (1時間)
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
