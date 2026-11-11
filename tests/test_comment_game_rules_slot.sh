#!/usr/bin/env bash
# ゲームの基本ルール節をテンプレート直書きからスロットにした変更の検証。
#
# 背景: comment_response_game.md / comment_template.md には「ソ連ゲームの基本ルール」が直書きで、
# 半熟英雄など別ゲームが画面でも説明に混ざった。節を prompts/comment_game_rules{,_ja}.md に切り出し、
# テンプレートは ${_comment_game_rules} / ${_comment_game_rules_ja} だけにした。既定値は従来の文面そのまま
# (ソ連ゲームの出力はバイト一致。docich 側の golden が実測で固定する)。画面のゲームが docich の profile を
# 持つ間は profile が差し替える。ここは Soren 側の配線と差し替えを見る。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

GAME="$ROOT/prompts/comment_response_game.md"
TPL="$ROOT/prompts/comment_template.md"
EN="$ROOT/prompts/comment_game_rules.md"
JA="$ROOT/prompts/comment_game_rules_ja.md"

# --- 1. 配線: envsubst 許可リスト3箇所・export・local ---
n=$(grep -c '\${_comment_game_rules} \${_comment_game_rules_ja}' "$SRC")
[ "$n" -eq 3 ] && ok "envsubst 3箇所すべてにスロットを登録" || not_ok "envsubst の登録数が $n (期待 3)"
grep -q 'export _comment_persona' "$SRC" && grep -q '_comment_channel_intro _comment_game_rules _comment_game_rules_ja _comment_length_policy' "$SRC" \
	&& ok "export 済み" || not_ok "export されていない"
grep -q 'local _comment_persona _comment_ui_memo _comment_channel_intro _comment_game_rules _comment_game_rules_ja' "$SRC" \
	&& ok "local 宣言済み" || not_ok "local 宣言なし"

# --- 2. テンプレートはスロットだけ。ソ連ゲームのルール文はスロット用ファイルにだけある ---
[ "$(grep -c '^\${_comment_game_rules}$' "$GAME")" -eq 1 ] && ok "game.md にスロットが1つ" || not_ok "game.md のスロット数"
[ "$(grep -c '^\${_comment_game_rules_ja}$' "$TPL")" -eq 1 ] && ok "comment_template.md にスロットが1つ" || not_ok "comment_template.md のスロット数"
for needle in "16 countries" "Main Game Basic Rules" "Strategy keys"; do
	grep -q "$needle" "$GAME" && not_ok "game.md にルール文「${needle}」が残った" || ok "game.md からルール文「${needle}」を除去"
	grep -q "$needle" "$EN" && ok "スロットファイル(英)に「${needle}」" || not_ok "スロットファイル(英)に「${needle}」が無い"
done
for needle in "16種類の国" "本編ゲームの基本ルール" "戦略の要"; do
	grep -q "$needle" "$TPL" && not_ok "comment_template.md にルール文「${needle}」が残った" || ok "comment_template.md からルール文「${needle}」を除去"
	grep -q "$needle" "$JA" && ok "スロットファイル(日)に「${needle}」" || not_ok "スロットファイル(日)に「${needle}」が無い"
done
grep -q '^	【本編ゲームの基本ルール' "$JA" && ok "日本語版は元のタブ字下げのまま" || not_ok "日本語版のタブ字下げが崩れた"

# --- 2b. 既定のルール文は従来のテンプレ直書きと同一内容(ピン) ---
# 元のテンプレートから切り出した文面そのもの(スロット値を元に戻すと元のテンプレとバイト一致することを
# 切り出し時に確認済み)。ソ連ゲームのルール文を意図して直すときは、このピンと docich 側の写し・golden を更新する。
sha() { python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }
[ "$(sha "$EN")" = "62eb938424dae27a621768769386b8356e778fc35b55b90b84ca2ea061915d17" ] && ok "既定ルール(英)は切り出し時と同一内容" || not_ok "既定ルール(英)の内容が変わった: $(sha "$EN")"
[ "$(sha "$JA")" = "534926e3099fdbb8e89abdb40790d18961ca65bf88a7045178215fea03ce5280" ] && ok "既定ルール(日)は切り出し時と同一内容" || not_ok "既定ルール(日)の内容が変わった: $(sha "$JA")"

# --- 3. 実 envsubst でスロットを展開: ブロックが元の位置・元の改行構造で現れる ---
list=$(grep -o "envsubst '[^']*'" "$SRC" | sed -n '1p' | sed "s/^envsubst '//; s/'$//")
case "$list" in *'${_comment_game_rules}'*) ok "カテゴリ別の許可リストにスロットがある" ;; *) not_ok "許可リストにスロットなし" ;; esac
export _comment_game_rules _comment_game_rules_ja
_comment_game_rules=$(cat "$EN"); _comment_game_rules_ja=$(cat "$JA")
envsubst "$list" <"$GAME" >"$TMP/game.out"
python3 - "$TMP/game.out" "$EN" <<'PY' && ok "英語版: ブロックが「Celebration…」と「Comment Batch Context」の間に元の改行で入る" || not_ok "英語版: 描画位置/改行"
import sys
out = open(sys.argv[1], encoding="utf-8").read()
block = open(sys.argv[2], encoding="utf-8").read().rstrip("\n")
assert out.count(block) == 1
i = out.index(block)
assert out[i - 2:i] == "\n\n", repr(out[i - 6:i])
assert out[i + len(block):].startswith("\n\n【Comment Batch Context】")
PY
envsubst "$(grep -o "envsubst '[^']*'" "$SRC" | sed -n '2p' | sed "s/^envsubst '//; s/'$//")" <"$TPL" >"$TMP/tpl.out"
python3 - "$TMP/tpl.out" "$JA" <<'PY' && ok "日本語版: ブロックが元の位置・タブ字下げ・改行で入る" || not_ok "日本語版: 描画位置/改行"
import sys
out = open(sys.argv[1], encoding="utf-8").read()
block = open(sys.argv[2], encoding="utf-8").read().rstrip("\n")
assert out.count(block) == 1
i = out.index(block)
assert out[i - 2:i] == "\n\n"
assert out[i + len(block):].startswith("\n\n\t【ルール】")
PY
grep -q '\${_comment_game_rules' "$TMP/game.out" "$TMP/tpl.out" && not_ok "未展開のスロットが残った" || ok "スロットは残らず展開される"

# --- 4. profile 取得: game_rules / game_rules_ja を通す(無害化・上限) ---
sed -n "/^_comment_game_profile()/,/^}/p" "$SRC" >"$TMP/fn_p.sh"; . "$TMP/fn_p.sh"
sed -n "/^_comment_profile_field()/,/^}/p" "$SRC" >"$TMP/fn_f.sh"; . "$TMP/fn_f.sh"
sed -n "/^_comment_profile_apply_persona()/,/^}/p" "$SRC" >"$TMP/fn_a.sh"; . "$TMP/fn_a.sh"
CANON="$TMP/canonical.json"; export DOCICH_GAME_SWITCH_CANONICAL_FILE="$CANON"
printf '{"active":{"game":"hanjuku-hero"}}' >"$CANON"
long=$(python3 -c "print('x'*2000)")
printf '#!/bin/bash\nprintf %%s %s\n' "'{\"schema\":1,\"game\":\"hanjuku-hero\",\"game_rules\":\"RULES-EN \$HOME \`id\` <b>\",\"game_rules_ja\":\"\\tRULES-JA\",\"ui_memo\":\"\",\"junk\":\"$long\"}'" >"$TMP/l.sh"; chmod +x "$TMP/l.sh"
export DOCICH_COMMENT_PROFILE="$TMP/l.sh"
prof=$(_comment_game_profile)
[ "$(_comment_profile_field "$prof" game_rules)" = "RULES-EN HOME id b" ] && ok "game_rules を通し、メタ文字を除去" || not_ok "game_rules: $(_comment_profile_field "$prof" game_rules)"
[ -n "$(_comment_profile_field "$prof" game_rules_ja)" ] && ok "game_rules_ja を通す" || not_ok "game_rules_ja が消えた"
case "$prof" in *junk*) not_ok "未知のキーが通った" ;; *) ok "未知のキーは通さない" ;; esac
printf '#!/bin/bash\nprintf %%s %s\n' "'{\"schema\":1,\"game\":\"hanjuku-hero\",\"game_rules\":\"$long\"}'" >"$TMP/l2.sh"; chmod +x "$TMP/l2.sh"; DOCICH_COMMENT_PROFILE="$TMP/l2.sh"
prof2=$(_comment_game_profile); len=$(_comment_profile_field "$prof2" game_rules | python3 -c "import sys; print(len(sys.stdin.read()))")
[ "$len" -le 1500 ] && ok "game_rules は1500文字で切る (len=$len)" || not_ok "上限なし (len=$len)"

# --- 5. 本物の差し替えコードを実行: 既定はソ連ゲームのルール、profile があれば差し替え ---
awk 'index($0, "comment_game_rules.md\"") && !f {f=1} f {print} f && /game_rules_ja/ {g=1} g && /^\t\tfi$/ {exit}' "$SRC" >"$TMP/slot_block.sh"
[ -s "$TMP/slot_block.sh" ] || { not_ok "差し替えコードを抽出できない"; exit 1; }
run_slots() { # mode_suffix profile_json -> "en|ja"
	local ELOOP_LIB_DIR="$ROOT" _mode_suffix="$1" _game_profile_json="$2"
	local _comment_persona="P" _comment_ui_memo="U" _comment_game_rules="" _comment_game_rules_ja=""
	eval "$(sed -n '/^\t\t_comment_game_rules=\$(cat/,$p' "$TMP/slot_block.sh" | head -2)"
	eval "$(sed -n '/if \[ "\$_mode_suffix" = "main" \] && \[ -n "\$_game_profile_json" \]; then/,$p' "$TMP/slot_block.sh")"
	printf '%s|%s' "${_comment_game_rules:0:40}" "${_comment_game_rules_ja:0:40}"
}
base=$(run_slots main "")
case "$base" in "【Main Game Basic Rules (not current Sore|"*"本編ゲームの基本ルール"*) ok "profile なしなら既定(ソ連ゲームのルール)" ;; *) not_ok "既定: $base" ;; esac
res=$(run_slots main '{"game":"hanjuku-hero","game_rules":"HJ-RULES-EN","game_rules_ja":"HJ-RULES-JA"}')
[ "$res" = "HJ-RULES-EN|HJ-RULES-JA" ] && ok "profile があれば英語版・日本語版とも差し替える" || not_ok "差し替え: $res"
res=$(run_slots main '{"game":"hanjuku-hero","game_rules":"HJ-RULES-EN"}')
case "$res" in "HJ-RULES-EN|"*"本編ゲームの基本ルール"*) ok "キーが片方だけならもう片方は既定のまま" ;; *) not_ok "片方のみ: $res" ;; esac
res=$(run_slots main '{"game":"hanjuku-hero","game_rules":"","game_rules_ja":""}')
[ "$res" = "$base" ] && ok "空文字の profile 値では既定を消さない" || not_ok "空文字で既定が消えた: $res"
res=$(run_slots main '{broken'); [ "$res" = "$base" ] && ok "壊れた profile は既定のまま" || not_ok "壊れた profile: $res"
res=$(run_slots soren91 '{"game":"hanjuku-hero","game_rules":"HJ-RULES-EN"}'); [ "$res" = "$base" ] && ok "soren91 モードでは差し替えない" || not_ok "soren91: $res"

[ "$FAIL" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$FAIL"
