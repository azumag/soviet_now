#!/usr/bin/env bash
# docich の特別コーナー(external-video-view)用メモが、メイン画面がそのビューの
# ときだけコメントUIメモへ入ることを検証する。
#
# docich は WebUI 設定から canonical と同じ state dir に special_corner_memo.md を
# 書き出す。comment.sh はコメント生成のたびにそれを読み直す(hot-read)。
# 他のゲームが映っている間・canonical が壊れている間は何も足さない。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

sed -n '/^_comment_special_corner_memo()/,/^}/p' "$SRC" >"$TMP/fn.sh"
[ -s "$TMP/fn.sh" ] || { not_ok "extract _comment_special_corner_memo"; exit 1; }
# shellcheck source=/dev/null
. "$TMP/fn.sh"

export DOCICH_GAME_SWITCH_CANONICAL_FILE="$TMP/run/game_switch.json"
mkdir -p "$TMP/run"
MEMO="$TMP/run/special_corner_memo.md"

# --- 1. ファイルが無ければ空 ---
out=$(_comment_special_corner_memo)
[ -z "$out" ] && ok "canonical/memo 無しは空" || not_ok "canonical 無しで出力: $out"

printf '%s\n' '- 【いまのメイン画面が external-video-view（特別コーナー）のときだけ有効】特別コーナー「ゲームA」の説明' \
	'  ゲームAの映像です。' >"$MEMO"

# --- 2. 別ゲームが映っている間は足さない ---
printf '%s' '{"active":{"game":"sorengame"},"candidate":{"game":"external-video-view"}}' >"$DOCICH_GAME_SWITCH_CANONICAL_FILE"
out=$(_comment_special_corner_memo)
[ -z "$out" ] && ok "active が別ゲームなら空(candidate は見ない)" || not_ok "別ゲーム中に出力: $out"

# --- 3. external-video-view が active なら毎回読み直して返す ---
printf '%s' '{"active":{"game":"external-video-view","generation":3}}' >"$DOCICH_GAME_SWITCH_CANONICAL_FILE"
out=$(_comment_special_corner_memo)
case "$out" in *"特別コーナー「ゲームA」の説明"*"ゲームAの映像です。") ok "active 中はメモを返す" ;; *) not_ok "メモ欠落: $out" ;; esac
printf '%s\n' '- 特別コーナー「ゲームB」の説明' >"$MEMO"
out=$(_comment_special_corner_memo)
case "$out" in *"ゲームB"*) ok "書き換えは次の呼び出しで反映(hot-read)" ;; *) not_ok "再読込されない: $out" ;; esac

# --- 4. 壊れた/危険な内容 ---
printf '%s\n' '- 説明 ${_comment_persona} `id` <b>x</b> \n' >"$MEMO"
out=$(_comment_special_corner_memo)
case "$out" in *'$'*|*'`'*|*'<'*|*'>'*|*'\'*) not_ok "メタ文字が残った: $out" ;; *) ok "メタ文字を落とす" ;; esac
printf '%s' '{not json' >"$DOCICH_GAME_SWITCH_CANONICAL_FILE"
out=$(_comment_special_corner_memo)
[ -z "$out" ] && ok "壊れた canonical では空" || not_ok "壊れた canonical で出力: $out"
printf '%s' '{"active":{"game":"external-video-view"}}' >"$DOCICH_GAME_SWITCH_CANONICAL_FILE"
head -c 20000 /dev/zero | tr '\0' 'a' >"$MEMO"
out=$(_comment_special_corner_memo)
[ "${#out}" -le 8192 ] && ok "8KiB で打ち切る" || not_ok "上限なし: ${#out}"

# --- 5. 明示パスの上書き ---
export DOCICH_SPECIAL_CORNER_MEMO_FILE="$TMP/other.md"
printf '%s\n' '- 別パスのメモ' >"$DOCICH_SPECIAL_CORNER_MEMO_FILE"
out=$(_comment_special_corner_memo)
case "$out" in *"別パスのメモ"*) ok "DOCICH_SPECIAL_CORNER_MEMO_FILE で上書きできる" ;; *) not_ok "上書き不可: $out" ;; esac

# --- 6. 呼び出し側: main モードでだけコメントUIメモへ追記している ---
grep -q '_special_corner_memo=$(_comment_special_corner_memo)' "$SRC" \
	&& ok "comment.sh がコメントUIメモへ追記する" || not_ok "呼び出しが無い"

exit "$FAIL"
