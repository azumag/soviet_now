#!/usr/bin/env bash
# ニュース素材をそのまま読み上げず、自分の言葉へ再構成するポリシーの回帰テスト。
#
# - 生成プロンプトが見出し・本文の丸読みと出典の読み上げを禁止していること
# - 生成後の品質チェックが素材の長い連続コピーを検出し、再生成へ回すこと
# - 生成後の品質チェックが英語・簡体字中国語の混入を検出し、再生成へ回すこと
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

# --- 品質チェック: 外国語混入検出（英語・簡体字中国語） ---
# 日本語の読み上げに英文・英単語・簡体字が混ざると、日本語TTSが日本語の音素で
# 誤読し、聞き取れない読み上げになる。短い固有名詞（NATO等）は許容する。
ja_normal=$'こんばんは、現在時刻は21時です。本日のニュースです。ロシアの無人機がウクライナ西部、ポーランド国境に近い地域を攻撃したという話です。ポーランド国境はNATO圏との境界でもあり、外交官団を乗せた列車が通過した直後だったという点が注目されています。ロシアはエネルギーインフラへの攻撃を続けており、EU諸国も対応を協議しています。事実関係を確認しながら、今後の展開を見ていきたいと思います。'
word_salad=$'こんばんは、現在時刻は21時です。ニュースを一つ。ロシアの無人機がウクライナ西部を攻撃したという話です。外交官たちがキーウから戻る列車で数分の違いで巻き込まれるところでした。ここから先は推測になりますが、合意がある apis aside、実際には戦闘が続いているという現実です。直接的に非難する hard な声明もあれば、事実確認が必要と terraceamine remain ものもあるでしょう。協議の場での heavy な論拠になります。歴史的に見ると、外交官の安全という問題は uppet に複雑です。この境界で外交官を危険にさらすことは、 escalate のリスクを computed に高める行為です。'
english_run=$'新しい橋が開通しました。A bridge is more than just a structure. It is a place where people live. 地元の人々は思い出を語っています。長年親しまれた旧橋が取り壊され、近代的な新橋に生まれ変わりました。'
simp_mix=$'今回の選挙の話です。这次の結果は非常に重要で、支持基盤を追っていくと保守と護憲の対立が見えます。投票率の时间帯ごとの変化も注目されます。选择の判断は难しいですが、现场の空気は伝わってきます。それでも住民の関心は高いままだと思います。'

verdict=$(_radio_quality_check "$ja_normal" "news")
if [ "$verdict" = "OK" ]; then
	ok 'pure Japanese with short acronyms passes'
else
	not_ok "pure Japanese with short acronyms passes (got: ${verdict})"
fi

verdict=$(_radio_quality_check "$word_salad" "news")
if [ "$verdict" = "FAIL:mixed_language" ]; then
	ok 'English words mixed into Japanese are detected'
else
	not_ok "English words mixed into Japanese are detected (got: ${verdict})"
fi

verdict=$(_radio_quality_check "$english_run" "news")
if [ "$verdict" = "FAIL:mixed_language" ]; then
	ok 'English sentences are detected'
else
	not_ok "English sentences are detected (got: ${verdict})"
fi

verdict=$(_radio_quality_check "$simp_mix" "news")
if [ "$verdict" = "FAIL:mixed_language" ]; then
	ok 'simplified Chinese mixed into Japanese is detected'
else
	not_ok "simplified Chinese mixed into Japanese is detected (got: ${verdict})"
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

# --- 外国語混入失敗時の再生成プロンプト ---
rewrite=$(_radio_build_rewrite_prompt "$saved_prompt" "$word_salad" "FAIL:mixed_language")
if grep -q '外国語がそのまま混ざっていました' "$rewrite" && grep -q 'カタカナで表記すること' "$rewrite"; then
	ok 'mixed language failure produces a rewrite prompt that demands katakana'
else
	not_ok 'mixed language failure produces a rewrite prompt that demands katakana'
fi
rm -f "$rewrite" 2>/dev/null || true

exit "$FAIL"
