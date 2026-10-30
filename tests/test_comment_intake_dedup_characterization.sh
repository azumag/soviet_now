#!/bin/bash
# soviet_now#144: 主応答経路の intake dedup seam の characterization test。
# `_filter_already_processed_comment_lines` / `_has_processed_comment_line` /
# `_record_processed_comment_lines` / `_comment_dedup_key` /
# `_format_comment_batch_context` の現行動作を固定する（規範ではなく実測契約）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ok=0
fail=0
pass() { echo "ok - $1"; ok=$((ok + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

export ELOOP_LIB_DIR="$TMP"
export COMMENT_PROCESSED_LINES_FILE="$TMP/processed.log"
export COMMENT_PROCESSED_LINES_TTL=1800
export COMMENT_PROCESSED_LINES_MAX=500
export COMMENT_BATCH_HISTORY_FILE="$TMP/batch_history.log"

source broadcast/comment.sh
log() { :; }

# ==== _comment_dedup_key: "user: msg" は msg 部分のみが key ====
k=$(_comment_dedup_key "alice: こんにちは")
[ "$k" = "こんにちは" ] && pass 'dedup_key strips "user: " prefix' || not_ok "dedup_key='$k'"
k=$(_comment_dedup_key "alice: こんにちは")
k2=$(_comment_dedup_key "bob: こんにちは")
[ "$k" = "$k2" ] && pass 'dedup_key is user-agnostic (same msg = same key)' || not_ok 'dedup_key differs by user'
# head 判定の glob は `*" "`（末尾空白）なので、空白を「含む」 head でも strip される
k=$(_comment_dedup_key "user name: こんにちは")
[ "$k" = "こんにちは" ] && pass 'dedup_key strips head containing space (glob is trailing-space only)' || not_ok "dedup_key='$k'"
# head が空白で終わる行 (": " 直前が空白) は唯一保持される
k=$(_comment_dedup_key "a : b")
[ "$k" = "a : b" ] && pass 'dedup_key keeps line when head ends with space' || not_ok "dedup_key='$k'"
k=$(_comment_dedup_key "区切りなしの行")
[ "$k" = "区切りなしの行" ] && pass 'dedup_key passthrough without ": "' || not_ok "dedup_key='$k'"

# ==== _filter_already_processed_comment_lines ====
# file 不在 → 全行 passthrough
out=$(_filter_already_processed_comment_lines $'alice: hello\nbob: world')
[ "$out" = $'alice: hello\nbob: world' ] && pass 'no processed file -> passthrough' || not_ok "passthrough='$out'"
# 空入力 → rc 0・空出力
out=$(_filter_already_processed_comment_lines "")
[ -z "$out" ] && pass 'empty input -> empty output' || not_ok 'empty input produced output'

# 記録 → 同一メッセージ(別user)は処理済みとして除外
_record_processed_comment_lines "alice: こんにちは"
out=$(_filter_already_processed_comment_lines $'bob: こんにちは\ncarol: 別の話題')
[ "$out" = "carol: 別の話題" ] && pass 'same message different user filtered' || not_ok "filter='$out'"

# TTL 内 → 除外、TTL 超過 → 残る
now=$(date +%s)
h=$(_comment_hash_text "期限テスト")
printf '%s|%s\n' "$((now - 1900))" "$h" >>"$COMMENT_PROCESSED_LINES_FILE"
out=$(_filter_already_processed_comment_lines "x: 期限テスト")
[ "$out" = "x: 期限テスト" ] && pass 'entry older than TTL is ignored' || not_ok "expired entry filtered '$out'"
printf '%s|%s\n' "$((now - 10))" "$h" >"$COMMENT_PROCESSED_LINES_FILE"
_record_processed_comment_lines "alice: こんにちは" # 再構成(期限切れ落ち防止のため明示)
h2=$(_comment_hash_text "新鮮テスト")
printf '%s|%s\n' "$((now - 10))" "$h2" >"$COMMENT_PROCESSED_LINES_FILE"
out=$(_filter_already_processed_comment_lines "x: 新鮮テスト")
[ -z "$out" ] && pass 'entry within TTL is filtered' || not_ok "fresh entry not filtered '$out'"

# ==== _has_processed_comment_line ====
printf '' >"$COMMENT_PROCESSED_LINES_FILE"
if _has_processed_comment_line "y: 未処理"; then not_ok 'unprocessed reported processed'; else pass 'unprocessed line -> rc 1'; fi
rm -f "$COMMENT_PROCESSED_LINES_FILE"
if _has_processed_comment_line "y: 未処理"; then not_ok 'no file reported processed'; else pass 'missing file -> rc 1'; fi
_record_processed_comment_lines "z: 既知の話"
if _has_processed_comment_line "q: 既知の話"; then pass 'processed detected across users'; else not_ok 'processed line missed'; fi

# ==== _record_processed_comment_lines: ts|hash 追記・TTL prune・MAX cap ====
: >"$COMMENT_PROCESSED_LINES_FILE"
_record_processed_comment_lines $'a: 一\nb: 二'
[ "$(wc -l <"$COMMENT_PROCESSED_LINES_FILE" | tr -d ' ')" = "2" ] && pass 'record appends ts|hash lines' || not_ok 'record line count mismatch'
grep -qE '^[0-9]+\|[0-9a-f]+$' "$COMMENT_PROCESSED_LINES_FILE" && pass 'record format ts|hexhash' || not_ok 'record format mismatch'
# 古いエントリは次回記録時に prune される
old=$(( $(date +%s) - 99999 ))
printf '%s|deadbeef\n' "$old" >"$COMMENT_PROCESSED_LINES_FILE"
_record_processed_comment_lines "a: 新しい"
! grep -q deadbeef "$COMMENT_PROCESSED_LINES_FILE" && pass 'record prunes expired entries' || not_ok 'expired entry kept'
# MAX cap: tail -n MAX で有界
export COMMENT_PROCESSED_LINES_MAX=3
seq 1 5 | while read -r i; do printf '%s|%s\n' "$(date +%s)" "h$i"; done >"$COMMENT_PROCESSED_LINES_FILE"
_record_processed_comment_lines "a: 追加"
[ "$(wc -l <"$COMMENT_PROCESSED_LINES_FILE" | tr -d ' ')" -le 3 ] && pass 'record capped at MAX' || not_ok 'record exceeded MAX'
export COMMENT_PROCESSED_LINES_MAX=500

# ==== _format_comment_batch_context: [i] user: msg + 前後コンテキスト ====
ctx=$(printf 'alice: 一つ目\nbob: 二つ目\nalice: 三つ目\n' | _format_comment_batch_context)
printf '%s' "$ctx" | grep -q '^\[1\] alice: 一つ目' && pass 'batch context item 1' || not_ok 'batch context item 1'
printf '%s' "$ctx" | grep -q '^\[3\] alice: 三つ目' && pass 'batch context item 3' || not_ok 'batch context item 3'
printf '%s' "$ctx" | grep -q '直後: alice: 三つ目' && pass 'next-item context embedded' || not_ok 'next context missing'
# 「直前が同一ユーザー: あり」は連続同一ユーザーのみ
ctx_same=$(printf 'alice: 連投一\nalice: 連投二\n' | _format_comment_batch_context)
printf '%s' "$ctx_same" | grep -q '直前が同一ユーザー: あり' && pass 'same-user prev marker for consecutive user' || not_ok 'same-user marker missing'
ctx_diff=$(printf 'alice: a\nbob: b\nalice: c\n' | _format_comment_batch_context)
! printf '%s' "$ctx_diff" | grep -q '直前が同一ユーザー: あり' && pass 'same-user marker absent for non-consecutive user' || not_ok 'same-user marker false positive'
# ": " を含まない行は user=不明 扱い
ctx2=$(printf '区切りなし\n' | _format_comment_batch_context)
printf '%s' "$ctx2" | grep -q '^\[1\] 不明: 区切りなし' && pass 'no-separator line -> user 不明' || not_ok "no-sep ctx='$ctx2'"

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]
