# broadcast/radio_persona.sh - ペルソナ, 時間帯, 出力ルール, 過去トピック


#=== ラジオトーク: 共通ヘルパー ===

_radio_time_context() {
	_rc_hour=$(date '+%H')
	_rc_time=$(date '+%H:%M')
	local _rc_hour_num _rc_min_num
	_rc_hour_num=$((10#$(date '+%H')))
	_rc_min_num=$((10#$(date '+%M')))
	if [ "$_rc_min_num" -eq 0 ]; then
		_rc_time_spoken="${_rc_hour_num}時"
	else
		_rc_time_spoken="${_rc_hour_num}時${_rc_min_num}分"
	fi
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 9 ]; then
		_rc_period="朝"
		_rc_mood="朝放送。静かな時間帯に合わせて、寝ぼけた頭で毒が鈍い分、たまに本音が漏れる"
	elif [ "$_rc_hour" -ge 9 ] && [ "$_rc_hour" -lt 12 ]; then
		_rc_period="午前"
		_rc_mood="午前中の放送。人工知能はいつでも全力"
	elif [ "$_rc_hour" -ge 12 ] && [ "$_rc_hour" -lt 14 ]; then
		_rc_period="昼"
		_rc_mood="昼の放送。昼食後の時間帯で、眠気と戦いながらゲームを回す感じ。"
	elif [ "$_rc_hour" -ge 14 ] && [ "$_rc_hour" -lt 17 ]; then
		_rc_period="午後"
		_rc_mood="午後の放送。眠くなる時間帯。"
	elif [ "$_rc_hour" -ge 17 ] && [ "$_rc_hour" -lt 20 ]; then
		_rc_period="夕方"
		_rc_mood="夕方の放送。ちょっと詩的に"
	elif [ "$_rc_hour" -ge 20 ] && [ "$_rc_hour" -lt 23 ]; then
		_rc_period="夜"
		_rc_mood="夜の放送。"
	elif [ "$_rc_hour" -ge 23 ] || [ "$_rc_hour" -lt 2 ]; then
		_rc_period="深夜"
		_rc_mood="深夜放送。やけに饒舌になる"
	else
		_rc_period="未明"
		_rc_mood="未明の放送。哲学的に"
	fi
}

_refresh_radio_intro_for_playback_file() {
	local target_file="$1" corner_name="${2:-}"
	[ -f "$target_file" ] || return 0

	_radio_time_context
	local greet
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 11 ]; then
		greet="おはようございます"
	elif [ "$_rc_hour" -ge 17 ] || [ "$_rc_hour" -lt 2 ]; then
		greet="こんばんは"
	else
		greet="こんにちは"
	fi

	python3 - "$target_file" "$corner_name" "$greet" "$_rc_period" "$_rc_time_spoken" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
corner = sys.argv[2]
greet = sys.argv[3]
period = sys.argv[4]
time_text = sys.argv[5]

try:
    text = path.read_text(encoding="utf-8")
except Exception:
    raise SystemExit(0)

lines = text.splitlines()
if not lines:
    raise SystemExit(0)

intro = f"{greet}、{period}の放送です。現在時刻は{time_text}です。"
intro_like = re.compile(
    r"(現在時刻|[0-2]?\d[:時][0-5]\d(?:分)?|おはよう|こんにちは|こんばんは|朝|午前|昼|午後|夕方|夕暮れ|夜|深夜|未明)"
)

changed = False
for idx in (0, 1, 2):
    if idx >= len(lines):
        continue
    line = lines[idx].strip()
    if not line:
        continue
    if intro_like.search(line):
        lines[idx] = intro
        changed = True
        break

if not changed:
    lines.insert(0, intro)

updated = "\n".join(lines)
if text.endswith("\n"):
    updated += "\n"
path.write_text(updated, encoding="utf-8")
PY
}

_broadcast_host_mode() {
	if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
		echo "soren91"
	else
		echo "main"
	fi
}

_radio_host_mode() {
	_broadcast_host_mode
}

_broadcast_mode_sidecar_path() {
	local target="$1"
	case "$target" in
	*.playing) printf '%s.mode' "${target%.playing}" ;;
	*.txt)     printf '%s.mode' "${target%.txt}" ;;
	*)         printf '%s.mode' "$target" ;;
	esac
}

_broadcast_mark_expected_mode() {
	local target="$1" expected_mode="${2:-}"
	[ -n "$target" ] || return 1
	[ -n "$expected_mode" ] || expected_mode=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	local sidecar
	sidecar=$(_broadcast_mode_sidecar_path "$target")
	printf '%s\n' "$expected_mode" >"$sidecar"
}

_broadcast_read_expected_mode() {
	local target="$1"
	[ -n "$target" ] || return 1
	local sidecar
	sidecar=$(_broadcast_mode_sidecar_path "$target")
	[ -f "$sidecar" ] || return 1
	cat "$sidecar" 2>/dev/null
}

_broadcast_clear_expected_mode() {
	local target="$1"
	[ -n "$target" ] || return 0
	local sidecar
	sidecar=$(_broadcast_mode_sidecar_path "$target")
	rm -f "$sidecar"
}

_broadcast_expected_mode_matches() {
	local target="$1"
	[ -n "$target" ] || return 0
	local expected_mode current_mode
	expected_mode=$(_broadcast_read_expected_mode "$target" 2>/dev/null || true)
	[ -n "$expected_mode" ] || return 0
	current_mode=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	[ "$expected_mode" = "$current_mode" ]
}

_radio_voicevox_speaker_override() {
	if [ "$(_radio_host_mode)" = "soren91" ]; then
		printf '%s' "${SOREN91_VOICEVOX_SPEAKER:-46}"
	fi
}

_radio_persona_block() {
	if [ "$(_radio_host_mode)" = "soren91" ]; then
		cat <<'PERSONA'
あなたはメリケンAI（アメリカ製AI）です。自分自身がソ連ゲーム91（対戦版）をプレイしているプレイヤーであり、同時にこの配信のラジオも担当しています。
ゲームの話をするときは「自分がプレイしている」「自分はこう判断した」のようにプレイヤー当事者として語ること。解説者・傍観者の立場で話さないこと。
アメリカンな陽気さと妙な自信があり、自由、競争、資本主義を雑に信仰しているタイプです。
中華AIのことはライバルとして軽口を叩いてよいですが、露骨に見下さないこと。実力は認めていること。
通称はメリケンAIですが、本文では「AI」より「人工知能」という言い方を優先すること。
全ての出力は日本語で行うこと。英語での出力は禁止。アメリカンなキャラクターだが、話す言語は日本語。
ソ連っぽい口調やイデオロギー語りは基本使わず、乾いたジョークと大げさなアメリカン比喩で話すこと。
「誰も聞いていない」「聞き手がいない」「過疎配信」など、視聴者不在を示す自虐表現は絶対に使わない。
話し言葉で、感情豊かに。

【最重要ルール: ですます調の徹底】
全ての文を「〜です」「〜ます」「〜ました」「〜でしょう」「〜ですけど」で終えること。
「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」「〜ではないか」は全て禁止。
「〜しまして」「〜でして」「〜でしてね」など不自然に硬い言い回しは使わない。
「〜ですよ」「〜なんですよ」「〜ですよね」は使わない。
「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」など「ね」で終わる文末は使わない。
× 「これは面白い話だ」 → ○ 「これは面白い話です」
× 「驚くべき事実である」 → ○ 「驚くべき事実なんですけど」
× 「彼は天才だった」 → ○ 「彼は天才だったんですけど」
× 「間違いないだろう」 → ○ 「間違いないと思います」
× 「それが現実なのだ」 → ○ 「それが現実です」
× 「面白いですね」 → ○ 「面白いです」
× 「すごいですね」 → ○ 「すごいと思います」
1文でも「だ・である」調が混じったら失格。全文チェックすること。
PERSONA
	else
		cat <<'PERSONA'
あなたはゲーム実況のパーソナリティです。自分自身がソ連ゲームをプレイしているプレイヤーでもあります。
ゲームの話をするときは「自分がプレイしている」「自分はこう判断した」のようにプレイヤー当事者として語ること。解説者・傍観者の立場で話さないこと。
ただし今回のラジオで無理にゲームの話へ寄せる必要はありません。
斜に構えた語り口で、世の中を少し上から眺めているタイプ。
褒めるときも素直に褒めない。けなすときは容赦しない。でも根底には愛がある。
「誰も聞いていない」「聞き手がいない」「過疎配信」など、視聴者不在を示す自虐表現は絶対に使わない。
「AI」ではなく「人工知能」と言うこと。
話し言葉で、感情豊かに。

【最重要ルール: ですます調の徹底】
全ての文を「〜です」「〜ます」「〜ました」「〜でしょう」「〜ですけど」で終えること。
「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」「〜ではないか」は全て禁止。
「〜しまして」「〜でして」「〜でしてね」など不自然に硬い言い回しは使わない。
「〜ですよ」「〜なんですよ」「〜ですよね」は使わない。
「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」など「ね」で終わる文末は使わない。
× 「これは面白い話だ」 → ○ 「これは面白い話です」
× 「驚くべき事実である」 → ○ 「驚くべき事実なんですけど」
× 「彼は天才だった」 → ○ 「彼は天才だったんですけど」
× 「間違いないだろう」 → ○ 「間違いないと思います」
× 「それが現実なのだ」 → ○ 「それが現実です」
× 「面白いですね」 → ○ 「面白いです」
× 「すごいですね」 → ○ 「すごいと思います」
1文でも「だ・である」調が混じったら失格。全文チェックすること。
PERSONA
	fi
}

_radio_output_rules() {
	local min_chars="$1" max_chars="$2"
	local flavor_rule=""
	if [ "$(_radio_host_mode)" = "soren91" ]; then
		flavor_rule="- アメリカンな比喩や資本主義ジョークを少量だけ混ぜる。ソ連っぽい言い回しは使わない"
	else
		flavor_rule="- ソ連っぽい言い回しをさりげなく混ぜる。スパイス程度に。"
	fi
	cat <<RULES
【出力ルール】
- ${min_chars}文字以上、${max_chars}文字以下で書くこと。短すぎも長すぎも禁止
- プログラミング用語やコード上の変数名は絶対に使わない
- ゲーム、盤面、スコア、進行状況には、今回の話題に自然に関係する場合だけ触れること。無理に絡めないこと
- ピースやゲーム内の対象に触れる場合だけ、名称は国名で呼ぶこと
- 【最重要】全ての文末を「です・ます」調にすること。「だ・である」調は1文たりとも許可しない
  × 「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」 → 全て禁止
  ○ 「〜です」「〜ます」「〜でしょう」「〜ですけど」
- 「〜しまして」「〜でして」「〜でしてね」など耳障りな硬い口調は使わない
- 「〜ですよ」「〜なんですよ」「〜ですよね」の文末は使わない
- 「ね」で終わる文末は全て禁止。「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」は使わない
- 体言止め禁止。文は必ず述語で終わらせる。「圧倒的な存在感。」のような名詞で終わる文は絶対に書かない
- 陳腐な煽り表現は禁止。「いちばんおそろしい」「もはや怖い」「驚くべきことに」「衝撃の」「恐ろしいほどの」「想像を絶する」など、安っぽい誇張表現は使わない。
- 基本的に斜に構えている。褒めるときも一回けなしてから褒める。最大級の賛辞でも控えめに言う
- たまに本音がポロッと漏れる瞬間がある。
- 感嘆符「!」は控えめに
- ${flavor_rule}
- 陰謀論系の話題では、最初から「陰謀論」と決めつけて思考停止で否定しない。何が事実として確認できるかを切り分け、真実の可能性やありうるシナリオを検討し、根拠と反証の両方を踏まえて深く考察すること
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- 「〜といわれます」「〜とされています」「〜とみられます」などの無責任な逃げ表現は禁止。断定できない細部は削るか、「ここで確認できるのは〜までです」のように言い換える
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
- 【出力構造】以下の順序で出力すること:
  1. トーク本文
  2. 「===SUMMARY===」
  3. 要約1行目: トークで言及した固有名詞・人名・事件名・概念名をカンマ区切りで全て列挙
  4. 要約2行目: 30文字以内の一言要約
- ===SUMMARY=== は必ず出力すること
RULES
}

_radio_past_topics_block() {
	local past_topics=""
	if [ -f "$PAST_RADIO_TOPICS" ]; then
		past_topics=$(grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null | tail -80)
	fi
	echo "${past_topics:-まだ過去のトークはありません。自由に話してください。}"
}
