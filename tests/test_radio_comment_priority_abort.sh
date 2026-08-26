#!/usr/bin/env bash
# コメントが溜まっている間、ラジオ(ニュース)の事前合成を合成中でも打ち切り、
# 合成済みチャンクは捨てずに再開できることを検証する。
# say_enqueue.sh / broadcast/radio_state.sh から必要な関数だけを抽出して実行する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SAY_SRC="$ROOT/say_enqueue.sh"
RADIO_SRC="$ROOT/broadcast/radio_state.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() {
	echo "not ok - $1"
	FAIL=1
}

for fn in \
	_hash_content_file \
	_comment_backlog_pending \
	_radio_render_should_abort_for_comment \
	_voicevox_synth_is_background_render \
	_kill_process_tree \
	_synthesize_chunk_yielding; do
	sed -n "/^${fn}()/,/^}/p" "$SAY_SRC" >>"$TMP/say_fns.sh"
done
. "$TMP/say_fns.sh"

_log() { :; }
_touch_voicevox_synth_lock_heartbeat() { :; }

cd "$TMP" || exit 1
export COMMENT_QUEUE_DIR="$TMP/.comment_queue"
mkdir -p "$COMMENT_QUEUE_DIR"

# --- 1: コメント滞留の検出 (.txt / .playing の両方) ---
if _comment_backlog_pending; then
	not_ok "empty queue should not report backlog"
else
	ok "empty comment queue reports no backlog"
fi

: >"$COMMENT_QUEUE_DIR/comment_1.txt"
_comment_backlog_pending && ok "queued comment (.txt) is detected" || not_ok "queued comment (.txt) should be detected"
rm -f "$COMMENT_QUEUE_DIR/comment_1.txt"

: >"$COMMENT_QUEUE_DIR/comment_1.playing"
_comment_backlog_pending && ok "in-flight comment (.playing) is detected" || not_ok "in-flight comment (.playing) should be detected"

# --- 2: 中断判定は背景ラジオだけに効く ---
export SOURCE_LABEL="radio_render:news"
_radio_render_should_abort_for_comment \
	&& ok "radio render aborts while comments are pending" \
	|| not_ok "radio render should abort while comments are pending"

export SOURCE_LABEL="comment"
if _radio_render_should_abort_for_comment; then
	not_ok "comment playback must never abort itself"
else
	ok "comment source is not aborted"
fi

# ラジオ「再生」(radio:news) は合成ではないので対象外
export SOURCE_LABEL="radio:news"
if _radio_render_should_abort_for_comment; then
	not_ok "radio playback label must not be treated as background render"
else
	ok "radio playback label is not treated as background render"
fi

export SOURCE_LABEL="radio_render:news"
export RADIO_RENDER_COMMENT_ABORT=0
if _radio_render_should_abort_for_comment; then
	not_ok "RADIO_RENDER_COMMENT_ABORT=0 should restore the old behaviour"
else
	ok "RADIO_RENDER_COMMENT_ABORT=0 disables the abort"
fi
unset RADIO_RENDER_COMMENT_ABORT

rm -f "$COMMENT_QUEUE_DIR"/comment_*
if _radio_render_should_abort_for_comment; then
	not_ok "no backlog should not abort the render"
else
	ok "radio render continues when no comment is pending"
fi

# --- 3: 合成中にコメントが入ったら即座に打ち切る (rc=9, 中途WAVは残さない) ---
_synthesize_chunk() {
	# 実際の VOICEVOX の代わりに、時間のかかる合成を模擬する。
	printf 'partial' >"$2"
	sleep 30
	printf 'done' >"$2"
}
export RADIO_RENDER_COMMENT_ABORT_POLL_SEC=1
out="$TMP/chunk_0.wav"
(
	sleep 2
	: >"$COMMENT_QUEUE_DIR/comment_2.txt"
) &
watcher=$!
start=$(date +%s)
_synthesize_chunk_yielding "ニュース本文" "$out"
rc=$?
elapsed=$(($(date +%s) - start))
wait "$watcher" 2>/dev/null || true

[ "$rc" -eq 9 ] && ok "mid-synthesis abort returns rc=9" || not_ok "mid-synthesis abort should return rc=9 (got $rc)"
[ "$elapsed" -lt 15 ] && ok "abort happens promptly (${elapsed}s < 15s, synth would take 30s)" || not_ok "abort took too long: ${elapsed}s"
[ ! -e "$out" ] && ok "partial WAV is removed on abort" || not_ok "partial WAV should be removed on abort"
if pgrep -f 'sleep 30' >/dev/null 2>&1; then
	not_ok "synthesis child process should be killed"
else
	ok "synthesis child process is killed"
fi
rm -f "$COMMENT_QUEUE_DIR"/comment_*

# --- 4: コメントが無ければ通常どおり合成する ---
_synthesize_chunk() {
	printf 'done' >"$2"
	return 0
}
out2="$TMP/chunk_1.wav"
_synthesize_chunk_yielding "ニュース本文" "$out2"
rc=$?
[ "$rc" -eq 0 ] && [ -s "$out2" ] \
	&& ok "synthesis succeeds when no comment is pending" \
	|| not_ok "synthesis should succeed when no comment is pending (rc=$rc)"

# 合成失敗は rc=1 のまま (中断と区別する)
_synthesize_chunk() { return 1; }
_synthesize_chunk_yielding "ニュース本文" "$TMP/chunk_2.wav"
rc=$?
[ "$rc" -eq 1 ] && ok "synthesis failure is still rc=1" || not_ok "synthesis failure should be rc=1 (got $rc)"

cd "$ROOT" || exit 1

# --- 5: 中断後の再試行は指数バックオフを進めない ---
unset -f _radio_render_retry_path 2>/dev/null || true
for fn in \
	_radio_audio_base_path \
	_radio_render_retry_path \
	_radio_schedule_deferred_render_retry \
	_radio_schedule_deferred_render_yield_retry; do
	sed -n "/^${fn}()/,/^}/p" "$RADIO_SRC" >>"$TMP/radio_fns.sh"
done
. "$TMP/radio_fns.sh"

export RADIO_RENDER_RETRY_BASE_SEC=30
export RADIO_RENDER_RETRY_MAX_SEC=300
export RADIO_RENDER_COMMENT_YIELD_RETRY_SEC=20
qf="$TMP/radio_100_1_news_1.txt"
printf 'radio\n' >"$qf"

read -r count delay _at <<<"$(_radio_schedule_deferred_render_yield_retry "$qf")"
[ "$count" -eq 0 ] && [ "$delay" -eq 20 ] \
	&& ok "comment yield retry keeps the failure counter at 0 and retries in 20s" \
	|| not_ok "comment yield retry should be count=0 delay=20 (got count=$count delay=$delay)"

read -r count delay _at <<<"$(_radio_schedule_deferred_render_yield_retry "$qf")"
[ "$count" -eq 0 ] && [ "$delay" -eq 20 ] \
	&& ok "repeated comment yields do not escalate the backoff" \
	|| not_ok "repeated yields should stay count=0 delay=20 (got count=$count delay=$delay)"

read -r count delay _at <<<"$(_radio_schedule_deferred_render_retry "$qf")"
[ "$count" -eq 1 ] && [ "$delay" -eq 30 ] \
	&& ok "a real failure still uses the exponential backoff" \
	|| not_ok "real failure should be count=1 delay=30 (got count=$count delay=$delay)"

# --- 6: 部分レンダーの再開・保持が say_enqueue.sh に組み込まれている ---
grep -Fq 'RENDER_PARTS_DIR="$QUEUE_DIR/render_${_render_parts_key}"' "$SAY_SRC" \
	&& ok "radio render uses a stable parts directory" \
	|| not_ok "radio render should use a stable parts directory"
grep -Fq '_log "事前合成を再利用 (チャンク$((_pc_i + 1))/${#_pre_chunks[@]}): $_pre_chunk_wav"' "$SAY_SRC" \
	&& ok "already synthesized chunks are reused on resume" \
	|| not_ok "already synthesized chunks should be reused on resume"
grep -Fq '_log "合成済みチャンクを保持（次回再開用）: $RENDER_PARTS_DIR"' "$SAY_SRC" \
	&& ok "partial chunks survive an abort" \
	|| not_ok "partial chunks should survive an abort"
grep -Fq '[ -n "${RENDER_PARTS_DIR:-}" ] && rm -rf "$RENDER_PARTS_DIR" 2>/dev/null' "$SAY_SRC" \
	&& ok "parts directory is removed once the render completes" \
	|| not_ok "parts directory should be removed once the render completes"

exit "$FAIL"
