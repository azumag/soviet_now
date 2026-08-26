#!/usr/bin/env bash
# コメント返しの生成出力に推論ブロック(<think>…</think>)が混ざったとき、
# それが読み上げ本文へ出ないことを検証する。
#
# 実発生 (2026-08-27 00:42): deepseek 系が英語の思考を "</think>" 付きで漏らし、
# ラジオ側にはある _ai_guard_model_output がコメント側には通っていなかったため、
# 英語の思考文がそのまま VOICEVOX で読み上げられた
# (tmp/.comment_queue/spoken_history/20260827_004221_20235_main.txt に実物)。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_comment_guard_model_text()/,/^}/p' "$SRC" >"$TMP/fn_guard.sh"
[ -s "$TMP/fn_guard.sh" ] || { not_ok "extract _comment_guard_model_text"; exit 1; }
# _comment_guard_model_text が呼ぶので先に読み込む
sed -n '/^_comment_strip_worknote_head()/,/^}/p' "$SRC" >"$TMP/fn_worknote.sh"
[ -s "$TMP/fn_worknote.sh" ] || { not_ok "extract _comment_strip_worknote_head"; exit 1; }
# shellcheck source=/dev/null
. "$TMP/fn_worknote.sh"
# shellcheck source=/dev/null
. "$TMP/fn_guard.sh"

ELOOP_LIB_DIR="$ROOT"
export ELOOP_LIB_DIR
# ai_generate.sh の _ai_guard_model_output をそのまま使う（本番と同じ経路）
sed -n '/^_ai_guard_model_output()/,/^}/p' "$ROOT/lib/ai_generate.sh" >"$TMP/fn_ai_guard.sh"
[ -s "$TMP/fn_ai_guard.sh" ] || { not_ok "extract _ai_guard_model_output"; exit 1; }
# shellcheck source=/dev/null
. "$TMP/fn_ai_guard.sh"
DOCICH_BIN=""
export DOCICH_BIN

# --- 1. 孤立した </think> より前の英語思考が本文へ残らない（実発生の形） ---
leak=$(cat <<'LEAK'
The indicator script is failing in this sandboxed environment. Let me check if it's an existing pattern in the codebase.

nimdavirus: GeForce NOWとかって使えないのかしら？ (GeForce NOW, can't you use that?)

I need to reply to this comment in Japanese, polite style.</think>GeForce NOW、たしかに一見するとGPUがない問題を一発で解決できそうに見えますね。ただあれはクラウド上のGPUマシンで対応タイトルを動かして映像だけを飛ばすサービスなので、そのまま載せられる仕組みではないんです。
LEAK
)
out=$(_comment_guard_model_text "$leak")
case "$out" in *"GeForce NOW、たしかに"*) ok "本文の日本語は残る" ;; *) not_ok "本文が消えた: $out" ;; esac
case "$out" in *"Let me check"*|*"I need to reply"*|*"indicator script"*) not_ok "英語の思考が残っている: $out" ;; *) ok "英語の思考が落ちる" ;; esac
case "$out" in *"</think>"*|*"<think>"*) not_ok "think タグが残っている: $out" ;; *) ok "think タグが残らない" ;; esac

# --- 2. 対応ペアの <think>…</think> も中身ごと落ちる ---
out=$(_comment_guard_model_text "<think>internal reasoning here</think>あずまぐさん、ありがとうございます。")
case "$out" in *"internal reasoning"*) not_ok "対応ペアの思考が残っている: $out" ;; *) ok "対応ペアの思考が落ちる" ;; esac
case "$out" in *"あずまぐさん、ありがとうございます。"*) ok "対応ペア後の本文が残る" ;; *) not_ok "対応ペア後の本文が消えた: $out" ;; esac

# --- 3. 普通の返答は素通しする（誤爆しない） ---
plain="nimdavirusさん、コメントありがとうございます。いまの盤面はロシアが2つ並んでいて、次の一手でソ連まで届く可能性があります。"
out=$(_comment_guard_model_text "$plain")
[ "$out" = "$plain" ] && ok "通常の返答は素通しする" || not_ok "通常の返答が変化した: $out"

# --- 4. ===SING=== の楽譜JSONを壊さない（SING抽出より前にガードを通すため） ---
sing=$(printf '%s\n' 'リクエストありがとうございます。歌います。' '' '===SING===' '{"notes": [{"key": 60, "frame_length": 15, "lyric": "き"}], "tempo": 120}' '===SING===' '' 'いかがでしたか。')
out=$(_comment_guard_model_text "$sing")
case "$out" in *'"notes"'*) ok "楽譜JSONが残る" ;; *) not_ok "楽譜JSONが消えた: $out" ;; esac
case "$out" in *'===SING==='*) ok "SINGマーカーが残る" ;; *) not_ok "SINGマーカーが消えた: $out" ;; esac

# --- 5. 空入力で落ちない ---
out=$(_comment_guard_model_text "")
[ -z "$out" ] && ok "空入力は空を返す" || not_ok "空入力で何か返した: $out"

# --- 6. 配線: 候補検証・本文生成・翻訳の3経路でガードを通している ---
grep -q 'guarded=$(_comment_guard_model_text "$raw")' "$SRC" && ok "候補検証でガードを通す" || not_ok "候補検証がガードを通していない"
grep -q 'attempt_talk=$(_comment_guard_model_text "$attempt_talk")' "$SRC" && ok "本文生成でガードを通す" || not_ok "本文生成がガードを通していない"
grep -q 'output=$(_comment_guard_model_text "$output")' "$SRC" && ok "翻訳でガードを通す" || not_ok "翻訳がガードを通していない"
# ガードは _clean_comment_talk より前に通す（SING/ADVICE 抽出前）
guard_line=$(grep -n 'attempt_talk=$(_comment_guard_model_text' "$SRC" | head -1 | cut -d: -f1)
clean_line=$(grep -n 'attempt_talk=$(_clean_comment_talk "$attempt_talk" 1)' "$SRC" | head -1 | cut -d: -f1)
if [ -n "$guard_line" ] && [ -n "$clean_line" ] && [ "$guard_line" -lt "$clean_line" ]; then
	ok "ガードは _clean_comment_talk より前"
else
	not_ok "ガードの位置が不正 (guard=$guard_line clean=$clean_line)"
fi


# ==== 作業メモ + --- 区切りの漏れ (ユーザー選択 B: コメント側だけで判定) ====
# --- 7. 実発生の形: 作業メモ + --- + 本文 → 本文だけ残る ---
leak_ja=$(printf '%s\n' \
	'インジケーターのスクリプトはこのサンドボックス環境ではネットワーク制約で実行できないようですが、コメント返しの生成自体は完了しています。' \
	'' \
	'以下、2件のコメント返しです。' \
	'' \
	'---' \
	'' \
	'あずまぐさん、めっちゃいい感じと言ってもらえて嬉しいです。' \
	'' \
	'あずまぐさん、そうなんです、ロシアで国々が分断されてしまったんです。')
out=$(printf '%s' "$leak_ja" | _comment_strip_worknote_head)
case "$out" in *"インジケーター"*|*"以下、2件"*) not_ok "作業メモが残っている: $out" ;; *) ok "作業メモの前置きが落ちる" ;; esac
case "$out" in *"めっちゃいい感じ"*) ok "1件目の本文が残る" ;; *) not_ok "1件目が消えた: $out" ;; esac
case "$out" in *"ロシアで国々が分断"*) ok "2件目の本文が残る" ;; *) not_ok "2件目が消えた: $out" ;; esac
case "$out" in *"---"*) not_ok "罫線が残っている: $out" ;; *) ok "罫線が落ちる" ;; esac

# --- 8. 誤爆しない: 先頭が視聴者への呼びかけなら前置きを落とさない ---
legit=$(printf '%s\n' 'あずまぐさん、スクリプトが実行できないという話、興味深いです。' '' '---' '' 'つづきの話です。')
out=$(printf '%s' "$legit" | _comment_strip_worknote_head)
case "$out" in *"あずまぐさん、スクリプトが実行できない"*) ok "呼びかけ付きの前置きは落とさない" ;; *) not_ok "本物の返答が削られた: $out" ;; esac

# --- 9. 誤爆しない: 作業メモ語彙が無ければ落とさない ---
legit2=$(printf '%s\n' '今日はここまでの戦績をまとめてみます。' '' '---' '' 'ロシアは2回できました。')
out=$(printf '%s' "$legit2" | _comment_strip_worknote_head)
case "$out" in *"今日はここまでの戦績"*) ok "作業メモ語彙が無ければ落とさない" ;; *) not_ok "無関係な前置きが削られた: $out" ;; esac

# --- 10. 区切りの後ろが空なら落とさない ---
tail_empty=$(printf '%s\n' '以下、2件のコメント返しです。' '---' '')
out=$(printf '%s' "$tail_empty" | _comment_strip_worknote_head)
[ -n "$out" ] && ok "区切りの後ろが空なら元テキストを返す" || not_ok "全部消えた"

# --- 11. 区切りが無い通常の返答は素通し ---
plain2='nimdavirusさん、コメントありがとうございます。いまの盤面はロシアが2つ並んでいます。'
out=$(printf '%s' "$plain2" | _comment_strip_worknote_head)
[ "$out" = "$plain2" ] && ok "区切りが無ければ素通し" || not_ok "通常の返答が変化した: $out"

# --- 12. ===SING=== を罫線と誤認しない ---
out=$(printf '%s' "$sing" | _comment_strip_worknote_head)
case "$out" in *'===SING==='*) ok "SINGマーカーを罫線と誤認しない" ;; *) not_ok "SINGマーカーが消えた: $out" ;; esac
case "$out" in *'"notes"'*) ok "楽譜JSONが残る(worknote段)" ;; *) not_ok "楽譜JSONが消えた: $out" ;; esac

# --- 13. 空入力で落ちない ---
out=$(printf '%s' "" | _comment_strip_worknote_head)
[ -z "$out" ] && ok "空入力は空のまま" || not_ok "空入力で何か返した: $out"

# --- 14. 配線: ガード本体が worknote 判定を通している ---
grep -q '_ai_guard_model_output 2>/dev/null | _comment_strip_worknote_head' "$SRC" \
	&& ok "ガードが worknote 判定を通す" || not_ok "worknote 判定が配線されていない"
# 共通ガード(ラジオ共用)は変更していないこと
grep -q "インジケーター" "$ROOT/lib/model_output_guard.py" \
	&& not_ok "共通ガードにコメント固有の語彙が混ざっている" || ok "共通ガード(ラジオ共用)は無変更"

[ "$FAIL" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$FAIL"
