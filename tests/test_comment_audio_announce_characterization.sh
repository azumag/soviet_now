#!/bin/bash
# soviet_now#144: comment queue 音声アナウンス seam の characterization test。
# `enqueue_audio_text` / `enqueue_audio_file` の現行契約を固定する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ok=0
fail=0
pass() { echo "ok - $1"; ok=$((ok + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

export ELOOP_LIB_DIR="$ROOT"
export COMMENT_QUEUE_DIR="$TMP/q"
export COMMENT_AUDIO_DEDUP_DIR="$TMP/dedup"

source lib/outbound_queue.sh
log() { :; }

# ==== enqueue_audio_text: 基本契約 ====
enqueue_audio_text "アナウンス一" weather && pass 'enqueue rc=0' || not_ok 'enqueue rc!=0'
f=$(ls "$COMMENT_QUEUE_DIR" | grep -v '^\.' | head -1)
case "$f" in
comment_announce_*_weather.txt) pass 'filename comment_announce_{ts}_{pid}_{rand}_{source}.txt' ;;
*) not_ok "filename=$f" ;;
esac
[ "$(cat "$COMMENT_QUEUE_DIR/$f")" = "アナウンス一" ] && pass 'txt holds text' || not_ok 'txt content mismatch'

# ==== source 検証 ====
enqueue_audio_text "x" 'bad;source' && not_ok 'shell char source accepted' || pass 'source with shell char rejected'
enqueue_audio_text "x" 'hanjuku:commentary' && not_ok 'hanjuku:commentary accepted' || pass 'hanjuku:commentary (colon form) rejected at source gate'
enqueue_audio_text "x" 'trpg:scene1' "" "" && not_ok 'trpg without fence accepted' || pass 'runtime-bound source without fence rejected'

# ==== runtime fence: game 不一致拒否・一致受理 ====
fence_wrong=$(python3 lib/runtime_audio_fence.py encode "{\"game\":\"trpg-view\",\"runtime_id\":\"g1-abcdef12\",\"generation\":1,\"lease_id\":\"l\",\"expires_at\":$(( $(date +%s) + 100 ))}")
enqueue_audio_text "x" 'hanjuku_commentary' "" "$fence_wrong" && not_ok 'wrong-game fence accepted' || pass 'fence game mismatch rejected'
fence_ok=$(python3 lib/runtime_audio_fence.py encode "{\"game\":\"hanjuku-hero\",\"runtime_id\":\"g1-abcdef12\",\"generation\":1,\"lease_id\":\"l\",\"expires_at\":$(( $(date +%s) + 100 ))}")
marker="$TMP/.marker"; sleep 1; touch "$marker"
enqueue_audio_text "柵付き" 'hanjuku_commentary' "" "$fence_ok" "scope1" && pass 'matching fence accepted' || not_ok 'matching fence rejected'
new=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*.txt' -newer "$marker" -type f 2>/dev/null | tail -1)
[ -z "$new" ] && new=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*.txt' -type f | tail -1)
[ -f "${new%.txt}.runtime_fence.json" ] && pass 'fence sidecar published' || not_ok 'fence sidecar missing'
grep -q '"game": *"hanjuku-hero"\|"game":"hanjuku-hero"' "${new%.txt}.runtime_fence.json" && pass 'fence sidecar binds game' || not_ok 'fence game wrong'
[ -f "${new%.txt}.dedup_key" ] && pass 'dedup_key sidecar written when scope given' || not_ok 'dedup_key sidecar missing'

# ==== speaker sidecar ====
enqueue_audio_text "声色指定" weather "zundamon" && pass 'speaker enqueue rc=0'
sp=$(find "$COMMENT_QUEUE_DIR" -name '*.speaker' | tail -1)
[ -n "$sp" ] && [ "$(cat "$sp")" = "zundamon" ] && pass '.speaker sidecar holds override' || not_ok 'speaker sidecar wrong'

# ==== dedup: 同一 text+scope は rc=0 で積まない（silent drop） ====
n_before=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*.txt' -type f | wc -l | tr -d ' ')
enqueue_audio_text "二度積み防止" weather "" "" "s1" && pass 'first scoped enqueue'
enqueue_audio_text "二度積み防止" weather "" "" "s1" && pass 'dup scoped enqueue rc=0 (silent drop)'
n_after=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*.txt' -type f | wc -l | tr -d ' ')
[ "$((n_after - n_before))" = "1" ] && pass 'same text+scope deduped to one file' || not_ok "dedup failed (delta=$((n_after-n_before)))"
# scope 違いは別扱い
enqueue_audio_text "二度積み防止" weather "" "" "s2" && pass 'different scope enqueued'
n_final=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*.txt' -type f | wc -l | tr -d ' ')
[ "$((n_final - n_before))" = "2" ] && pass 'different scope creates second file' || not_ok 'scope dedup over-matched'

# ==== enqueue_audio_file: 内容コピー・dedup・speaker ====
printf 'ファイル内容一\n' >"$TMP/voice1.txt"
enqueue_audio_file "$TMP/voice1.txt" radio && pass 'audio_file enqueue rc=0'
af=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*_radio.txt' -type f | tail -1)
[ "$(cat "$af")" = "ファイル内容一" ] && pass 'audio_file content copied' || not_ok 'audio_file content wrong'
# 同一内容の別ファイルは dedup（content hash ベース）
cp "$TMP/voice1.txt" "$TMP/voice2.txt"
radio_before=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*_radio.txt' -type f | wc -l | tr -d ' ')
enqueue_audio_file "$TMP/voice2.txt" radio && pass 'same-content file rc=0 (dedup drop)'
radio_after=$(find "$COMMENT_QUEUE_DIR" -name 'comment_announce_*_radio.txt' -type f | wc -l | tr -d ' ')
[ "$radio_after" = "$radio_before" ] && pass 'audio_file dedup by content hash' || not_ok 'audio_file dedup failed'
# 空/不在ファイルは拒否
enqueue_audio_file "$TMP/nonexistent.txt" radio && not_ok 'missing file accepted' || pass 'missing file rejected'
: >"$TMP/empty.txt"
enqueue_audio_file "$TMP/empty.txt" radio && not_ok 'empty file accepted' || pass 'empty file rejected'

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]
