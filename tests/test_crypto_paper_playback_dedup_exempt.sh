#!/usr/bin/env bash
# Regression for the 2026-09-18 PAPER corner outage: every crypto_paper
# (docich PAPER corner) audio delivery in a 10-minute manual test was
# silently dropped as a "重複スキップ" (duplicate) by _play_comment_queue's
# content-hash dedupe, even though each delivery was a genuinely new,
# once-only announcement (dedupe already happens upstream, per event_id, in
# docich's _enqueue_audio_delivery). crypto_paper deliveries must play even
# when their raw text is byte-identical to an earlier delivery (deterministic
# fallback narration legitimately repeats when trading facts/data don't
# change); ordinary comment playback must still be deduped as before.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

cd "$TMP" || exit 1
export ELOOP_LIB_DIR="$ROOT"
export COMMENT_QUEUE_DIR="$TMP/comment_queue"
export COMMENT_VIEWER_MEMORY_ENABLED=0
export COMMENT_SPOKEN_HISTORY_DIR="$TMP/spoken_history"
export COMMENT_SPOKEN_HISTORY_MAX_FILES=10
# COMMENT_PLAYED_HASHES_FILE is a fixed relative path in comment.sh
# (not derived from COMMENT_QUEUE_DIR), so its parent must exist under cwd
# regardless of where COMMENT_QUEUE_DIR itself points.
mkdir -p "$COMMENT_QUEUE_DIR" "tmp/.say_queue" "tmp/.comment_queue"
_cp_my_pid="test"
RADIO_SAY_RATE=""

log() { :; }
_clean_comment_talk() { cat; }
_sanitize_onair_text() { cat; }
_broadcast_read_expected_mode() { :; }
_broadcast_host_mode() { printf '%s' main; }
_broadcast_clear_expected_mode() { :; }
_play_deferred_radio_queue_once() { :; }

cat >say_enqueue.sh <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x say_enqueue.sh

source "$ROOT/broadcast/comment.sh"
source "$ROOT/broadcast/comment_lib.sh"

# --- _comment_playback_context_label: crypto_paper filename recognition ---
check '[ "$(_comment_playback_context_label "x/comment_announce_1_ab_crypto_paper.txt")" = "crypto_paper" ]' \
	'crypto_paper のqueueファイル名がlabel crypto_paperに分類される'
check '[ "$(_comment_playback_context_label "x/comment_announce_1_ab_crypto_paper.playing")" = "crypto_paper" ]' \
	'.playingへリネーム後も label crypto_paper のまま'

# --- crypto_paper deliveries: identical text must NOT be duplicate-skipped ---
same_text='時間足チャートの解説です。今回は公開ローソクの取得が間に合わず、数字をお伝えできません。'
printf '%s\n' "$same_text" >"$COMMENT_QUEUE_DIR/comment_announce_1_aaa_crypto_paper.txt"
_play_comment_queue
printf '%s\n' "$same_text" >"$COMMENT_QUEUE_DIR/comment_announce_2_bbb_crypto_paper.txt"
_play_comment_queue

check '! grep -q "重複スキップ" tmp/.say_queue/debug.log 2>/dev/null' \
	'crypto_paper は同一本文でも重複スキップされない'
check '[ "$(grep -c "再生開始" tmp/.say_queue/debug.log 2>/dev/null)" -eq 2 ]' \
	'crypto_paper の2件とも再生開始まで進む'
check '[ -z "$(find "$COMMENT_QUEUE_DIR" -maxdepth 1 -name "*crypto_paper*" 2>/dev/null)" ]' \
	'crypto_paper のqueueファイルが両方消費される(取りこぼしなし)'

# --- fixed terminal deliveries: same body, distinct batch; same batch once ---
source "$ROOT/lib/outbound_queue.sh"
: >tmp/.say_queue/debug.log
first=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
second=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
terminal='資料を確認できませんでした。詳しい内容はまだ断定できません。'
check '_comment_audio_claim_enqueue_key "$terminal" "twitch:$first"' '最初のterminal batchは投入可能'
check '_comment_audio_claim_enqueue_key "$terminal" "twitch:$second"' '異なるbatchの同本文は投入可能'
check '! _comment_audio_claim_enqueue_key "$terminal" "twitch:$first"' '同batchの並行投入は拒否'
for item in "${first}_1_1" "${second}_2_2" "${first}_3_3"; do
    printf '%s\n' "$terminal" >"$COMMENT_QUEUE_DIR/comment_terminal_twitch_${item}.txt"
    _play_comment_queue
done
check '[ "$(grep -c "再生開始" tmp/.say_queue/debug.log)" -eq 2 ]' '異なるterminal2batchは再生、同batch再配達は再生しない'
check '[ "$(grep -c "重複スキップ" tmp/.say_queue/debug.log)" -eq 1 ]' 'terminal再生dedupはbatch単位'

# --- real consumer rejection: retry only after failure, complete only on success ---
cat >say_enqueue.sh <<'SH'
#!/usr/bin/env bash
printf 'attempt\n' >>say-attempts
[ -e say-success ] || exit 42
exit 0
SH
chmod +x say_enqueue.sh
third=cccccccccccccccccccccccccccccccc
retry_file="$COMMENT_QUEUE_DIR/comment_terminal_twitch_${third}_4_4.txt"
printf '%s\n' "$terminal" >"$retry_file"
meta=$(_comment_meta_sidecar_path "$retry_file")
printf '{"mode":"main","model":"fixture"}\n' >"$meta"
retry_key=$(_outbound_chat_hash "terminal-batch:twitch:$third")
_play_comment_queue
check '[ -f "$retry_file" ] && [ -f "$meta" ]' 'say exit42後は同じterminal queueとmetadataを保持'
check '! grep -qF "$retry_key" "$COMMENT_PLAYED_HASHES_FILE"' 'say失敗を完了hashに記録しない'
_play_comment_queue
check '[ "$(wc -l <say-attempts | tr -d " ")" -eq 2 ] && [ -f "$retry_file" ]' '次tickで実consumerを再試行'
touch say-success
_play_comment_queue
check '[ "$(wc -l <say-attempts | tr -d " ")" -eq 3 ] && [ ! -f "$retry_file" ]' 'consumer成功後にqueueを消費'
check 'grep -qF "$retry_key" "$COMMENT_PLAYED_HASHES_FILE"' 'consumer成功後に完了hashを記録'
printf '%s\n' "$terminal" >"$COMMENT_QUEUE_DIR/comment_terminal_twitch_${third}_5_5.txt"
_play_comment_queue
check '[ "$(wc -l <say-attempts | tr -d " ")" -eq 3 ]' '成功確認済の同batch再配送はconsumerを再実行しない'

# --- control: ordinary (non-exempt) comment playback still dedupes ---
: >tmp/.say_queue/debug.log
printf '%s\n' "同じ内容のコメント返信テストです。" >"$COMMENT_QUEUE_DIR/comment_ordinary_1.txt"
_play_comment_queue
printf '%s\n' "同じ内容のコメント返信テストです。" >"$COMMENT_QUEUE_DIR/comment_ordinary_2.txt"
_play_comment_queue

check 'grep -q "重複スキップ" tmp/.say_queue/debug.log 2>/dev/null' \
	'通常コメントは同一本文なら引き続き重複スキップされる(既存挙動の回帰確認)'

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
