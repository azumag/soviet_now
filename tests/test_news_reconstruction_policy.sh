#!/usr/bin/env bash
# ニュース素材をそのまま読み上げず、自分の言葉へ再構成するポリシーの回帰テスト。
#
# - 生成プロンプトが見出し・本文の丸読みと出典の読み上げを禁止していること
# - 生成後の品質チェックが素材の長い連続コピーを検出し、再生成へ回すこと
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

. "$ROOT/broadcast/radio_quality.sh"

CORNERS="$ROOT/broadcast/radio_corners.sh"
NEWS_PROMPT="$ROOT/prompts/radio_news.md"

# --- プロンプトのポリシー文言 ---
if grep -q '素材の見出し・本文・要約をそのまま読み上げないこと' "$CORNERS"; then
	ok 'news prompt forbids reading the source material verbatim'
else
	not_ok 'news prompt forbids reading the source material verbatim'
fi
if grep -q '見出しの文言をそのまま音読せず' "$CORNERS"; then
	ok 'news prompt forbids reading the headline verbatim'
else
	not_ok 'news prompt forbids reading the headline verbatim'
fi
if grep -q '見出しの文言をそのまま音読せず' "$NEWS_PROMPT"; then
	ok 'unused radio_news template follows the same reconstruction policy'
else
	not_ok 'unused radio_news template follows the same reconstruction policy'
fi
if grep -q 'タイトルを日本語で1文だけ読み上げること\|ニュース本文に入る前に' "$CORNERS" "$NEWS_PROMPT"; then
	not_ok 'old read-the-headline instruction is gone'
else
	ok 'old read-the-headline instruction is gone'
fi

# --- 品質チェック: 素材の丸読み検出 ---
material=$'■ 政府が新しい経済対策を発表\n政府は物価高に対応するため、新たな経済対策を発表した。給付金の支給と税制の見直しを柱とする内容で、来月から順次実施される予定だ。'
verbatim='政府は物価高に対応するため、新たな経済対策を発表した。給付金の支給と税制の見直しを柱とする内容で、来月から順次実施される予定です。'
reconstructed='政府は物価高への対応として新しい経済対策をまとめました。給付金と税制の見直しが柱で、来月から順次動き出す見通しです。関係者の説明では、対象世帯への周知が今後の焦点になります。'

verdict=$(_radio_quality_check "$verbatim" "news" "$material")
if [ "$verdict" = "FAIL:verbatim_source" ]; then
	ok 'verbatim body copy is detected for the news corner'
else
	not_ok "verbatim body copy is detected for the news corner (got: ${verdict})"
fi

verdict=$(_radio_quality_check "$reconstructed" "news" "$material")
if [ "$verdict" = "OK" ]; then
	ok 'reconstructed wording passes the quality check'
else
	not_ok "reconstructed wording passes the quality check (got: ${verdict})"
fi

verdict=$(_radio_quality_check "$verbatim" "theme" "$material")
if [ "$verdict" = "OK" ]; then
	ok 'non-news corners ignore the news material input'
else
	not_ok "non-news corners ignore the news material input (got: ${verdict})"
fi

verdict=$(_radio_quality_check "$verbatim" "news" "短い素材")
if [ "$verdict" = "OK" ]; then
	ok 'short material does not trigger verbatim detection'
else
	not_ok "short material does not trigger verbatim detection (got: ${verdict})"
fi

# --- 丸読み失敗時の再生成プロンプト ---
saved_prompt="$TMP/saved_prompt.txt"
printf '%s\n' '【本日のニュース素材】' >"$saved_prompt"
rewrite=$(_radio_build_rewrite_prompt "$saved_prompt" "$verbatim" "FAIL:verbatim_source")
if grep -q 'そのまま読み上げていました' "$rewrite" && grep -q 'あなた自身の言葉で書き直して' "$rewrite"; then
	ok 'verbatim failure produces a rewrite prompt that demands reconstruction'
else
	not_ok 'verbatim failure produces a rewrite prompt that demands reconstruction'
fi
rm -f "$rewrite" 2>/dev/null || true

exit "$FAIL"
