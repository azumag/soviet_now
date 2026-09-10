#!/usr/bin/env bash
# 2026-09-11: 重複判定を「本文（先頭の user: を除く）」だけをキーにする。
# 別アカウント（IDローテーション）が同一本文を連投するスパムを、ユーザー名に
# 依存せず集約する。キーワード判定はしないため、文面が異なる正常コメントは
# 影響を受けない。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
fail=0
pass=0
ok() { pass=$((pass + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

eval "$(sed -n '/^_comment_hash_text()/,/^}$/p' "$SRC")"
eval "$(sed -n '/^_comment_dedup_key()/,/^}$/p' "$SRC")"
eval "$(sed -n '/^_filter_already_processed_comment_lines()/,/^}$/p' "$SRC")"
eval "$(sed -n '/^_has_processed_comment_line()/,/^}$/p' "$SRC")"
eval "$(sed -n '/^_record_processed_comment_lines()/,/^}$/p' "$SRC")"

log() { :; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
COMMENT_PROCESSED_LINES_FILE="$TMP/processed_line_hashes.log"
COMMENT_PROCESSED_LINES_TTL=1800
COMMENT_PROCESSED_LINES_MAX=2000

k1=$(_comment_dedup_key "alice: own kick. co m")
k2=$(_comment_dedup_key "bob: own kick. co m")
[ "$k1" = "$k2" ] && ok || not_ok "別ユーザー同一本文は同一キー ($k1 / $k2)"

k3=$(_comment_dedup_key "alice: こんにちは")
[ "$k1" != "$k3" ] && ok || not_ok "本文が異なれば別キー"

k4=$(_comment_dedup_key "https://example.com/a")
[ "$k4" = "https://example.com/a" ] && ok || not_ok "user接頭辞なしは保持 (got '$k4')"

k5=$(_comment_dedup_key "viewer: see https://example.com path: /x")
[ "$k5" = "see https://example.com path: /x" ] && ok || not_ok "本文中の ': ' は保持 (got '$k5')"

# 記録 → 別ユーザーの同一本文を除外
_record_processed_comment_lines "ownkick_05f: own kick. co m"
got=$(_filter_already_processed_comment_lines "ownkick_644: own kick. co m")
[ -z "$got" ] && ok || not_ok "別ユーザーの同一本文を除外 (got '$got')"

got=$(_filter_already_processed_comment_lines "viewer: こんばんは")
[ "$got" = "viewer: こんばんは" ] && ok || not_ok "新規本文は保持 (got '$got')"

echo "pass=$pass fail=$fail"
exit "$fail"
