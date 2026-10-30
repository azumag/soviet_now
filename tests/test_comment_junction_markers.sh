#!/bin/bash
# soviet_now#144: generate→deliver 結合部のマーカー抽出 seam の characterization。
# soviet_now#739 で `_extract_named_block` / `_remove_named_block` の
# heredoc stdin 消費バグ（python3 - 形式がプログラムに stdin を使い
# パイプのテキストが届かない）を `python3 -c` 形式へ修正済み。
# 本テストは修正後の正しい挙動（抽出・除去が実際に動く）を固定する。
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
source broadcast/comment.sh
log() { :; }

# ==== named block extract/remove はパイプ入力を処理する（#739 修正後） ====
out=$(printf '本文\n===ADVICE===\n縦置きが有効\n===ADVICE===\n' | _extract_named_block ADVICE)
[ "$out" = '縦置きが有効' ] && pass 'extract: closed block -> inner text' || not_ok "extract changed: '$out'"
out=$(printf '===COMMENT_ADVICE===\n一行目\n===ADVICE===\n他\n' | _extract_named_block COMMENT_ADVICE)
[ "$out" = '一行目' ] && pass 'extract: open block -> text until next marker' || not_ok "open extract changed: '$out'"
out=$(printf '===CODEX_ADVICE===\n全部\n' | _extract_named_block CODEX_ADVICE)
[ "$out" = '全部' ] && pass 'extract: EOF block -> text until end' || not_ok "eof extract changed: '$out'"
out=$(printf '普通の本文\n' | _extract_named_block ADVICE)
[ -z "$out" ] && pass 'extract: no marker -> empty' || not_ok "no-marker extract changed: '$out'"
printf '===ADVICE===\nx\n===ADVICE===\n' | _extract_named_block ADVICE >/dev/null && pass 'extract rc=0' || not_ok 'extract rc!=0'

out=$(printf '前\n===ADVICE===\n除去対象\n===ADVICE===\n後\n' | _remove_named_block ADVICE)
[ "$out" = '前
後' ] && pass 'remove: closed block removed, text survives' || not_ok "remove changed: '$out'"
out=$(printf 'そのまま本文\n' | _remove_named_block ADVICE)
[ "$out" = 'そのまま本文' ] && pass 'remove: no marker -> passthrough' || not_ok "remove passthrough changed: '$out'"
printf 'x\n' | _remove_named_block ADVICE >/dev/null && pass 'remove rc=0' || not_ok 'remove rc!=0'

# ==== 帰結: 呼出側の `if [ -n "$part" ]` ゲートが発火し advice file 経路が生きる ====
attempt_talk='本文と ===ADVICE=== マーカー入り'
part=$(printf '%s' "$attempt_talk" | _extract_named_block ADVICE)
[ -z "$part" ] && pass 'inline marker (no block) still yields empty extract' || not_ok 'inline marker extracted'

# ==== _extract_sing_score / _remove_sing_score_block ====
# named block と同じ python3 -c 形式（#739 で揃った）。実動作を固定する。
sing='{"notes":[{"key":60,"frame_length":45,"lyric":"き"}]}'
out=$(printf '応答文\n===SING===\n%s\n===SING===\n' "$sing" | _extract_sing_score)
printf '%s' "$out" | grep -q '"notes"' && pass 'sing score JSON extracted (works: -c form)' || not_ok "sing extract='$out'"
out=$(printf '応答文\n===SING===\n%s\n===SING===\n' "$sing" | _remove_sing_score_block)
printf '%s' "$out" | grep -q '"notes"' && not_ok 'sing JSON left in text' || pass 'sing JSON removed from text'
printf '%s' "$out" | grep -q '応答文' && pass 'text survives sing removal' || not_ok 'text lost in sing removal'
out=$(printf '前置き %s 後置き' "$sing" | _extract_sing_score)
printf '%s' "$out" | grep -q '"notes"' && pass 'bare score JSON extracted without marker' || not_ok "bare extract='$out'"
out=$(printf 'x {"a":1} y' | _extract_sing_score || true)
[ -z "$out" ] && pass 'non-score JSON ignored' || not_ok "false positive='$out'"
printf 'x {"a":1} y' | _extract_sing_score >/dev/null && not_ok 'non-score extract rc=0' || pass 'non-score extract rc=1 (fail-closed)'

# ==== 生成候補検証 (_comment_is_valid_generation_candidate) ====
_is_valid_comment_talk() { [ -n "$1" ]; }
_contains_provider_error_text() { return 1; }
if _comment_is_valid_generation_candidate ""; then not_ok 'empty candidate accepted'; else pass 'empty candidate rejected'; fi
# marker ブロックを含む生成候補は、guard→clean→sanitize 連鎖で
# 検証を通らず却下される（候補検証は抽出より前の段階なので、
# #739 の extract 修正とは独立に却下経路が維持される）。
if _comment_is_valid_generation_candidate $'===ADVICE===\nx\n===ADVICE===\n有効な本文'; then not_ok 'marker candidate accepted'; else pass 'candidate containing ===ADVICE=== block is rejected downstream'; fi

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]
