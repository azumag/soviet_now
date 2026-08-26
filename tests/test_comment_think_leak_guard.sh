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

[ "$FAIL" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$FAIL"
