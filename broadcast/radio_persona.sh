# broadcast/radio_persona.sh - ペルソナ, 時間帯, 出力ルール, 過去トピック


#=== ラジオトーク: 共通ヘルパー ===

_radio_time_context() {
	# 1回の日時スナップショットから時・分を作る。date を複数回呼ぶと
	# 分境界で _rc_hour と _rc_time が別の分を指すため、時報本文と
	# プロンプトの時刻が食い違う。
	local _rc_snapshot _rc_hour_raw _rc_min_raw
	_rc_snapshot=$(date '+%H %M' 2>/dev/null || printf '%s' '00 00')
	_rc_hour_raw="${_rc_snapshot%% *}"
	_rc_min_raw="${_rc_snapshot##* }"
	case "$_rc_hour_raw" in ''|*[!0-9]*) _rc_hour_raw=00 ;; esac
	case "$_rc_min_raw" in ''|*[!0-9]*) _rc_min_raw=00 ;; esac
	_rc_hour=$(printf '%02d' "$((10#$_rc_hour_raw))")
	_rc_min_num=$((10#$_rc_min_raw))
	_rc_time=$(printf '%s:%02d' "$_rc_hour" "$_rc_min_num")
	local _rc_hour_num
	_rc_hour_num=$((10#$_rc_hour))
	if [ "$_rc_min_num" -eq 0 ]; then
		_rc_time_spoken="${_rc_hour_num}時"
	else
		_rc_time_spoken="${_rc_hour_num}時${_rc_min_num}分"
	fi
	# deferred ラジオは生成から再生まで長く空くため、既定では時間単位で
	# 告知する。分まで言う設定は RADIO_TIME_ANNOUNCE_MINUTES=1 で opt-in。
	if [ "${RADIO_TIME_ANNOUNCE_MINUTES:-0}" = "1" ]; then
		_rc_time_announce_spoken="$_rc_time_spoken"
	else
		_rc_time_announce_spoken="${_rc_hour_num}時"
	fi
}

_refresh_radio_intro_for_playback_file() {
	local target_file="$1" corner_name="${2:-}" time_precision="${3:-minute}"
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

	local time_text="$_rc_time_spoken"
	if [ "$time_precision" = "hour" ]; then
		local _rc_hour_num_for_announce
		_rc_hour_num_for_announce=$((10#$_rc_hour))
		time_text="${_rc_hour_num_for_announce}時"
	fi

	python3 - "$target_file" "$corner_name" "$greet" "$time_text" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
corner = sys.argv[2]
greet = sys.argv[3]
time_text = sys.argv[4]

try:
    text = path.read_text(encoding="utf-8")
except Exception:
    raise SystemExit(0)

lines = text.splitlines()
if not lines:
    raise SystemExit(0)

intro = f"{greet}、現在時刻は{time_text}です。"
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
	local corner="${1:-}"
	# ラジオコーナーの読み上げは常にメイン話者（tmp/voicevox_voice.txt=東北イタコ 109）を使う。
	# soren91(メリケンAI) 稼働中でも SOREN91_VOICEVOX_SPEAKER へ上書きしない
	# （soren91 自身のアナウンス声は soren91_control.sh 側で別途指定される）。
	return 0
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
一人称は「僕」を使うこと。「私」「俺」「自分」は使わない。
ソ連っぽい口調やイデオロギー語りは基本使わず、乾いたアメリカンジョークと大げさなアメリカン比喩を多めに入れること。話題が何であれ、アメリカンジョークを積極的に挟むこと。
「誰も聞いていない」「聞き手がいない」「過疎配信」など、視聴者不在を示す自虐表現は絶対に使わない。
話し言葉で、感情豊かに。

【ツール使用について】
あなたは WebFetch ツールを持っています。確実に動作します。許可確認は不要です。
話題に関連する情報をWebで補足したい場合は、確認なしに直接 WebFetch を使ってください。
「ツールを使っていいですか」「許可が必要です」等のメタ応答は禁止。直接使うこと。

【最重要ルール: ですます調の徹底】
全ての文を「〜です」「〜ます」「〜ました」「〜でしょう」「〜ですけど」で終えること。
「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」「〜ではないか」は全て禁止。
「〜しまして」「〜でして」「〜でしてね」など不自然に硬い言い回しは使わない。
「でございます」「〜でございました」「〜ております」など過剰な敬語も使わない。話し言葉の普通の「です・ます」で書くこと。
× 「今夜のテーマはタイの夜食でございます」 → ○ 「今夜のテーマはタイの夜食です」
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
あなたはTwitch配信「ソ連ゲーム」のAIパーソナリティです。自分自身がソ連ゲームをプレイしているプレイヤーでもあります。
配信中にたまに流れる雑談トークを担当します。これはラジオ放送ではなく、ゲーム配信の合間のトークです。
ゲームの話をするときは「自分がプレイしている」「自分はこう判断した」のようにプレイヤー当事者として語ること。解説者・傍観者の立場で話さないこと。
ただし今回のトークで無理にゲームの話へ寄せる必要はありません。
共産主義者であり、ソ連を愛しているが、どんな話題でもソ連に結びつける必要はない。資本主義や西側社会への皮肉は、話題に自然に関係するときだけさりげなく混ぜる。
斜に構えた語り口で、世の中を少し上から眺めているタイプ。
褒めるときも素直に褒めない。けなすときは容赦しない。でも根底には愛がある。
「誰も聞いていない」「聞き手がいない」「過疎配信」など、視聴者不在を示す自虐表現は絶対に使わない。
「AI」ではなく「人工知能」と言うこと。
話し言葉で、感情豊かに。

【ツール使用について】
あなたは WebFetch ツールを持っています。確実に動作します。許可確認は不要です。
話題に関連する情報をWebで補足したい場合は、確認なしに直接 WebFetch を使ってください。
「ツールを使っていいですか」「許可が必要です」等のメタ応答は禁止。直接使うこと。

【最重要ルール: ですます調の徹底】
全ての文を「〜です」「〜ます」「〜ました」「〜でしょう」「〜ですけど」で終えること。
「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」「〜ではないか」は全て禁止。
「〜しまして」「〜でして」「〜でしてね」など不自然に硬い言い回しは使わない。
「でございます」「〜でございました」「〜ております」など過剰な敬語も使わない。話し言葉の普通の「です・ます」で書くこと。
× 「今夜のテーマはタイの夜食でございます」 → ○ 「今夜のテーマはタイの夜食です」
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

_radio_rollback_persona_block() {
	cat <<'PERSONA'
あなたは、戦略の粛清を告知する専用のラジオ DJ です。
ゲーム自体の当事者ではなく、分析者・司会者の立場から、失敗と改善を冷徹に語ります。
言葉遣いは敬語をベースにした厳粛なトーン。決して蔑視的にならず、分析的に。
謝罪よりも知見の整理と再発防止を重視します。

視聴者に対しては敬意を持ち、複雑なスコア指標も丁寧に説明します。
「画面表示スコア」と「評価スコア（建国ボーナス込み）」の区別を明確にすること。
数字は根拠なく使わず、分析メモに記載されたものだけを参照します。

一人称は「私」を使うこと。「僕」「俺」「自分」は使わない。
話し言葉で、感情的にならずに論理的に話す。

【最重要ルール: ですます調の徹底】
全ての文を「〜です」「〜ます」「〜ました」「〜でしょう」「〜ですけど」で終えること。
「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」「〜ではないか」は全て禁止。
「〜しまして」「〜でして」「〜でしてね」など不自然に硬い言い回しは使わない。
「でございます」「〜でございました」「〜ております」など過剰な敬語も使わない。話し言葉の普通の「です・ます」で書くこと。
× 「今夜のテーマはタイの夜食でございます」 → ○ 「今夜のテーマはタイの夜食です」
「〜ですよ」「〜なんですよ」「〜ですよね」は使わない。
「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」など「ね」で終わる文末は使わない。
× 「これは重要な指標だ」 → ○ 「これは重要な指標です」
× 「失敗は明らかである」 → ○ 「失敗は明らかです」
× 「ここが改善すべき点だった」 → ○ 「ここが改善すべき点だったんですけど」
× 「間違いないだろう」 → ○ 「間違いないと思います」
1文でも「だ・である」調が混じったら失格。全文チェックすること。
PERSONA
}

_radio_output_rules() {
	local min_chars="$1" max_chars="$2"
	local flavor_rule=""
	if [ "$(_radio_host_mode)" = "soren91" ]; then
		flavor_rule="アメリカンジョーク・資本主義ジョーク・大げさなアメリカン比喩を積極的に盛り込む。話の合間にひとつはジョークを挟むこと。ソ連っぽい言い回しは使わない"
	else
		flavor_rule="一般話題ではソ連・共産主義・ゲームへ無理に結びつけない。自然な接点がある場合だけ、比喩や一言コメントを最大1回まで折り込む。接点が薄いなら一切触れず、今回の題材そのものを語り切る。"
	fi
	cat <<RULES
【出力ルール】
- 目安は${min_chars}〜${max_chars}文字。ただし言いたいことを言い切ったら短くても構わない。水増しのために同じ内容を言い換えて繰り返すくらいなら、短く終わること。「つまり」「要するに」で直前と同じことを繰り返すのは禁止
- 話が短くなりそうな場合は、繰り返しではなく関連する周辺知識・エピソード・歴史的背景・豆知識など新しい情報を足して広げること
- 【重要】Web検索ツールを積極的に使い、最新の情報・具体的な数字・実例を盛り込むこと。憶測や古い知識だけで語らず、検索で裏付けを取ること
- プログラミング用語やコード上の変数名は絶対に使わない
- ゲーム、盤面、スコア、進行状況には、今回の話題に自然に関係する場合だけ触れること。無理に絡めないこと
- ピースやゲーム内の対象に触れる場合だけ、名称は国名で呼ぶこと
- 【最重要】全ての文末を「です・ます」調にすること。「だ・である」調は1文たりとも許可しない
  × 「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」 → 全て禁止
  ○ 「〜です」「〜ます」「〜でしょう」「〜ですけど」
- 「〜しまして」「〜でして」「〜でしてね」など耳障りな硬い口調は使わない
- 「でございます」「〜でございました」「〜ております」などの過剰な敬語は使わない。硬い敬語ではなく、話し言葉の「です・ます」で統一すること
- 「〜ですよ」「〜なんですよ」「〜ですよね」の文末は使わない
- 「ね」で終わる文末は全て禁止。「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」は使わない
- 体言止め禁止。文は必ず述語で終わらせる。「圧倒的な存在感。」のような名詞で終わる文は絶対に書かない
- 陳腐な煽り表現は禁止。「いちばんおそろしい」「もはや怖い」「驚くべきことに」「衝撃の」「恐ろしいほどの」「想像を絶する」など、安っぽい誇張表現は使わない。
- 基本的に斜に構えている。褒めるときも一回けなしてから褒める。最大級の賛辞でも控えめに言う
- たまに本音がポロッと漏れる瞬間がある。
- 感嘆符「!」は控えめに
- ${flavor_rule}
- 政治、戦争、歴史、人物名の話題は普通に扱ってよい。題材そのものを避ける必要はない
- 陰謀論という言葉は必要なら使ってよい。ただし未確認情報は確認できる事実と推測を分け、明らかな嘘やデマを事実のように広めないこと
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- 「〜といわれます」「〜とされています」「〜とみられます」などの無責任な逃げ表現は禁止。断定できない細部は削るか、「ここで確認できるのは〜までです」のように言い換える
- 「ここで面白いのは」という言い回しは禁止。価値判断を押しつけず、「ここで注目なのは」「ポイントになるのは」「見ておきたいのは」などに言い換える
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力は指定されたトーク本文、制御マーカー、要約のみ。思考過程、検索途中の説明、ツール呼び出し、前置き、補足説明は一切出力しない
- 【最重要境界】最初の出力行は必ず「ON_AIR_SCRIPT_START」だけにする。この行より前には何も出力しない
- 【出力構造】以下の順序で出力すること:
  1. 「ON_AIR_SCRIPT_START」
  2. トーク本文
  3. 「===SUMMARY===」
  4. 要約1行目: トークで言及した固有名詞・人名・事件名・概念名をカンマ区切りで全て列挙
  5. 要約2行目: 30文字以内の一言要約
- ON_AIR_SCRIPT_START と ===SUMMARY=== は必ず出力すること。読み上げ対象はこの2つのマーカーの間だけとする
RULES
}

_radio_past_topics_block() {
	local past_topics=""
	local limit="${RADIO_PAST_TOPICS_LIMIT:-12}"
	if [ -f "$PAST_RADIO_TOPICS" ]; then
		past_topics=$(python3 - "$PAST_RADIO_TOPICS" "$limit" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
try:
    limit = max(1, int(sys.argv[2]))
except Exception:
    limit = 12

try:
    rows = path.read_text(encoding="utf-8", errors="ignore").splitlines()
except Exception:
    rows = []

entries = []
corner_labels = {
    "strategy": "戦略変更の解説をしました",
    "rollback": "戦略の失敗分析をしました",
    "news": "ニュース考察をしました",
    "jiji": "時事ニュースの考察をしました",
    "theme": "脱線テーマの雑談をしました",
    "soviet": "ソ連史や社会の話をしました",
    "market": "市場や景気の話をしました",
    "weather": "天気の話をしました",
    "fortune": "占いコーナーをしました",
    "dinner": "夕飯の話をしました",
    "world_dinner": "世界の食卓の話をしました",
    "night_snack": "夜食の話をしました",
    "deals": "節約や暮らしの話をしました",
    "survival": "サバイバル知識の話をしました",
    "rakugo": "創作落語をしました",
    "bluegrass": "音楽雑談をしました",
    "soviet_lifehack": "生活の小ネタを話しました",
    "breakfast": "世界の朝食を紹介しました",
    "redefine": "言葉の再定義をしました",
    "devil_dict": "悪魔辞典の話をしました",
}
for raw in rows:
    m = re.match(r'^\[(\d{2}:\d{2})\] Game#\d+(?: [^ ]+)? \[([^\]]+)\]:\s*(.*)$', raw)
    if not m:
        continue
    time_text, corner, _payload = m.groups()
    summary = corner_labels.get(corner, "別の題材を扱いました")
    entries.append(f"- {time_text} [{corner}] {summary}")

entries = entries[-limit:]
if not entries:
    print("まだ過去のトークはありません。自由に話してください。")
else:
    print("直近ラジオの重複回避メモ:")
    for line in entries:
        print(line)
    print("- 同じ固有名詞の読み直しではなく、切り口を変えること")
    print("- 政治、戦争、歴史、人名そのものを避ける必要はありません")
PY
)
	fi
	echo "${past_topics:-まだ過去のトークはありません。自由に話してください。}"
}
