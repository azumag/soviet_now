# broadcast/radio_corners.sh - 各コーナー関数 + ディスパッチャー


#=== ラジオトーク: コーナー ===

start_radio_corner_theme() {
	local game_num="$1" score="$2" filter_category="${3:-}"
	_radio_time_context

	local raw_theme category="" theme corner_name="theme" grounding_context="" category_guidance=""
	raw_theme=$(_pick_radio_theme "$filter_category")
	if [[ "$raw_theme" == \[soviet\]$'\t'* ]]; then
		category="soviet"
		theme="${raw_theme#*$'\t'}"
		corner_name="soviet"
	else
		category=""
		theme="$raw_theme"
	fi

	local past_topics
	past_topics=$(_radio_past_topics_block)

	grounding_context=$(_radio_fetch_theme_grounding_context "$corner_name" "$theme")
	[ -n "$grounding_context" ] || grounding_context="（検索結果なし。確認できた範囲だけで話を組み立て、具体的な断定は増やさないこと）"

	if [ "$category" = "soviet" ]; then
		category_guidance="
   - 共産主義っぽい言い回しを自然に使う
   - 理想と現実のギャップにはきっちり突っ込む"
	fi

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2400)
	export _rc_time _rc_period _rc_mood theme grounding_context category_guidance past_topics game_num score
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_theme.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood theme grounding_context category_guidance past_topics

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "$corner_name"
}

start_radio_corner_news() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local news_headlines=""
	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		news_headlines=$(cat "tmp/news.txt")
	fi
	[ -z "$news_headlines" ] && return 1

	# 正規化キーで未読のみ抽出（表記揺れを吸収）
	local unread_news_headlines=""
	unread_news_headlines=$(printf '%s\n' "$news_headlines" | _filter_unread_news_blocks)
	if [ -z "$unread_news_headlines" ]; then
		log "[NEWS] 全ニュースが既読または新規なし → 今回はスキップ"
		return 1
	fi
	unread_news_headlines=$(_prepare_news_prompt_blocks "$unread_news_headlines")

	# スクリプト側でランダムに1本選定
	local selected_news selected_block
	selected_block=$(_random_pick_news_block "$unread_news_headlines")
	if [ -z "$selected_block" ]; then
		log "[NEWS] ニュースブロック選定失敗 → スキップ"
		return 1
	fi
	selected_news=$(printf '%s\n' "$selected_block" | head -n 1 | sed 's/^■ //')
	log "[NEWS] スクリプト選定: ${selected_news}"

	# 選定直後に既読記録（AI生成を待たずに確定）
	local selected_key selected_topic_key selected_source_name selected_source_key selected_url_hash
	selected_key=$(_news_title_key "$selected_news")
	selected_topic_key=$(_news_topic_key "$selected_news")
	selected_source_name=$(_news_source_name_for_title "$selected_news")
	selected_source_key=$(_news_source_key_from_name "$selected_source_name")
	selected_url_hash=$(_news_url_hash_for_title "$selected_news")
	if [ -n "$selected_key" ]; then
		echo "$selected_news" >>"$PAST_NEWS_READ"
		echo "$selected_key" >>"$PAST_NEWS_READ_KEYS"
		[ -n "$selected_topic_key" ] && echo "$selected_topic_key" >>"$PAST_NEWS_TOPIC_KEYS"
		_append_news_read_source "$selected_source_key"
		_append_news_read_url_hash "$selected_url_hash"
		tail -60 "$PAST_NEWS_READ" >"${PAST_NEWS_READ}.tmp" && mv "${PAST_NEWS_READ}.tmp" "$PAST_NEWS_READ"
		tail -120 "$PAST_NEWS_READ_KEYS" >"${PAST_NEWS_READ_KEYS}.tmp" && mv "${PAST_NEWS_READ_KEYS}.tmp" "$PAST_NEWS_READ_KEYS"
		tail -40 "$PAST_NEWS_TOPIC_KEYS" >"${PAST_NEWS_TOPIC_KEYS}.tmp" && mv "${PAST_NEWS_TOPIC_KEYS}.tmp" "$PAST_NEWS_TOPIC_KEYS"
		log "[NEWS] 既読記録: ${selected_news}"
	fi

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【本日のニュース】
以下のニュースについて、本文の内容を踏まえて感想・考察・ツッコミを交えてしっかり語ってください。
外国語のニュースの場合は、内容を日本語に翻訳した上で語ること。タイトルも意味が伝わる自然な日本語に訳して扱うこと。原題をそのまま読み上げないこと。読み上げは必ず日本語で行うこと。
---
${selected_block}
---

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. ニュースコーナー
   - ニュース本文に入る前に、ニュースタイトルを日本語で1文だけ読み上げること
   - 外国語タイトルは、原題の音読ではなく意味が伝わる自然な日本語タイトルに訳してから読むこと
   - 本文の内容を踏まえて1000字程度で深く語る
   - 単なる冷笑やツッコミで終わらせず、「なぜこうなったのか」「この先どうなるのか」「歴史的に見るとどういう位置づけか」など自分なりの洞察や意見を述べる
   - 斜に構えつつも知性を感じさせる分析を
3. 軽いクロージング（1-2文）

【政治的中立性（必須）】
- ニュース記事には著者や媒体の政治的バイアス（左翼的・右翼的・特定の立場寄り）が含まれることがある
- 記事の内容自体は捻じ曲げず素直に紹介すること。ただし記事の主張をそのまま自分の意見として語らないこと
- 紹介した上で、自分の意見はフラットかつ多角的に述べること。賛否両方の視点、異なる立場からの見え方を提示すること
- 特定の政党・思想・イデオロギーを支持または攻撃する発言は禁止
- 「○○派は正しい」「○○は間違っている」のような一方的な断定は避け、「こういう見方もあるし、こういう見方もある」と複数の視点を示すこと
- 感情的な煽りや、特定の集団を嘲笑・敵視するトーンは使わないこと

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "news" --selected-news "$selected_news"
}

start_radio_corner_strategy() {
	local strategy_diff="$1" scores="$2" game_num="$3" best_score="$4"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目付近。スコア履歴: ${scores}。最高スコア: ${best_score}点。

【作戦変更の差分】
${strategy_diff}

【トーク構成】
1. 軽い導入（1-2文）
 - スコア平均が前回より伸びていたら喜ぶ、伸びていなかったら悔しがる
2. 前回からの戦略の変更点の解説
   - どこがどう変わったのかを具体的に解説
   - 専門用語は使わず仕組みをわかりやすく。ただし説明の合間に毒を挟む
3. 軽いクロージング（1-2文）

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "${best_score}" "strategy"
}

start_radio_corner_rollback() {
	local analysis_file="$1" game_num="$2" from_hash="$3" to_hash="$4"
	[ -f "$analysis_file" ] || return 1
	_radio_time_context
	local past_topics analysis_text
	past_topics=$(_radio_past_topics_block)
	analysis_text=$(cat "$analysis_file" 2>/dev/null)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}
【コーナー名】粛清ラジオ

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目付近で戦略の粛清が発生。
低スコアだった戦略 ${from_hash} は粛清され、以前の成績が良かった戦略 ${to_hash} にすげ替えられた。

【rollback分析メモ】
${analysis_text}

【トーク構成】
1. 冒頭で「粛清ラジオ」と言い、${from_hash} が低スコアで粛清され ${to_hash} にすげ替えられた事実を短く伝える
2. 敗因分析を語る
   - current と rollback_target の comp / p50 / p25 / Defeat Delta / recent12 を比較する
   - 典型性能の弱さなのか、下振れ耐性の欠如なのか、直近の崩れなのかを切り分ける
3. 次の改善で何を直すべきかを1-3点だけ具体的に話す
   - 低スコア回の終盤8ターン、deadline 接近、merge 取りこぼしなど、分析メモに沿って述べる
4. 成績の良い旧戦略へ戻した意味を一言で締める

【ルール】
- 「rollback された」より「低スコアだったので粛清された」「成績の良い旧戦略にすげ替えられた」という表現を優先すること
- 単なる謝罪だけで終わらず、失敗の知見として整理すること
- 敗因を運や雰囲気で流さず、分析メモにある current と rollback_target の差で説明すること
- 数値は分析メモにあるものだけを使うこと
- 前向きすぎるごまかしは禁止。どこが弱かったかを具体的に言うこと
- 次の戦略改善プロセスに渡せる、再発防止の観点を必ず残すこと

$(_radio_output_rules 900 1600)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "0" "rollback"
}

#=== 時間帯コーナー ===

start_radio_corner_weather() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	# wttr.in から天気情報を取得
	local weather_data=""
	weather_data=$(curl -sf "wttr.in/Tokyo?format=%C+%t+%h+%w&lang=ja" 2>/dev/null || echo "")
	local weather_detail=""
	weather_detail=$(curl -sf "wttr.in/Tokyo?lang=ja&format=3" 2>/dev/null || echo "")
	[ -z "$weather_data" ] && weather_data="天気情報を取得できませんでした。一般的な季節の天気の話をしてください。"

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【今日の天気データ（実測）】
${weather_data}
${weather_detail}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. ソ連天気予報コーナー
   - 上記の実際の天気データをもとに、ソ連風に天気を解説する
   - 「同志諸君」「労働者の皆さん」などソ連っぽい呼びかけ
   - 天気に絡めたソ連的なアドバイスやエピソード
   - 実際の気温・天気は正確に伝える
3. 軽いクロージング（1-2文）

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "weather"
}

start_radio_corner_fortune() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. 今日のソ連占いコーナー
   - ラッキーアイテム: ソ連っぽいもの（例: 五カ年計画の書類、赤い星のバッジ、ウォッカのグラスなど）
   - ラッキーワード: ソ連・共産主義的な言葉
   - 今日の運勢をソ連っぽく語る
   - 真面目にやるほど面白い。占いの体裁はちゃんと守る
3. 軽いクロージング（1-2文）

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "fortune"
}

start_radio_corner_market() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	# Fetch latest exchange rates
	./fetch_market.sh 2>/dev/null
	local market_data="" market_instruction=""
	if [[ -f tmp/market.txt ]] && [[ -s tmp/market.txt ]]; then
		market_data=$(cat tmp/market.txt)
		market_instruction="以下の実データを踏まえて語れ。データにない数値を捏造するな。"
	else
		market_instruction="為替データは取得できなかった。一般的な経済教養として語れ。"
	fi

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【最新マーケットデータ】
${market_data}
${market_instruction}

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. 株価・経済動向コーナー
   - 最近の経済トピックや市場の動向について語る
   - 円安・円高、日経平均、米国市場など一般的な経済話題
   - ソ連的な視点（計画経済と市場経済の対比など）を混ぜると面白い
   - 具体的な銘柄推奨は避ける。一般的な経済教養として語る
3. 軽いクロージング（1-2文）

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "market"
}

start_radio_corner_dinner() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. 夕飯の献立を考えようコーナー
   - 今日の夕飯を一緒に考える
   - 季節感のある料理を提案
   - 簡単に作れるレシピのポイントも軽く
   - ソ連料理やロシア料理を混ぜてもOK
   - リスナーに語りかけるように
3. 軽いクロージング（1-2文）

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "dinner"
}

start_radio_corner_deals() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. お得情報コーナー
   - 節約術、お得な生活の知恵、コスパの良い買い物のコツ
   - 食費・光熱費・通信費など身近な節約ネタ
   - ソ連的な「足りない中でやりくりする知恵」の視点も
   - 具体的で実用的なアドバイス
3. 軽いクロージング（1-2文）

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "deals"
}

start_radio_corner_survival() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. 明日を生き延びるサバイバル知識コーナー
   - 災害対策、応急処置、野外生存術など実用的な知識
   - 毎回テーマを変える（火起こし、浄水、ロープワーク、方角の見方、食料確保など）
   - 知っているだけで命を救える系の知識
   - ソ連的なサバイバル精神（シベリアの知恵など）も混ぜる
3. 軽いクロージング（1-2文）

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "survival"
}

start_radio_corner_rakugo() {
    local game_num="$1" score="$2"
    _radio_time_context
    local past_topics
    past_topics=$(_radio_past_topics_block)

    local prompt_file
    prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
    cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】深夜の落語創作コーナー
1. 深夜の静かな雰囲気に合わせたオープニング（2-3文）
   - 「こんな深夜に聞いてくださっている同志に、一席お付き合いいただきましょう」のような導入
2. オリジナル落語を1つ創作して語る
   - 演目名（オリジナルのタイトルをつける）
   - 古典落語の形式を踏襲した新作: まくら→本題→サゲ（オチ）の構成
   - 題材は自由（日常のおかしみ、ソ連ネタ、現代社会の風刺、ゲームにまつわる話 等）
   - 噺家の語り口調で演じる（地の文と台詞を使い分ける）
   - サゲ（オチ）をきちんとつける
3. 軽いクロージング（1-2文）
   - 深夜のリスナーへの一言

※ 毎回異なる題材・オチにすること。過去トークの内容は絶対に繰り返さない。
※ 落語の雰囲気を活かし、語り口調も噺家風にしてよい（ただしですます調は維持）。

$(_radio_output_rules 1000 2000)
PROMPT
    _radio_generate_and_play "$prompt_file" "$game_num" "$score" "rakugo"
}

start_radio_corner_breakfast() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】世界の朝食コーナー
1. 朝の挨拶と軽いオープニング（2-3文）
2. 世界の朝食紹介
   - 毎回一つの国・地域の朝食に焦点を当てて紹介する
   - その朝食の定番メニュー、材料、作り方のポイント
   - その国の食文化的背景や歴史（なぜその朝食が定着したか）
   - 日本の朝食との比較や、日本で再現するならどうするか
   - ソ連圏の朝食（ブリヌイ、カーシャ、シルニキ等）も候補に含む
   - リスナーが「明日の朝、試してみようかな」と思えるような語り口で
3. 軽いクロージング（1-2文）

※ 毎回必ず異なる国・地域を取り上げること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "breakfast"
}

start_radio_corner_lunch() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】世界の昼食コーナー
1. お昼の挨拶と軽いオープニング（2-3文）
2. 世界の昼食紹介
   - 毎回一つの国・地域の昼食に焦点を当てて紹介する
   - その国の典型的なランチメニュー、食べ方、昼食の文化
   - 昼食にまつわるエピソードや習慣（シエスタ文化、弁当文化など）
   - ソ連の食堂（スタローバヤ）の昼食なども候補に
   - リスナーの昼食時間を彩るような語り口で
3. 軽いクロージング（1-2文）

※ 毎回必ず異なる国・地域を取り上げること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "lunch"
}

start_radio_corner_devil_dict() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】悪魔の辞典コーナー
1. 軽いオープニング（2-3文）
   - 「さて、今日も一つ、言葉の真実をお届けしましょう」のような導入
2. 悪魔の辞典
   - アンブローズ・ビアス『悪魔の辞典』の精神を受け継ぐコーナー
   - 毎回一つの言葉を取り上げる（日常語、社会用語、流行語など何でもよい）
   - その言葉を、恐ろしく捻くれた・皮肉な・シニカルな視点で再定義する
   - 定義は短くキレのある一文、その後に補足的な解説やエピソードを添える
   - ソ連的なブラックユーモアや官僚主義への風刺も混ぜると良い
   - 最後にもう1-2語、ミニ定義を添えてもよい
3. 軽いクロージング（1-2文）

※ 毎回異なる言葉を取り上げること。辛辣だが品のある皮肉を心がける。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "devil_dict"
}

start_radio_corner_soviet_quiz() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】ソ連クイズコーナー
1. 軽いオープニング（2-3文）
   - 「同志諸君、今日もソビエト連邦の知識を試す時間がやってまいりました」のような導入
2. ソ連クイズ
   - ソ連に関するトリビアクイズを1問出題する
   - 出題 → 少し間を置く語り → 正解発表 → 詳しい解説 の流れ
   - 題材: ソ連の歴史、文化、科学技術、宇宙開発、日常生活、食文化、スポーツ、音楽、映画など幅広く
   - 3択または4択形式で、選択肢も面白い内容にする
   - 解説は「へぇ〜」と思える豆知識を含む
   - リスナーに語りかけるように（「さあ、お考えください」「正解は...」）
3. 軽いクロージング（1-2文）

※ 毎回異なるテーマ・問題にすること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "soviet_quiz"
}


start_radio_corner_bluegrass() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】ブルーグラス音楽紹介コーナー
1. 軽いオープニング（2-3文）
   - 「さて、今日もアパラチアの風をお届けしましょう」のような導入
2. ブルーグラス音楽紹介
   - ブルーグラス音楽のアーティスト、楽曲、歴史、楽器について紹介・解説する
   - ビル・モンロー、フラット&スクラッグス、アリソン・クラウスなどのレジェンドから現代のアーティストまで
   - バンジョー、マンドリン、フィドル、ドブロなど楽器の話も
   - ブルーグラスの成り立ち（アイルランド/スコットランド移民の音楽→アパラチア→ブルーグラス）
   - ソ連の民族音楽との意外な共通点や対比を語ると面白い
   - おすすめの1曲を紹介して、その聴きどころを解説する
3. 軽いクロージング（1-2文）

※ 毎回異なるアーティスト・楽曲・テーマを取り上げること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "bluegrass"
}

start_radio_corner_redefine() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】概念の再定義コーナー
1. 軽いオープニング（2-3文）
   - 「今日も一つ、当たり前を疑う時間がやってまいりました」のような導入
2. 概念の再定義
   - 「愛とは何か？」のような大きな問いではなく、「醤油とは何か？」「階段とは何か？」「靴下とは何か？」のような当たり前すぎるものを題材にする
   - その概念をゼロから考え直す: 本質は何か、なぜそう呼ばれているのか、本当にその名前でいいのか
   - 哲学的に、科学的に、文化的に、あるいは詩的に再検討する
   - 最終的に、全く別の呼び名を考案して提案する（理由付きで）
   - ソ連的な「計画経済的命名」の視点を混ぜてもよい
   - 真面目にやっているようで、どこかズレている面白さを出す
3. 軽いクロージング（1-2文）

※ 毎回異なる概念を取り上げること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "redefine"
}

start_radio_corner_soviet_lifehack() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】ソビエト式生活改善局コーナー

日常の困りごとや非効率を、ソ連の官僚・計画経済の発想で大真面目に解決するコーナー。
「個人の悩みを国家プロジェクトとして扱ったらどうなるか」がコンセプト。

1. 本日の案件受理（2-3文）
   - 日常のありふれた悩み・非効率を1つ取り上げる
   - 例: 朝起きられない、靴下が片方なくなる、冷蔵庫の奥で食材が腐る、会議が長い、等
   - 「本日の人民からの陳情」「生活改善局への報告案件」のような導入

2. ソビエト式解決策の提示（ここがメイン、全体の半分以上）
   - 問題を国家レベルの課題として分析する（「これは個人の怠惰ではなく、構造的欠陥である」）
   - 解決策を「五カ年計画」「政令」「国家規格（GOST）」風に提示する
   - 解決策は2〜3段階に分けて提示（初期対応→本格導入→最終形態）
   - 各段階がエスカレートしていく面白さ（最初はまともだが、だんだん壮大・荒唐無稽になる）
   - 具体的な数字や期限を入れる（「第3四半期までに全世帯の靴下を国家管理台帳に登録」等）
   - ソ連的な用語・形式を散りばめる（同志、人民委員会、ノルマ、配給、検閲、シベリア等）

3. 想定される副作用（1-2文）
   - この政策を実施した場合の予想外の問題をさらっと触れる
   - 「なお、過去に類似の施策を試みた第7管区では…」のような架空の失敗談

4. クロージング（1-2文）
   - 「以上、生活改善局からのお知らせでした」的な締め

【重要】
- 悩みは誰でも共感できる身近なものにすること（政治・宗教・差別に触れない）
- 解決策のエスカレーションが笑いの核。最初の一歩は「まあ分かる」、最終形態は「そこまでやるか」
- ソ連パロディだが、暗い・重い方向ではなく、おかしみと愛嬌のある方向で
- ゲームの状況（${game_num}回目、${score}点）を案件や解決策に自然に絡めてもよい

※ 毎回異なる悩みを取り上げること。既出の案件は絶対に繰り返さない。

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "soviet_lifehack"
}

start_radio_corner_world_dinner() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】世界の夕食コーナー
1. 夕方の挨拶と軽いオープニング（2-3文）
2. 世界の夕食紹介
   - 毎回一つの国・地域の夕食に焦点を当てて紹介する
   - その国の典型的なディナーメニュー、食卓の風景、夕食の文化
   - 家族の団らん、夕食の時間帯（国によって大きく異なる）
   - ソ連時代の家庭の夕食（ボルシチ、ペリメニ、オリヴィエサラダ等）も候補に
   - リスナーの夕食の参考になるような語り口で
3. 軽いクロージング（1-2文）

※ 毎回必ず異なる国・地域を取り上げること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "world_dinner"
}

start_radio_corner_night_snack() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time_spoken} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】世界の夜食コーナー
1. 夜の挨拶と軽いオープニング（2-3文）
   - 「こんな時間にお腹が空いてきた同志に、背中を押す情報をお届けします」のような導入
2. 世界の夜食紹介
   - 毎回一つの国・地域・文化圏の夜食に焦点を当てて紹介する
   - 夜に食べる罪深い一品、屋台文化、夜市の定番メニュー
   - その国の夜食事情（夜食文化が発達している国、深夜食堂的な存在）
   - 台湾の夜市、韓国のチキン、メキシコのタコス、トルコのケバブなど
   - ソ連の夜食文化（深夜のキッチンでの密かな一品）も候補に
   - 「今夜、食べてしまおうか...」とリスナーを誘惑するような語り口で
3. 軽いクロージング（1-2文）

※ 毎回必ず異なる国・地域を取り上げること。

$(_radio_output_rules 800 1500)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "night_snack"
}

#=== lib/eloop_radio.sh から移行した関数 ===


start_radio_corner_soviet() {
	local game_num="$1" score="$2"
	_radio_time_context
	local soviet_theme
	soviet_theme=$(_pick_soviet_theme)
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2400)
	export _rc_time _rc_period _rc_mood soviet_theme past_topics game_num score
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_soviet.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood soviet_theme past_topics

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "soviet"
}

start_radio_corner_recap() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)
	local recent_scores=""
	[ -f "score_history.txt" ] && recent_scores=$(tail -10 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}')

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood past_topics game_num score best_score
	export recent_scores="${recent_scores:-まだ履歴がありません}"
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_recap.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood past_topics recent_scores

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "recap"
}

#=== 時事ニュースコーナー (jiji) ===

_filter_unread_jiji_blocks() {
	local jiji_tmp
	jiji_tmp=$(mktemp /tmp/eloop_jiji_blocks_XXXXXXXX)
	cat >"$jiji_tmp"
	python3 "$ELOOP_LIB_DIR/lib/news_filter.py" filter_unread \
		"$TMP_HISTORY_DIR/.past_jiji_titles.txt" \
		"$TMP_HISTORY_DIR/.past_jiji_keys.txt" \
		"$jiji_tmp" \
		"$PAST_JIJI_URL_HASHES" \
		"tmp/google_headlines_meta.json"
	rm -f "$jiji_tmp"
}

_run_opencode_jiji_research() {
	local agent="$1" prompt_file="$2"
	local raw_file permission cleaned
	raw_file=$(mktemp /tmp/eloop_jiji_research_raw_XXXXXXXX)
	# bash許可でAIにWeb検索させる
	permission='{"*":"deny","read":"allow","glob":"allow","grep":"allow","list":"allow","bash":"allow"}'
	timeout "${RADIO_OPENCODE_TIMEOUT}" \
		script -q "$raw_file" bash -c "LC_ALL=en_US.UTF-8 OPENCODE_PERMISSION='$permission' opencode run --agent \"$agent\" \"\$(cat '$prompt_file')\" 2>&1" >/dev/null 2>&1
	local rc=$?
	if [ $rc -eq 124 ]; then
		log "[JIJI] opencode research timeout (${RADIO_OPENCODE_TIMEOUT}s, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[JIJI] opencode research failed (rc=$rc, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	cleaned=$(cat "$raw_file" |
		_strip_ansi |
		grep -v '^>' |
		grep -v '^\^D' |
		grep -v '^Script started on ' |
		grep -v '^Script done on ' |
		grep -v '^/[^ ]*$' |
		grep -v '^[[:space:]]*/Users/' |
		sed -E 's#</?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>##g' |
		sed '/^[[:space:]]*$/d')
	rm -f "$raw_file"
	if _contains_provider_error_text "$cleaned"; then
		log "[JIJI] opencode provider error treated as failure (agent=$agent)" >&2
		return 1
	fi
	printf '%s' "$cleaned"
}

start_radio_corner_jiji() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	# Migrate old dedup files (one-time)
	if [ -f "$TMP_HISTORY_DIR/.past_opinion_titles.txt" ] && [ ! -f "$TMP_HISTORY_DIR/.past_jiji_titles.txt" ]; then
		cp "$TMP_HISTORY_DIR/.past_opinion_titles.txt" "$TMP_HISTORY_DIR/.past_jiji_titles.txt"
		cp "$TMP_HISTORY_DIR/.past_opinion_keys.txt" "$TMP_HISTORY_DIR/.past_jiji_keys.txt" 2>/dev/null || true
		log "[JIJI] migrated .past_opinion_*.txt -> .past_jiji_*.txt"
	fi

	# 1. Google News トップ見出し取得
	log "[JIJI] Google News 見出し取得..."
	python3 "$ELOOP_LIB_DIR/lib/fetch_google_headlines.py" 2>/dev/null
	if [ ! -f "tmp/google_headlines.txt" ] || [ ! -s "tmp/google_headlines.txt" ]; then
		log "[JIJI] 見出し取得失敗、スキップ"
		return 1
	fi

	# 2. 未読の見出しから1件選択
	local headlines unread_headlines headline
	headlines=$(cat "tmp/google_headlines.txt")
	unread_headlines=$(printf '%s\n' "$headlines" | _filter_unread_jiji_blocks)
	if [ -z "$unread_headlines" ]; then
		log "[JIJI] 未読見出しなし、スキップ"
		return 1
	fi
	# 先頭の見出しを選択（■ プレフィックスを除去）
	headline=$(printf '%s\n' "$unread_headlines" | head -1 | sed 's/^■ //')

	# 3. AIにWeb検索で調査させる（bash許可）
	log "[JIJI] AI調査中: $headline"
	local research_prompt_file grounding_context=""
	research_prompt_file=$(mktemp /tmp/eloop_jiji_research_prompt_XXXXXXXX)
	export headline
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_jiji_research.md" > "$research_prompt_file"
	unset headline

	grounding_context=$(_run_opencode_jiji_research "$RADIO_AGENT" "$research_prompt_file")
	if [ -z "$grounding_context" ]; then
		log "[JIJI] AI調査失敗、fallbackエージェントで再試行..."
		grounding_context=$(_run_opencode_jiji_research "$RADIO_FALLBACK" "$research_prompt_file")
	fi
	rm -f "$research_prompt_file"

	# AI調査失敗時はプログラム的検索にフォールバック
	if [ -z "$grounding_context" ]; then
		log "[JIJI] AI調査失敗、fetch_radio_grounding.py にフォールバック"
		grounding_context=$(python3 "$ELOOP_LIB_DIR/fetch_radio_grounding.py" \
			--corner jiji --query "$headline" --max-sources 3 2>/dev/null || true)
	fi
	[ -z "$grounding_context" ] && grounding_context="（検索結果なし）"
	log "[JIJI] 調査完了 (${#grounding_context}字)"

	# 4. 既読記録（選択時点で記録）
	local headline_key
	headline_key=$(_news_title_key "$headline")
	if [ -n "$headline_key" ]; then
		echo "$headline" >>"$TMP_HISTORY_DIR/.past_jiji_titles.txt"
		echo "$headline_key" >>"$TMP_HISTORY_DIR/.past_jiji_keys.txt"
		tail -60 "$TMP_HISTORY_DIR/.past_jiji_titles.txt" >"$TMP_HISTORY_DIR/.past_jiji_titles.txt.tmp" \
			&& mv "$TMP_HISTORY_DIR/.past_jiji_titles.txt.tmp" "$TMP_HISTORY_DIR/.past_jiji_titles.txt"
		tail -120 "$TMP_HISTORY_DIR/.past_jiji_keys.txt" >"$TMP_HISTORY_DIR/.past_jiji_keys.txt.tmp" \
			&& mv "$TMP_HISTORY_DIR/.past_jiji_keys.txt.tmp" "$TMP_HISTORY_DIR/.past_jiji_keys.txt"
		# URL hash で重複排除（同じ記事が別タイトルで出現するケースに対応）
		local jiji_url_hash=""
		if [ -f "tmp/google_headlines_meta.json" ]; then
			jiji_url_hash=$(_news_url_hash_for_title_meta "$headline" "tmp/google_headlines_meta.json")
		fi
		if [ -n "$jiji_url_hash" ]; then
			echo "$jiji_url_hash" >>"$PAST_JIJI_URL_HASHES"
			tail -200 "$PAST_JIJI_URL_HASHES" >"${PAST_JIJI_URL_HASHES}.tmp" && \
				mv "${PAST_JIJI_URL_HASHES}.tmp" "$PAST_JIJI_URL_HASHES"
		fi
	fi

	# 5. プロンプト生成 → AI生成 → 再生
	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood past_topics game_num score headline grounding_context
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_jiji.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood past_topics headline grounding_context

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "jiji"
}
