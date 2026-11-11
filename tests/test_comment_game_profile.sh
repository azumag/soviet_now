#!/usr/bin/env bash
# docich が持つゲーム別 profile (半熟英雄など) をコメントプロンプトの既存スロットへ当てる処理の検証。
#
# 正本は docich (docich.comment.game_profile / bin/docich-comment-profile)。ここは呼び出し側だけを見る:
# - launcher の不在・失敗・空・異常・タイムアウトは必ず「profile なし」(= 従来どおり)になる
# - 無害化(テンプレート/シェルのメタ文字除去)と、ソレン画面・soren91 では使わないこと
# - 状態メモ・ペルソナ・UIメモ・運用メモ行の差し替えが本物のコードで効くこと
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

for fn in _comment_game_profile _comment_profile_field _comment_profile_apply_persona _build_comment_ops_context; do
	sed -n "/^${fn}()/,/^}/p" "$SRC" >"$TMP/fn_${fn}.sh"
	[ -s "$TMP/fn_${fn}.sh" ] || { not_ok "extract ${fn}"; exit 1; }
	# shellcheck source=/dev/null
	. "$TMP/fn_${fn}.sh"
done

CANON="$TMP/canonical.json"
export DOCICH_GAME_SWITCH_CANONICAL_FILE="$CANON"
export ELOOP_LIB_DIR="$TMP" TMP_STATE_DIR="tmp/state"
mkdir -p "$TMP/tmp/state" "$TMP/prompts"
set_game() { printf '{"active":{"game":"%s","generation":3},"phase":"ready"}' "$1" >"$CANON"; }

make_launcher() { # name, body
	printf '#!/bin/bash\n%s\n' "$2" >"$TMP/$1"
	chmod +x "$TMP/$1"
	printf '%s' "$TMP/$1"
}
GOOD='{"schema":1,"game":"hanjuku-hero","persona_replace":[["自分自身がソ連ゲームをプレイしているプレイヤーでもあります。","ゲームの担当は現在のメイン画面に従います。"]],"game_state":"現在のメイン画面: hanjuku-hero(PROFILE-STATE)","ui_memo":"","ops_lines":["- PROFILE-OPS-HEADER","  ・PROFILE-OPS-BODY"]}'

# --- 1. 正常: 半熟が画面なら launcher の内容が検証済み JSON で返る ---
set_game hanjuku-hero
export DOCICH_COMMENT_PROFILE
DOCICH_COMMENT_PROFILE=$(make_launcher good.sh "echo \"called:\$1\" >>\"$TMP/calls\"; printf '%s' '$GOOD'")
prof=$(_comment_game_profile)
[ -n "$prof" ] && ok "半熟が画面なら profile が返る" || not_ok "profile が空"
[ "$(_comment_profile_field "$prof" game_state)" = "現在のメイン画面: hanjuku-hero(PROFILE-STATE)" ] && ok "game_state を取り出せる" || not_ok "game_state: $prof"
grep -q "called:hanjuku-hero" "$TMP/calls" && ok "launcher へ画面のゲーム名を渡す" || not_ok "launcher 呼び出しなし"
_comment_profile_field "$prof" ui_memo >/dev/null && ok "空の ui_memo は有効な値(rc 0)" || not_ok "空 ui_memo が失敗扱い"
[ -z "$(_comment_profile_field "$prof" ui_memo)" ] && ok "空 ui_memo は空文字" || not_ok "ui_memo が空でない"
_comment_profile_field "$prof" nothing >/dev/null && not_ok "存在しないキーが成功" || ok "存在しないキーは失敗(rc 1)"
_comment_profile_field "" game_state >/dev/null && not_ok "空 JSON が成功" || ok "空 JSON は失敗(rc 1)"

# --- 2. fail-open: どんな失敗でも「profile なし」(空) ---
check_empty() { # label
	out=$(_comment_game_profile); [ -z "$out" ] && ok "$1 → profile なし" || not_ok "$1 → 空でない: $out"
}
DOCICH_COMMENT_PROFILE="$TMP/nonexistent"; check_empty "launcher 不在"
printf '#!/bin/bash\nprintf x\n' >"$TMP/noexec.sh"; DOCICH_COMMENT_PROFILE="$TMP/noexec.sh"; check_empty "実行権限なし"
DOCICH_COMMENT_PROFILE=$(make_launcher fail.sh "printf '%s' '$GOOD'; exit 1"); check_empty "launcher が失敗(rc 1)"
DOCICH_COMMENT_PROFILE=$(make_launcher empty.sh ":"); check_empty "launcher が空出力"
DOCICH_COMMENT_PROFILE=$(make_launcher broken.sh "printf '%s' '{not json'"); check_empty "壊れた JSON"
DOCICH_COMMENT_PROFILE=$(make_launcher schema2.sh "printf '%s' '${GOOD/\"schema\":1/\"schema\":2}'"); check_empty "schema 不一致"
DOCICH_COMMENT_PROFILE=$(make_launcher othergame.sh "printf '%s' '${GOOD/\"game\":\"hanjuku-hero\"/\"game\":\"robots\"}'"); check_empty "別ゲームの profile"
DOCICH_COMMENT_PROFILE=$(make_launcher huge.sh "head -c 20000 /dev/zero | tr '\\\\0' 'a'"); check_empty "出力が上限超過"
DOCICH_COMMENT_PROFILE=$(make_launcher hang.sh "exec sleep 30")
start=$SECONDS; check_empty "launcher がタイムアウト"
[ $((SECONDS - start)) -le 8 ] && ok "タイムアウトは約5秒で打ち切る" || not_ok "打ち切りが遅い ($((SECONDS - start))s)"

# --- 3. 無害化: テンプレート/シェルのメタ文字は除去され、長さは上限で切る ---
EVIL='{"schema":1,"game":"hanjuku-hero","game_state":"A$HOME`id`<b>\\\\z","ops_lines":["- L1 $(whoami)"],"persona_replace":[["x$y","z"],["ok旧","ok新"]]}'
DOCICH_COMMENT_PROFILE=$(make_launcher evil.sh "printf '%s' '$EVIL'")
prof=$(_comment_game_profile)
gs=$(_comment_profile_field "$prof" game_state)
case "$gs" in *'$'*|*'`'*|*'<'*|*'>'*|*'\'*) not_ok "メタ文字が残った: $gs" ;; *) ok "game_state のメタ文字を除去 ($gs)" ;; esac
case "$prof" in *'whoami)'*) ok "ops_lines の \$ のみ除去(括弧は文字として残る)" ;; *) not_ok "ops_lines: $prof" ;; esac
case "$prof" in *'$'*) not_ok "JSON に \$ が残った" ;; *) ok "JSON に \$ が残らない" ;; esac
case "$prof" in *'"x'*) not_ok "メタ文字を含む persona_replace の旧文が残った" ;; *) ok "メタ文字を含む persona_replace は捨てる" ;; esac
case "$prof" in *'ok旧'*) ok "健全な persona_replace は残る" ;; *) not_ok "健全な pair が消えた: $prof" ;; esac

# --- 4. ソレン画面・soren91 では launcher を呼ばない / 対応外ゲームは profile なし ---
rm -f "$TMP/calls"
DOCICH_COMMENT_PROFILE=$(make_launcher good2.sh "echo called >>\"$TMP/calls\"; printf '%s' '$GOOD'")
for g in sorengame soren91; do
	set_game "$g"; out=$(_comment_game_profile)
	[ -z "$out" ] && [ ! -e "$TMP/calls" ] && ok "$g 画面では launcher を呼ばず profile なし" || not_ok "$g 画面で呼んだ/返った"
done
set_game robots; DOCICH_COMMENT_PROFILE=$(make_launcher none.sh ":"); check_empty "対応外ゲーム(launcher は何も出さない)"
rm -f "$CANON"; DOCICH_COMMENT_PROFILE=$(make_launcher good3.sh "printf '%s' '$GOOD'"); check_empty "canonical 不在"
printf '%s' '{"active":{"game":"../etc;rm"}}' >"$CANON"; check_empty "不正なゲーム名は launcher に渡さない"

# --- 5. ペルソナ差し替え: 該当文だけ置換、無ければそのまま、profile 空ならそのまま ---
set_game hanjuku-hero; DOCICH_COMMENT_PROFILE=$(make_launcher good4.sh "printf '%s' '$GOOD'")
prof=$(_comment_game_profile)
persona='あなたはAIです。自分自身がソ連ゲームをプレイしているプレイヤーでもあります。視聴者に返事して。'
res=$(printf '%s' "$persona" | _comment_profile_apply_persona "$prof")
case "$res" in *"ゲームの担当は現在のメイン画面に従います。"*) ok "ペルソナのプレイヤー文を置換" ;; *) not_ok "置換されない: $res" ;; esac
case "$res" in *"ソ連ゲームをプレイしている"*) not_ok "旧文が残った: $res" ;; *) ok "旧文は残らない" ;; esac
case "$res" in "あなたはAIです。"*"視聴者に返事して。") ok "前後の文は保たれる" ;; *) not_ok "前後が崩れた: $res" ;; esac
res=$(printf '%s' "別のペルソナ文" | _comment_profile_apply_persona "$prof"); [ "$res" = "別のペルソナ文" ] && ok "該当文が無ければそのまま" || not_ok "変化した: $res"
res=$(printf '%s' "$persona" | _comment_profile_apply_persona ""); [ "$res" = "$persona" ] && ok "profile 空ならそのまま" || not_ok "変化した: $res"
res=$(printf '%s' "$persona" | _comment_profile_apply_persona "{broken"); [ "$res" = "$persona" ] && ok "profile 壊れならそのまま" || not_ok "変化した: $res"

# --- 6. 本物の呼び出しコード(状態メモ・ペルソナ/UIメモ)をスタブ付きで実行 ---
_build_comment_game_context() { printf 'SOVIET-STATS'; }
_comment_soren91_game_state_note() { printf 'SOREN91-NOTE'; }
awk '/^\tlocal _game_profile_json=""$/{f=1} f{print} f&&/_build_comment_game_context "\$GAME_STATE"/{g=1} g&&/^\tfi$/{exit}' "$SRC" >"$TMP/gs_block.sh"
[ -s "$TMP/gs_block.sh" ] || { not_ok "状態メモの呼び出しブロックを抽出できない"; exit 1; }
run_gs() { # mode
	local _spoken_ctx_mode="$1" GAME_STATE=x
	eval "$(cat "$TMP/gs_block.sh")"
	printf '%s' "$game_state_context"
}
set_game hanjuku-hero; DOCICH_COMMENT_PROFILE=$(make_launcher good5.sh "printf '%s' '$GOOD'")
[ "$(run_gs main)" = "現在のメイン画面: hanjuku-hero(PROFILE-STATE)" ] && ok "半熟中はソ連ゲームの統計でなく profile の状態メモ" || not_ok "状態メモ: $(run_gs main)"
[ "$(run_gs soren91)" = "SOREN91-NOTE" ] && ok "soren91 中は従来の差し替えメモ" || not_ok "soren91: $(run_gs soren91)"
DOCICH_COMMENT_PROFILE="$TMP/nonexistent"
[ "$(run_gs main)" = "SOVIET-STATS" ] && ok "launcher 不在なら従来どおり(ソ連ゲームの状態メモ)" || not_ok "従来動作でない: $(run_gs main)"
set_game sorengame; DOCICH_COMMENT_PROFILE=$(make_launcher good6.sh "printf '%s' '$GOOD'")
[ "$(run_gs main)" = "SOVIET-STATS" ] && ok "ソレン画面は従来どおり" || not_ok "ソレン画面: $(run_gs main)"

awk '/if \[ "\$_mode_suffix" = "main" \] && \[ -n "\$_game_profile_json" \]; then/{f=1} f{print} f&&/^\t\tfi$/{exit}' "$SRC" >"$TMP/persona_block.sh"
[ -s "$TMP/persona_block.sh" ] || { not_ok "ペルソナの呼び出しブロックを抽出できない"; exit 1; }
run_persona() { # mode_suffix profile_json -> "persona|ui"
	local _mode_suffix="$1" _game_profile_json="$2"
	local _comment_persona="$persona" _comment_ui_memo="SOVIET-UI-MEMO"
	eval "$(cat "$TMP/persona_block.sh")"
	printf '%s|%s' "$_comment_persona" "$_comment_ui_memo"
}
set_game hanjuku-hero; DOCICH_COMMENT_PROFILE=$(make_launcher good7.sh "printf '%s' '$GOOD'"); prof=$(_comment_game_profile)
res=$(run_persona main "$prof")
case "$res" in *"ゲームの担当は現在のメイン画面に従います。"*"|") ok "半熟中: ペルソナ置換 + ソ連UIメモを空にする" ;; *) not_ok "ペルソナ/UI: $res" ;; esac
res=$(run_persona soren91 "$prof"); case "$res" in *"SOVIET-UI-MEMO") ok "soren91 モードでは差し替えない" ;; *) not_ok "soren91 で変化: $res" ;; esac
res=$(run_persona main ""); case "$res" in "$persona|SOVIET-UI-MEMO") ok "profile なしなら従来どおり" ;; *) not_ok "従来動作でない: $res" ;; esac
NOUI='{"game":"hanjuku-hero","game_state":"s"}'
res=$(run_persona main "$NOUI"); case "$res" in *"|SOVIET-UI-MEMO") ok "ui_memo キーが無ければUIメモはそのまま" ;; *) not_ok "UIメモが変化: $res" ;; esac

# --- 7. 運用メモ行: profile の行だけを使う（内蔵の文面は無い） ---
set_game hanjuku-hero
prof=$(DOCICH_COMMENT_PROFILE=$(make_launcher good8.sh "printf '%s' '$GOOD'") _comment_game_profile)
out=$(_build_comment_ops_context main "$prof")
case "$out" in *PROFILE-OPS-HEADER*PROFILE-OPS-BODY*) ok "運用メモに profile の行が入る" ;; *) not_ok "profile 行なし: $out" ;; esac
[ "$(printf '%s\n' "$out" | sed -n '3p')" = "- PROFILE-OPS-HEADER" ] && ok "profile 行はルーティング行の直後" || not_ok "位置: $(printf '%s\n' "$out" | sed -n '1,4p')"
out=$(_build_comment_ops_context main "")
case "$out" in *PROFILE-OPS*|*"半熟英雄BOTの仕組み"*) not_ok "profile なしで半熟の行が出た" ;; *) ok "profile なしなら半熟の行は出ない(fail-open)" ;; esac
out=$(_build_comment_ops_context main '{"game":"robots","ops_lines":["- OTHER-GAME-LINE"]}')
case "$out" in *OTHER-GAME-LINE*) not_ok "別ゲームの profile を使った" ;; *) ok "別ゲームの profile は使わない" ;; esac
out=$(_build_comment_ops_context main '{broken'); case "$out" in *PROFILE*) not_ok "壊れた profile で行が出た" ;; *) ok "壊れた profile でも落ちず何も足さない" ;; esac
out=$(COMMENT_OPS_CONTEXT_MAX_CHARS=200 _build_comment_ops_context main "$prof")
case "$out" in *PROFILE-OPS-BODY*) ok "文字数上限が小さくても profile の行は切れない" ;; *) not_ok "上限で切れた: $out" ;; esac
set_game robots; out=$(_build_comment_ops_context main "$prof"); case "$out" in *PROFILE-OPS*) not_ok "半熟以外の画面に出た" ;; *) ok "半熟以外の画面には出ない" ;; esac
set_game hanjuku-hero; out=$(_build_comment_ops_context soren91 "$prof"); case "$out" in *PROFILE-OPS*) not_ok "soren91 に出た" ;; *) ok "soren91 モードには出ない" ;; esac

# --- 7b. 適用ログ: ゲーム名と結果だけ。本文は出さない。ソレン/soren91 では出さない ---
sed -n "/^_comment_log_game_profile()/,/^}/p" "$SRC" >"$TMP/fn_log.sh"
[ -s "$TMP/fn_log.sh" ] || { not_ok "extract _comment_log_game_profile"; exit 1; }
# shellcheck source=/dev/null
. "$TMP/fn_log.sh"
LOGF="$TMP/applied.log"; log() { printf '%s\n' "$*" >>"$LOGF"; }
: >"$LOGF"; set_game hanjuku-hero; _comment_log_game_profile "$prof"
grep -q "ゲームprofile: 適用 game=hanjuku-hero" "$LOGF" && ok "適用時: ゲーム名つきで1行ログ" || not_ok "適用ログなし: $(cat "$LOGF")"
case "$(cat "$LOGF")" in *PROFILE-STATE*|*PROFILE-OPS*) not_ok "ログに profile の本文が出た" ;; *) ok "ログに profile の本文は出さない" ;; esac
: >"$LOGF"; _comment_log_game_profile ""
grep -q "ゲームprofile: なし(従来のプロンプト) game=hanjuku-hero" "$LOGF" && ok "profile が空でも非ソ連ゲームなら理由つきでログ" || not_ok "空ログなし: $(cat "$LOGF")"
for g in sorengame soren91; do : >"$LOGF"; set_game "$g"; _comment_log_game_profile ""; [ ! -s "$LOGF" ] && ok "$g 画面ではログを出さない" || not_ok "$g でログが出た"; done
: >"$LOGF"; rm -f "$CANON"; _comment_log_game_profile ""; [ ! -s "$LOGF" ] && ok "canonical 不在ではログを出さない" || not_ok "canonical 不在でログ"

# --- 8. 配線: 呼び出しは fail-open で、soren91 では取得しない ---
grep -q '_game_profile_json=$(_comment_game_profile 2>/dev/null || true)' "$SRC" && ok "profile 取得は失敗しても継続する" || not_ok "取得の fail-open が無い"
grep -q '_build_comment_ops_context "$_spoken_ctx_mode" "$_game_profile_json"' "$SRC" && ok "運用メモへ profile を渡している" || not_ok "運用メモへ渡していない"
grep -q '_comment_log_game_profile "$_game_profile_json" || true' "$SRC" && ok "profile 取得の直後に適用ログを出す(失敗しても継続)" || not_ok "適用ログの呼び出しが無い"
n=$(grep -c '"\$_spoken_ctx_mode" != "soren91"' "$SRC"); [ "$n" -ge 1 ] && ok "soren91 では profile を取得しない" || not_ok "soren91 ガードなし"

[ "$FAIL" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$FAIL"
