#!/bin/bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
SRC="$ROOT/workers/poll_worker.sh"

sed -n '/^_poll_result_comment_validator()/,/^}/p' "$SRC" >"$TMP/functions.sh"
sed -n '/^_format_poll_result_commentary()/,/^}/p' "$SRC" >>"$TMP/functions.sh"
# shellcheck source=/dev/null
. "$TMP/functions.sh"

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok %d - %s\n' "$pass" "$1"; }
not_ok() { fail=$((fail + 1)); printf 'not ok - %s\n' "$1"; }

result='{"poll":{"choices":[{"title":"工場の株を国民に配る","votes":1},{"title":"プレパスで資本家を一斉検挙","votes":0},{"title":"クルシュシェフ像でNFT発行","votes":0},{"title":"みんなで国営アイス","votes":0}]}}'

if _poll_result_comment_validator '国民株主とは、資本主義もずいぶん赤く染まりましたね。'; then
	ok "日本語の一言コメントを許可"
else
	not_ok "正常な一言コメントを拒否"
fi

leak='The output I produced is not in the required format. Let me redo this correctly. **集計結果** - 工場の株を国民に配る: 1票'
if _poll_result_comment_validator "$leak"; then
	not_ok "実発生した英語自己訂正文を許可"
else
	ok "実発生した英語自己訂正文を拒否"
fi

if _poll_result_comment_validator '工場の株は1票で最多でした。'; then
	not_ok "AIによる票数再掲を許可"
else
	ok "AIによる票数再掲を拒否"
fi

out=$(_format_poll_result_commentary "$result" '国民株主とは、資本主義もずいぶん赤く染まりましたね。')
expected='工場の株を国民に配る1票、プレパスで資本家を一斉検挙0票、クルシュシェフ像でNFT発行0票、みんなで国営アイス0票。国民株主とは、資本主義もずいぶん赤く染まりましたね。'
if [ "$out" = "$expected" ]; then
	ok "票数は機械生成し一言コメントだけを連結"
else
	not_ok "整形結果が不一致: $out"
fi

fallback=$(_format_poll_result_commentary "$result" '')
case "$fallback" in
	*'工場の株を国民に配る1票'*'みんなで国営アイス0票。投票ありがとうございました。') ok "AI不正時も正確な集計へフォールバック" ;;
	*) not_ok "フォールバックが不正: $fallback" ;;
esac

if [ "${#out}" -le 200 ]; then ok "最終文は200文字以内"; else not_ok "最終文が200文字超過"; fi

if grep -Fq '_poll_result_comment_validator' "$SRC" && grep -Fq '_format_poll_result_commentary "$result_json" "$raw"' "$SRC"; then
	ok "生成経路に検証と機械集計を配線"
else
	not_ok "生成経路の配線が不足"
fi

printf '1..%d\n' "$((pass + fail))"
[ "$fail" -eq 0 ]
