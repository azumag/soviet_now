#!/bin/bash
# soviet_now#144: generate→deliver 結合部のマーカー抽出 seam の characterization。
# 【重要】本テストが固定する現行動作には既知の不具合が含まれる:
#   `_extract_named_block` / `_remove_named_block` は内部で
#   `python3 - "$marker" <<'PY'` を呼び、heredoc が python のプログラムになる
#   ため、パイプ経由のテキストは一切届かず sys.stdin.read() は常に空。
#   結果として両関数は**任意入力で常に空出力・rc=0**を返す。
#   呼出側は `if [ -n "$(...extract...)" ]` でゲートするため remove も発火せず、
#   COMMENT_ADVICE/ADVICE/CODEX_ADVICE の抽出→advice file 追記経路は死んでおり、
#   ===X=== ブロックは本文へ残って後段 sanitizer 任せになる（follow-up issue で追跡）。
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

# ==== 現行固定: named block extract/remove は常に空（既知不具合、仕様ではない） ====
out=$(printf '本文\n===ADVICE===\n縦置きが有効\n===ADVICE===\n' | _extract_named_block ADVICE)
[ -z "$out" ] && pass 'extract: closed block -> empty (dead path, piped text unreachable)' || not_ok "extract changed: '$out'"
out=$(printf '===COMMENT_ADVICE===\n一行目\n===ADVICE===\n他\n' | _extract_named_block COMMENT_ADVICE)
[ -z "$out" ] && pass 'extract: open block -> empty (dead path)' || not_ok "open extract changed"
out=$(printf '===CODEX_ADVICE===\n全部\n' | _extract_named_block CODEX_ADVICE)
[ -z "$out" ] && pass 'extract: EOF block -> empty (dead path)' || not_ok "eof extract changed"
printf '===ADVICE===\nx\n===ADVICE===\n' | _extract_named_block ADVICE >/dev/null && pass 'extract rc=0 despite dead path' || not_ok 'extract rc!=0'

out=$(printf '前\n===ADVICE===\n除去対象\n===ADVICE===\n後\n' | _remove_named_block ADVICE)
[ -z "$out" ] && pass 'remove: any input -> empty (dead path)' || not_ok "remove changed: '$out'"
printf 'x\n' | _remove_named_block ADVICE >/dev/null && pass 'remove rc=0' || not_ok 'remove rc!=0'

# ==== 帰結: 呼出側の `if [ -n "$part" ]` ゲートは発火しない → 本文は残る ====
# （extract が空を返すため remove 経路に入らない = 生成本文は wipe されない）
attempt_talk='本文と ===ADVICE=== マーカー入り'
part=$(printf '%s' "$attempt_talk" | _extract_named_block ADVICE)
[ -z "$part" ] && pass 'advice never routed to advice file (gate never fires)' || not_ok 'gate fired'

# ==== _extract_sing_score / _remove_sing_score_block: 別実装で動作 ====
# python3 -c 形式のため stdin のテキストが届き実際に動く（named block との非対称を固定）
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
# marker ブロックを含む生成候補は、現行の guard→clean→sanitize 連鎖で
# 検証を通らず却下される（===ADVICE=== ブロックが本文から除去されず、
# 後段の形式検証で弾かれる）。dead extract との整合をここでも固定する。
if _comment_is_valid_generation_candidate $'===ADVICE===\nx\n===ADVICE===\n有効な本文'; then not_ok 'marker candidate accepted'; else pass 'candidate containing ===ADVICE=== block is rejected downstream'; fi

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]
