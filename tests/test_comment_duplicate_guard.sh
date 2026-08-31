#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export COMMENT_QUEUE_DIR="$TMP/comment_queue"
export COMMENT_AUDIO_DEDUP_DIR="$TMP/audio_dedup"
export COMMENT_AUDIO_DEDUP_TTL_SEC=300
export OUTBOUND_CHAT_QUEUE_DIR="$TMP/outbound_chat"
export COMMENT_SPOKEN_HISTORY_DIR="$TMP/spoken_history"
export COMMENT_SPOKEN_HISTORY_MAX_FILES=10
mkdir -p "$COMMENT_QUEUE_DIR"

source "$ROOT/lib/outbound_queue.sh"
source "$ROOT/broadcast/comment.sh"

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

text='同じ本文のハッシュを確認します。'
if command -v md5sum >/dev/null 2>&1; then
	expected_hash=$(printf '%s' "$text" | md5sum | awk '{print $1}')
else
	expected_hash=$(printf '%s' "$text" | md5 -q)
fi
actual_hash=$(_comment_hash_text "$text")
check '[ -n "$actual_hash" ]' 'コメント本文のハッシュが空にならない'
check '[ "$actual_hash" = "$expected_hash" ]' 'コメント本文のハッシュが環境に依存せず一致する'

printf '%s\n' "$text" >"$TMP/reply.txt"
actual_file_hash=$(_comment_hash_file "$TMP/reply.txt")
if command -v md5sum >/dev/null 2>&1; then
	expected_file_hash=$(printf '%s\n' "$text" | md5sum | awk '{print $1}')
else
	expected_file_hash=$(printf '%s\n' "$text" | md5 -q)
fi
check "[ \"$actual_file_hash\" = \"$expected_file_hash\" ]" 'コメントファイルのハッシュが取得できる'

check '_comment_audio_claim_enqueue_key "重複本文"' '同じ本文の初回投入権を取得できる'
if _comment_audio_claim_enqueue_key "重複本文"; then
	printf 'not ok - 同じ本文の二回目の投入権を拒否する\n'
	fail=$((fail + 1))
else
	printf 'ok - 同じ本文の二回目の投入権を拒否する\n'
	ok=$((ok + 1))
fi

enqueue_audio_text "キュー投入の重複確認" test >/dev/null
enqueue_audio_text "キュー投入の重複確認" test >/dev/null
queue_count=$(find "$COMMENT_QUEUE_DIR" -maxdepth 1 -type f -name '*.txt' | wc -l | tr -d ' ')
check '[ "$queue_count" -eq 1 ]' '音声キューが同一本文を一件だけ保持する'

check 'grep -q "_comment_audio_claim_enqueue_key.*attempt_talk" "$ROOT/broadcast/comment.sh"' '直接生成するコメントにも本文重複ガードがある'

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
