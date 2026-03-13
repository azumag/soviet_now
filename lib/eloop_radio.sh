#!/bin/bash
# lib/eloop_radio.sh - ラジオトーク・オーディオ管理

#=== ANSIエスケープ除去 ===

_strip_ansi() {
	perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

_contains_provider_error_text() {
	printf '%s' "$1" | grep -Eiq 'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|request_id|invalid error token|invalid token'
}

#=== opencode run を疑似TTY付きで実行 ===

_run_opencode_radio() {
	local agent="$1" prompt_file="$2"
	local raw_file cleaned
	raw_file=$(mktemp /tmp/eloop_radio_raw_XXXXXXXX)
	timeout "${RADIO_OPENCODE_TIMEOUT}" \
		script -q "$raw_file" bash -c "LC_ALL=en_US.UTF-8 OPENCODE_PERMISSION='$RADIO_OPENCODE_PERMISSION' opencode run --agent \"$agent\" \"\$(cat '$prompt_file')\" 2>&1" >/dev/null 2>&1
	local rc=$?
	if [ $rc -eq 124 ]; then
		# command substitution に混ざらないよう stderr に出す
		log "[RADIO] opencode timeout (${RADIO_OPENCODE_TIMEOUT}s, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		# 非タイムアウト失敗も本文扱いせず fallback へ渡す
		log "[RADIO] opencode failed (rc=$rc, agent=$agent)" >&2
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
		log "[RADIO] opencode provider error treated as failure (agent=$agent)" >&2
		return 1
	fi
	printf '%s' "$cleaned"
}

_run_claude_radio() {
	local prompt_file="$1"
	local prompt output
	prompt=$(cat "$prompt_file" 2>/dev/null)
	if [ -z "$prompt" ]; then
		return 1
	fi
	# command substitution に混ざらないよう stderr に出す
	log "[RADIO] claude fallback (model=$RADIO_CLAUDE_MODEL)" >&2
	output=$(claude -p "$prompt" --model "$RADIO_CLAUDE_MODEL" 2>/dev/null)
	if _contains_provider_error_text "$output"; then
		log "[RADIO] claude provider error treated as failure (model=$RADIO_CLAUDE_MODEL)" >&2
		return 1
	fi
	printf '%s' "$output"
}

_clean_comment_talk() {
	printf '%s\n' "$1" |
		grep -Eiv '^[[:space:]]*(assistant|analysis|final|tool_call|tool_result)[[:space:]]*$' |
		grep -Eiv '^[[:space:]]*(agent|model|provider)[[:space:]]*[:=].*$' |
		grep -Eiv '^[[:space:]]*(zai|glmflash|sonnet|claude|opencode)[[:space:]]*$' |
		sed '/^[[:space:]]*$/d'
}

_is_valid_comment_talk() {
	local talk="$1"
	local compact
	compact=$(printf '%s' "$talk" | tr -d '[:space:]')
	[ ${#compact} -ge 24 ] || return 1
	printf '%s' "$talk" | grep -Eq '[。！？]' || return 1
	return 0
}

#=== ラジオトーク: 共通ヘルパー ===

_radio_time_context() {
	_rc_hour=$(date '+%H')
	_rc_time=$(date '+%H:%M')
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

_radio_persona_block() {
	cat "$ELOOP_LIB_DIR/prompts/radio_persona.md"
}

_radio_output_rules() {
	local min_chars="$1" max_chars="$2"
	export min_chars max_chars
	envsubst '${min_chars} ${max_chars}' < "$ELOOP_LIB_DIR/prompts/radio_rules.md"
	unset min_chars max_chars
}

_radio_parse_output_to_files() {
	local body_file="$1" summary_file="$2" selected_news_file="$3"
	python3 "$ELOOP_LIB_DIR/lib/radio_parser.py" "$body_file" "$summary_file" "$selected_news_file"
}

_radio_past_topics_block() {
	local past_topics=""
	if [ -f "$PAST_RADIO_TOPICS" ]; then
		past_topics=$(grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null | tail -"${PAST_RADIO_TOPICS_KEEP:-100}")
	fi
	echo "${past_topics:-まだ過去のトークはありません。自由に話してください。}"
}

_radio_dedup_text() {
	python3 "$ELOOP_LIB_DIR/lib/radio_text_utils.py" dedup
}

_sanitize_onair_text() {
	python3 "$ELOOP_LIB_DIR/lib/radio_text_utils.py" sanitize
}

_normalize_radio_tone() {
	python3 "$ELOOP_LIB_DIR/lib/radio_text_utils.py" normalize_tone
}

_ensure_corner_announce() {
	local text="$1" corner_name="$2"
	local announce=""
	case "$corner_name" in
		soviet)   announce="ソ連共産主義ネタコーナーです。" ;;
		news)     announce="本日のニュースです。" ;;
		weather)  announce="ソ連天気予報コーナーです。" ;;
		fortune)  announce="今日のソ連占いコーナーです。" ;;
		market)   announce="本日の株価・経済動向コーナーです。" ;;
		dinner)   announce="今日の夕飯の献立を考えようコーナーです。" ;;
		deals)    announce="お得情報コーナーです。" ;;
		survival) announce="明日を生き延びるサバイバル知識コーナーです。" ;;
		jiji)     announce="時事ニュースコーナーです。" ;;
		rollback) announce="粛清ラジオです。" ;;
		theme)    announce="" ;;
		strategy) announce="" ;;
	esac
	[ -z "$announce" ] && { printf '%s' "$text"; return 0; }
	# 既に含まれていたら二重挿入しない
	if printf '%s\n' "$text" | head -n 5 | grep -qF "$announce"; then
		printf '%s' "$text"
		return 0
	fi
	# 挨拶行（1行目）の後に挿入
	local first_line rest
	first_line=$(printf '%s\n' "$text" | head -n 1)
	rest=$(printf '%s\n' "$text" | tail -n +2)
	printf '%s\n%s\n%s' "$first_line" "$announce" "$rest"
}

_ensure_radio_intro() {
	local text="$1" corner_name="${2:-}"
	[ -z "$text" ] && return 1

	_radio_time_context
	local greet
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 11 ]; then
		greet="おはようございます"
	elif [ "$_rc_hour" -ge 17 ] || [ "$_rc_hour" -lt 2 ]; then
		greet="こんばんは"
	else
		greet="こんにちは"
	fi

	local head
	head=$(printf '%s\n' "$text" | head -n 3)
	if printf '%s\n' "$head" | grep -Eq '現在時刻|[0-2][0-9]:[0-5][0-9]|おはよう|こんにちは|こんばんは'; then
		printf '%s' "$text"
		return 0
	fi

	local intro_line
	intro_line="${greet}、${_rc_period}の放送です。現在時刻は${_rc_time}です。"

	printf '%s\n%s' "$intro_line" "$text"
}

_news_title_key() {
	local title="$1"
	python3 "$ELOOP_LIB_DIR/lib/news_filter.py" title_key "$title"
}

_filter_unread_news_blocks() {
	local news_tmp
	news_tmp=$(mktemp /tmp/eloop_news_blocks_XXXXXXXX)
	cat >"$news_tmp"
	python3 "$ELOOP_LIB_DIR/lib/news_filter.py" filter_unread "$PAST_NEWS_READ" "$PAST_NEWS_READ_KEYS" "$news_tmp"
	rm -f "$news_tmp"
}

_resolve_selected_news_title() {
	local selected_title="$1" news_file="$2"
	python3 "$ELOOP_LIB_DIR/lib/news_filter.py" resolve_title "$selected_title" "$news_file"
}

# 自分のコーナーの状態ファイルだけ安全に削除 (並列実行の競合防止)
_radio_clear_state() {
	local my_corner="$1"
	local current
	current=$(cat $RADIO_STATE_FILE 2>/dev/null) || return 0
	case "$current" in *":${my_corner}:"*) rm -f $RADIO_STATE_FILE ;; esac
}

_radio_generate_and_play() {
	local prompt_file="$1" game_num="$2" score="$3" corner_name="$4"
	shift 4
	local no_preempt=true
	local selected_news=""
	local selected_news_preselected=false
	while [ $# -gt 0 ]; do
		case "$1" in
		--no-preempt) no_preempt=true ;;
		--selected-news) shift; selected_news="$1"; selected_news_preselected=true ;;
		esac
		shift
	done

	# 同一 game_num + corner の二重生成/二重再生を防止
	local done_marker="$TMP_MARKERS_DIR/.radio_done_${game_num}_${corner_name}"
	if [ -f "$done_marker" ]; then
		log "[RADIO:${corner_name}] duplicate skip: already done for game=${game_num}"
		return 0
	fi
	local inflight_dir="$TMP_MARKERS_DIR/.radio_inflight_${game_num}_${corner_name}"
	if ! mkdir "$inflight_dir" 2>/dev/null; then
		log "[RADIO:${corner_name}] duplicate skip: in-flight for game=${game_num}"
		return 0
	fi

	echo "generating:${corner_name}:$(date +%s)" > $RADIO_STATE_FILE
	log "[RADIO:${corner_name}] トーク生成中..."
	local talk
	talk=$(_run_opencode_radio "$RADIO_AGENT" "$prompt_file")
	if [ -z "$talk" ]; then
		talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$prompt_file")
	fi
	if [ -z "$talk" ]; then
		talk=$(_run_claude_radio "$prompt_file")
	fi
	rm -f "$prompt_file"

	if [ -z "$talk" ]; then
		log "[RADIO:${corner_name}] トーク生成失敗"
		_radio_clear_state "$corner_name"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	local talk_body talk_summary parsed_selected_news parse_dir
	parse_dir=$(mktemp -d /tmp/eloop_radio_parse_XXXXXXXX)
	printf '%s' "$talk" | _radio_parse_output_to_files "$parse_dir/body.txt" "$parse_dir/summary.txt" "$parse_dir/selected_news.txt"
	talk_body=$(cat "$parse_dir/body.txt" 2>/dev/null)
	talk_summary=$(cat "$parse_dir/summary.txt" 2>/dev/null)
	parsed_selected_news=$(cat "$parse_dir/selected_news.txt" 2>/dev/null)
	rm -rf "$parse_dir"
	[ -z "$talk_summary" ] && talk_summary="(要約なし)"

	if [ -z "$selected_news" ]; then
		selected_news="$parsed_selected_news"
	elif [ -z "$parsed_selected_news" ] && [ "$corner_name" = "news" ]; then
		log "[RADIO:news] SELECTED_NEWS抽出失敗 -> スケジュール済みタイトルを採用: ${selected_news}"
	fi

	# ニュースコーナーの場合、選んだニュースを既読リストに記録
	if [ "$corner_name" = "news" ]; then
		if [ -n "$selected_news" ]; then
			local selected_key
			selected_news=$(_resolve_selected_news_title "$selected_news" "tmp/news.txt")
			if [ "$selected_news_preselected" = true ]; then
				log "[RADIO:news] スケジュール済みニュースを再生: ${selected_news}"
			elif [ -n "$selected_news" ]; then
			selected_key=$(_news_title_key "$selected_news")
			if [ -z "$selected_key" ]; then
				log "[RADIO:news] 既読記録スキップ: タイトル解決失敗"
			elif grep -qxF "$selected_news" "$PAST_NEWS_READ" 2>/dev/null || grep -qxF "$selected_key" "$PAST_NEWS_READ_KEYS" 2>/dev/null; then
				log "[RADIO:news] 重複ニュース検出 → スキップ: ${selected_news}"
				_radio_clear_state "$corner_name"
				rmdir "$inflight_dir" 2>/dev/null || true
				return 1
			else
				echo "$selected_news" >>"$PAST_NEWS_READ"
				echo "$selected_key" >>"$PAST_NEWS_READ_KEYS"
				tail -"${PAST_NEWS_READ_KEEP:-60}" "$PAST_NEWS_READ" >"${PAST_NEWS_READ}.tmp" && mv "${PAST_NEWS_READ}.tmp" "$PAST_NEWS_READ"
				tail -"${PAST_NEWS_READ_KEYS_KEEP:-120}" "$PAST_NEWS_READ_KEYS" >"${PAST_NEWS_READ_KEYS}.tmp" && mv "${PAST_NEWS_READ_KEYS}.tmp" "$PAST_NEWS_READ_KEYS"
				log "[RADIO:news] 既読記録: ${selected_news}"
			fi
			fi
		fi
	fi

	{
		[ -f "$PAST_RADIO_TOPICS" ] && grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null || true
		echo "[$(date '+%H:%M')] Game#${game_num} ${score}pts [${corner_name}]: ${talk_summary}"
	} | tail -"${PAST_RADIO_TOPICS_KEEP:-100}" >"${PAST_RADIO_TOPICS}.tmp" && mv "${PAST_RADIO_TOPICS}.tmp" "$PAST_RADIO_TOPICS"

	local talk_body_parsed talk_body_sanitized talk_body_dedup
	talk_body_parsed="$talk_body"
	talk_body_sanitized=$(printf '%s' "$talk_body_parsed" | _sanitize_onair_text)
	talk_body_dedup=$(printf '%s' "$talk_body_sanitized" | _radio_dedup_text)

	# dedup が過剰に効いて短文化した場合は、まず非dedup本文に戻す
	if [ ${#talk_body_dedup} -lt "${RADIO_MIN_TALK_LENGTH:-100}" ] && [ ${#talk_body_sanitized} -ge "${RADIO_MIN_TALK_LENGTH:-100}" ]; then
		log "[RADIO:${corner_name}] dedup短縮が過剰 (${#talk_body_sanitized} -> ${#talk_body_dedup}字) → 非dedup本文を採用"
		talk_body="$talk_body_sanitized"
	else
		talk_body="$talk_body_dedup"
	fi

	# パーサ結果が短い場合は、生の出力から本文を再抽出して救済
	if [ ${#talk_body} -lt "${RADIO_MIN_TALK_LENGTH:-100}" ]; then
		local fallback_body
		fallback_body=$(printf '%s\n' "$talk" | sed '/^===SUMMARY===/,$d' | sed '/^===SELECTED_NEWS===/,$d')
		fallback_body=$(printf '%s' "$fallback_body" | _sanitize_onair_text)
		if [ ${#fallback_body} -ge "${RADIO_MIN_TALK_LENGTH:-100}" ]; then
			log "[RADIO:${corner_name}] 本文再抽出フォールバック採用 (${#fallback_body}字)"
			talk_body="$fallback_body"
		fi
	fi

	# 挨拶・時刻言及が抜けた出力を補完
	local talk_with_intro
	talk_with_intro=$(_ensure_radio_intro "$talk_body" "$corner_name")
	[ -n "$talk_with_intro" ] && talk_body="$talk_with_intro"
	talk_body=$(_ensure_corner_announce "$talk_body" "$corner_name")
	talk_body=$(printf '%s' "$talk_body" | _normalize_radio_tone)

	if [ ${#talk_body} -lt "${RADIO_MIN_TALK_LENGTH:-100}" ]; then
		local debug_dump
		debug_dump="$TMP_DEBUG_DIR/radio_short_${corner_name}_$(date +%s).txt"
		{
			echo "===RAW==="
			printf '%s\n' "$talk"
			echo
			echo "===PARSED==="
			printf '%s\n' "$talk_body_parsed"
			echo
			echo "===SANITIZED==="
			printf '%s\n' "$talk_body_sanitized"
			echo
			echo "===DEDUP==="
			printf '%s\n' "$talk_body_dedup"
		} >"$debug_dump"
		log "[RADIO:${corner_name}] WARNING: 本文が短すぎる(${#talk_body}字) → スキップ (dump: $debug_dump)"
		_radio_clear_state "$corner_name"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	# say待ちは say_enqueue.sh 内で行われるため、ここでは不要

	local talk_file
	talk_file=$(mktemp /tmp/eloop_radio_talk_XXXXXXXX)
	echo "$talk_body" >"$talk_file"
	echo "playing:${corner_name}:$(date +%s)" > $RADIO_STATE_FILE
	log "[RADIO:${corner_name}] ${#talk_body}字"
	if [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
		local news_cc_text=""
		if declare -F _build_cc_attribution_text >/dev/null 2>&1; then
			news_cc_text=$(_build_cc_attribution_text "$selected_news")
		fi
		if [ -n "$news_cc_text" ] && declare -F _post_cc_text_to_chat >/dev/null 2>&1; then
			_post_cc_text_to_chat "$news_cc_text"
		elif declare -F _append_cc_post_log >/dev/null 2>&1; then
			_append_cc_post_log "SKIP" "no_cc_text title=${selected_news}" "[NEWS] ${selected_news}"
		fi
	elif [ "$corner_name" = "news" ] && declare -F _append_cc_post_log >/dev/null 2>&1; then
		_append_cc_post_log "SKIP" "no_selected_news" "[NEWS] (selected_news unavailable)"
	fi
	if [ "$no_preempt" = true ]; then
		SAY_CONTEXT_LABEL="radio:${corner_name}" ./say_enqueue.sh --no-preempt "$talk_file" "$RADIO_SAY_RATE" 0
	else
		SAY_CONTEXT_LABEL="radio:${corner_name}" ./say_enqueue.sh "$talk_file" "$RADIO_SAY_RATE" 0
	fi
	rm -f "$talk_file"
	touch "$done_marker"
	# 古い重複防止マーカーを掃除（最新200件だけ保持）
	local old_markers
	old_markers=$(ls -1t $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | tail -n +201 || true)
	if [ -n "$old_markers" ]; then
		echo "$old_markers" | xargs rm -f 2>/dev/null || true
	fi
	_radio_clear_state "$corner_name"
	rmdir "$inflight_dir" 2>/dev/null || true
	log "[RADIO:${corner_name}] トーク終了"
}

#=== ラジオトーク: テーマ選択 ===

_pick_radio_theme() {
	local themes=()
	local theme_keys=()
	if [ -f "$ELOOP_LIB_DIR/data/radio_themes.txt" ]; then
		while IFS= read -r _line || [ -n "$_line" ]; do
			[ -n "$_line" ] || continue
			case "$_line" in
			\#*) continue ;;
			esac
			local t_key="${_line%%。*}"
			[ "$t_key" = "$_line" ] && t_key="${_line%%を深掘り*}"
			[ -n "$t_key" ] || t_key="$_line"
			local seen=false existing_key
			for existing_key in "${theme_keys[@]}"; do
				if [ "$existing_key" = "$t_key" ]; then
					seen=true
					break
				fi
			done
			if [ "$seen" = false ]; then
				themes+=("$_line")
				theme_keys+=("$t_key")
			fi
		done < "$ELOOP_LIB_DIR/data/radio_themes.txt"
	fi
	if [ ${#themes[@]} -eq 0 ]; then
		themes=("世界の料理と文化の話。各国の食卓と暮らしの違いを深掘りして")
	fi
	local past_themes_file="$PAST_RADIO_TOPICS"
	local available_themes=()
	local past_theme_list=""
	[ -f "$past_themes_file" ] && past_theme_list=$(cat "$past_themes_file")
	for t in "${themes[@]}"; do
		[ -z "$t" ] && continue
		local t_key="${t%%。*}"
		[ "$t_key" = "$t" ] && t_key="${t%%を深掘り*}"
		if ! echo "$past_theme_list" | grep -qF "$t_key"; then
			available_themes+=("$t")
		fi
	done
	if [ ${#available_themes[@]} -eq 0 ]; then
		available_themes=("${themes[@]}")
		>"$past_themes_file"
	fi
	local theme="${available_themes[$((RANDOM % ${#available_themes[@]}))]}"
	local theme_key="${theme%%。*}"
	[ "$theme_key" = "$theme" ] && theme_key="${theme%%を深掘り*}"
	echo "$theme_key" >>"$past_themes_file"
	tail -"${PAST_RADIO_TOPICS_KEEP:-100}" "$past_themes_file" >"${past_themes_file}.tmp" && mv "${past_themes_file}.tmp" "$past_themes_file"
	echo "$theme"
}

_pick_soviet_theme() {
	local soviet_themes=()
	while IFS= read -r _line; do
		[ -n "$_line" ] && soviet_themes+=("$_line")
	done < "$ELOOP_LIB_DIR/data/radio_soviet_themes.txt"
	local past_soviet_file="$TMP_HISTORY_DIR/.past_soviet_themes.txt"
	local available_soviet=()
	local past_soviet_list=""
	[ -f "$past_soviet_file" ] && past_soviet_list=$(cat "$past_soviet_file")
	for st in "${soviet_themes[@]}"; do
		[ -z "$st" ] && continue
		local st_key="${st%%。*}"
		[ "$st_key" = "$st" ] && st_key="${st%%を深掘り*}"
		if ! echo "$past_soviet_list" | grep -qF "$st_key"; then
			available_soviet+=("$st")
		fi
	done
	if [ ${#available_soviet[@]} -eq 0 ]; then
		available_soviet=("${soviet_themes[@]}")
		>"$past_soviet_file"
	fi
	local soviet_theme="${available_soviet[$((RANDOM % ${#available_soviet[@]}))]}"
	local soviet_key="${soviet_theme%%。*}"
	[ "$soviet_key" = "$soviet_theme" ] && soviet_key="${soviet_theme%%を深掘り*}"
	echo "$soviet_key" >>"$past_soviet_file"
	tail -"${PAST_SOVIET_TOPICS_KEEP:-100}" "$past_soviet_file" >"${past_soviet_file}.tmp" && mv "${past_soviet_file}.tmp" "$past_soviet_file"
	echo "$soviet_theme"
}

#=== ラジオトーク: 5つのコーナー ===

start_radio_corner_theme() {
	local game_num="$1" score="$2"
	_radio_time_context
	local theme
	theme=$(_pick_radio_theme)
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood theme past_topics game_num score
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_theme.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood theme past_topics

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "theme"
}

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
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood soviet_theme past_topics game_num score
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_soviet.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood soviet_theme past_topics

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "soviet"
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
		log "[NEWS] 全ニュースが既読 → 既読履歴をリセットして再読モードに切替"
		: > "$PAST_NEWS_READ"
		: > "$PAST_NEWS_READ_KEYS"
		: > "$PAST_NEWS_TOPIC_KEYS"
		: > "$PAST_NEWS_READ_SOURCES"
		unread_news_headlines="$news_headlines"
	fi

	unread_news_headlines=$(_prepare_news_prompt_blocks "$unread_news_headlines")

	local selected_news selected_block
	selected_block=$(_random_pick_news_block "$unread_news_headlines")
	if [ -z "$selected_block" ]; then
		log "[NEWS] ニュースブロック選定失敗 → スキップ"
		return 1
	fi
	selected_news=$(printf '%s\n' "$selected_block" | head -n 1 | sed 's/^■ //')
	log "[NEWS] スクリプト選定: ${selected_news}"

	local selected_key selected_topic_key selected_source_name selected_source_key
	selected_key=$(_news_title_key "$selected_news")
	selected_topic_key=$(_news_topic_key "$selected_news")
	selected_source_name=$(_news_source_name_for_title "$selected_news")
	selected_source_key=$(_news_source_key_from_name "$selected_source_name")
	if [ -n "$selected_key" ]; then
		echo "$selected_news" >>"$PAST_NEWS_READ"
		echo "$selected_key" >>"$PAST_NEWS_READ_KEYS"
		[ -n "$selected_topic_key" ] && echo "$selected_topic_key" >>"$PAST_NEWS_TOPIC_KEYS"
		_append_news_read_source "$selected_source_key"
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

$(_radio_output_rules 1000 2000)
PROMPT

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "news" --selected-news "$selected_news"
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

start_radio_corner_strategy() {
	local strategy_diff="$1" scores="$2" game_num="$3" best_score="$4"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood past_topics game_num scores best_score strategy_diff
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_strategy.md" > "$prompt_file"
	unset persona_block output_rules _rc_time _rc_period _rc_mood past_topics scores strategy_diff

	_radio_generate_and_play "$prompt_file" "$game_num" "${best_score}" "strategy"
}

#=== 時事ニュースコーナー (jiji) ===

_filter_unread_jiji_blocks() {
	local jiji_tmp
	jiji_tmp=$(mktemp /tmp/eloop_jiji_blocks_XXXXXXXX)
	cat >"$jiji_tmp"
	python3 "$ELOOP_LIB_DIR/lib/news_filter.py" filter_unread \
		"$TMP_HISTORY_DIR/.past_jiji_titles.txt" \
		"$TMP_HISTORY_DIR/.past_jiji_keys.txt" \
		"$jiji_tmp"
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

#=== ニュース: 毎ゲーム取得 & 再生 ===

_legacy_fetch_and_play_news() {
	local game_num="$1" score="$2"
	# 旧呼び出し（引数なし）でも、起動時点の値を固定して後段に渡す
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' || echo 0)

	log "[NEWS] ニュース取得..."
	./fetch_news.sh 2>/dev/null

	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		start_radio_corner_news "$game_num" "$score"
	else
		log "[NEWS] ニュースなし、スキップ"
	fi
}

#=== ラジオトーク: ディスパッチャー ===

_legacy_start_random_radio_corner() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' || echo 0)

	# ニュースは毎ゲーム別途実行するので、ここでは除外
	local candidates=("theme" "soviet" "recap")

	local pick="${candidates[$((RANDOM % ${#candidates[@]}))]}"
	log "[RADIO] コーナー選択: ${pick}"

	case "$pick" in
	theme)   start_radio_corner_theme "$game_num" "$score" ;;
	soviet)  start_radio_corner_soviet "$game_num" "$score" ;;
	recap)   start_radio_corner_recap "$game_num" "$score" ;;
	esac
}

_legacy_schedule_nonessential_audio_jobs() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' || echo 0)

	# 配信演出の頻度 (変更しても毎ループ source で即反映)
	local news_interval_day=4
	local news_interval_night=8
	local news_night_start_hour=2
	local news_night_end_hour=5
	local news_phase=1
	local radio_interval=5
	local radio_phase=0
	local comment_backlog_skip_threshold=4

	local comment_queued=0 comment_playing=0 comment_total=0
	local skip_nonessential_radio=false
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	if is_comment_backlog_high "$comment_backlog_skip_threshold"; then
		skip_nonessential_radio=true
	fi

	local current_hour current_news_interval current_news_mode
	current_hour=$(date +%H)
	if (( 10#$current_hour >= news_night_start_hour && 10#$current_hour < news_night_end_hour )); then
		current_news_interval="$news_interval_night"
		current_news_mode="night"
	else
		current_news_interval="$news_interval_day"
		current_news_mode="day"
	fi

	if [ "$current_news_mode" != "${LAST_NEWS_MODE:-}" ]; then
		log "[NEWS] schedule mode=${current_news_mode} interval=${current_news_interval} (night: ${news_night_start_hour}:00-${news_night_end_hour}:00)"
		LAST_NEWS_MODE="$current_news_mode"
	fi

	if (( game_num % current_news_interval == news_phase )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[NEWS] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			fetch_and_play_news "$game_num" "$score" &
		fi
	fi

	if (( game_num % radio_interval == radio_phase )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[RADIO] skip random corner: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			start_random_radio_corner "$game_num" "$score" &
		fi
	fi

	# 時事ニュースコーナー（2時間に1回）
	local jiji_interval_sec=7200
	local jiji_last_file="$TMP_STATE_DIR/.jiji_last_run"
	local jiji_last_ts now_ts jiji_elapsed
	now_ts=$(date +%s)
	jiji_last_ts=$(cat "$jiji_last_file" 2>/dev/null || echo 0)
	jiji_elapsed=$((now_ts - jiji_last_ts))
	if [ "$jiji_elapsed" -ge "$jiji_interval_sec" ]; then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[JIJI] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			echo "$now_ts" > "$jiji_last_file"
			start_radio_corner_jiji "$game_num" "$score" &
		fi
	fi
}

#=== ソ連祝賀トーク ===

generate_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_celebration_XXXXXXXX)

	export score turns game_num current_time
	envsubst < "$ELOOP_LIB_DIR/prompts/celebration.md" > "$celebration_prompt_file"
	echo "generating:celebration:$(date +%s)" > $RADIO_STATE_FILE
	log "[CELEBRATION] 生成中..."
	local celebration_talk
	celebration_talk=$(_run_opencode_radio "$RADIO_AGENT" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$celebration_prompt_file")
	fi
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_claude_radio "$celebration_prompt_file")
	fi
	rm -f "$celebration_prompt_file"

	if [ -n "$celebration_talk" ]; then
		echo "$celebration_talk" >tmp/radio_celebration.txt
		echo "playing:celebration:$(date +%s)" > $RADIO_STATE_FILE
		log "[CELEBRATION] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	else
		_radio_clear_state "celebration"
		log "[CELEBRATION] 祝賀トーク生成失敗"
	fi
}
