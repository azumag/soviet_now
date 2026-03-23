# broadcast/radio_factcheck.sh - ファクトチェック, Webグラウンディング


_radio_extract_grounding_query() {
	local corner_name="$1" prompt_context="$2" selected_news="${3:-}" query=""
	case "$corner_name" in
	news)
		query="$selected_news"
		;;
	theme)
		query=$(_radio_extract_prompt_section_value "【今回の脱線テーマ指定】" "$prompt_context")
		;;
	soviet)
		query=$(_radio_extract_prompt_section_value "【今回の脱線テーマ指定】" "$prompt_context")
		;;
	esac
	printf '%s' "$query" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//'
}

_radio_fetch_web_grounding() {
	local corner_name="$1" prompt_context="$2" selected_news="${3:-}"
	[ "${RADIO_WEB_GROUNDING_ENABLED:-1}" = "1" ] || return 0

	local query grounding grounding_timeout rc
	query=$(_radio_extract_grounding_query "$corner_name" "$prompt_context" "$selected_news")
	[ -n "$query" ] || return 0
	grounding_timeout="${RADIO_WEB_GROUNDING_TIMEOUT_SEC:-30}"

	log "[RADIO:${corner_name}] web grounding取得中... query=${query}" >&2
	grounding=$(timeout "${grounding_timeout}s" \
		python3 "$ELOOP_LIB_DIR/fetch_radio_grounding.py" \
			--corner "$corner_name" \
			--query "$query" \
			--ttl-sec "${RADIO_WEB_GROUNDING_TTL_SEC:-21600}" \
			--max-sources "${RADIO_WEB_GROUNDING_MAX_SOURCES:-3}" \
			--cache-dir "$RADIO_WEB_GROUNDING_CACHE_DIR" 2>/dev/null)
	rc=$?
	if [ "$rc" -eq 124 ]; then
		log "[RADIO:${corner_name}] web grounding timeout (${grounding_timeout}s) -> continue without grounding" >&2
		return 0
	fi
	if [ "$rc" -ne 0 ]; then
		log "[RADIO:${corner_name}] web grounding failed rc=${rc} -> continue without grounding" >&2
		return 0
	fi
	if [ -n "$grounding" ]; then
		log "[RADIO:${corner_name}] web grounding取得成功" >&2
	fi
	printf '%s' "$grounding"
}

_radio_should_fact_check() {
	local corner_name="$1"
	[ "${RADIO_FACT_CHECK_ENABLED:-1}" != "0" ] || return 1
	local skip_list=" ${RADIO_FACT_CHECK_SKIP_CORNERS:-} "
	case "$skip_list" in
	*" ${corner_name} "*) return 1 ;;
	esac
	return 0
}

_radio_compact_text_len() {
	python3 -c 'import re,sys; print(len(re.sub(r"\s+", "", sys.stdin.read())))'
}

_radio_fact_check_length_ok() {
	local original="$1" checked="$2"
	local orig_len checked_len
	orig_len=$(printf '%s' "$original" | _radio_compact_text_len)
	checked_len=$(printf '%s' "$checked" | _radio_compact_text_len)
	awk -v o="${orig_len:-0}" -v c="${checked_len:-0}" -v ratio="${RADIO_FACT_CHECK_MIN_RATIO:-0.68}" -v max_shrink="${RADIO_FACT_CHECK_MAX_ABS_SHRINK:-700}" '
	BEGIN {
	    if (o < 400) exit 0
	    if (c >= o * ratio) exit 0
	    if ((o - c) <= max_shrink) exit 0
	    exit 1
	}'
}

_radio_fact_check_style_reason() {
	local original="$1" checked="$2" issues="$3"
	printf '%s\0%s\0%s' "$original" "$checked" "$issues" | \
		python3 -c '
import difflib
import re
import sys

few_issues_max = int(float(sys.argv[1]))
min_similarity_noissues = float(sys.argv[2])
min_similarity_few = float(sys.argv[3])
max_paragraph_drop = int(float(sys.argv[4]))

parts = sys.stdin.buffer.read().split(b"\0", 2)
while len(parts) < 3:
    parts.append(b"")
original = parts[0].decode("utf-8", "ignore")
checked = parts[1].decode("utf-8", "ignore")
issues = parts[2].decode("utf-8", "ignore")

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def paras(text: str):
    return [ln.strip() for ln in text.splitlines() if ln.strip()]

issue_lines = []
for raw in issues.splitlines():
    line = raw.strip()
    if not line or line == "なし":
        continue
    if re.fullmatch(r"-+", line):
        continue
    issue_lines.append(line)

ratio = difflib.SequenceMatcher(None, norm(original), norm(checked)).ratio()
orig_paras = paras(original)
checked_paras = paras(checked)

if not issue_lines and ratio < min_similarity_noissues:
    print(f"rewrite_too_large_noissues ratio={ratio:.2f}")
    raise SystemExit(0)

if len(issue_lines) <= few_issues_max and ratio < min_similarity_few:
    print(f"rewrite_too_large_few_issues ratio={ratio:.2f} issues={len(issue_lines)}")
    raise SystemExit(0)

if len(orig_paras) >= 4 and len(checked_paras) < max(1, len(orig_paras) - max_paragraph_drop):
    print(f"paragraph_drop {len(orig_paras)}->{len(checked_paras)}")
    raise SystemExit(0)

print("")
' \
			"${RADIO_FACT_CHECK_FEW_ISSUES_MAX:-2}" \
			"${RADIO_FACT_CHECK_MIN_SIMILARITY_NOISSUES:-0.90}" \
			"${RADIO_FACT_CHECK_MIN_SIMILARITY_FEW_ISSUES:-0.74}" \
			"${RADIO_FACT_CHECK_MAX_PARAGRAPH_DROP:-2}"
}

_radio_fact_check_body() {
	local corner_name="$1" prompt_context="$2" talk_body="$3" selected_news="${4:-}"
	if ! _radio_should_fact_check "$corner_name"; then
		printf '%s' "$talk_body"
		return 0
	fi
	[ -n "$talk_body" ] || return 1

	local web_grounding="" prompt_context_trimmed
	local factcheck_timeout factcheck_claude_timeout
	factcheck_timeout="${RADIO_FACT_CHECK_OPENCODE_TIMEOUT_SEC:-45}"
	factcheck_claude_timeout="${RADIO_FACT_CHECK_CLAUDE_TIMEOUT_SEC:-60}"
	web_grounding=$(_radio_fetch_web_grounding "$corner_name" "$prompt_context" "$selected_news")
	prompt_context_trimmed=$(_radio_compact_fact_check_context "$corner_name" "$prompt_context")
	if [ ${#prompt_context_trimmed} -gt 16000 ]; then
		prompt_context_trimmed=$(printf '%s' "$prompt_context_trimmed" | tail -c 16000)
	fi

	local factcheck_dir prompt_file raw_output safe_script issues issue_preview debug_dump last_candidate style_reason
	last_candidate=""
	factcheck_dir=$(mktemp -d /tmp/eloop_radio_factcheck_XXXXXXXX) || return 1
	prompt_file="$factcheck_dir/prompt.txt"
	cat >"$prompt_file" <<PROMPT
あなたは放送前のファクトチェック担当です。
与えられた「元原稿」を、与えられた「材料」から支持できる範囲にだけ言い換えてください。
目的は「誤情報を減らしつつ、面白さ・語り口・熱量をできるだけ保つこと」です。

【最優先ルール】
- 材料にない新事実を絶対に足さない
- 固有名詞、年号、人数、数値、因果関係、逸話、引用は、材料で支えられないなら削るか弱める
- 自信が低い細部は、「と言われます」「とされています」「とみられます」などの無責任な逃げ表現へ言い換えず、その細部ごと削るか、確認できる範囲の事実だけに言い換える
- news / strategy / weather / market では、材料にない断定を禁止
- theme / soviet / celebration でも、確信のない歴史細部は一般論へ落とす
- Web検索で集めた資料がある場合は、それを最優先で使う
- 必要な箇所以外は極力書き換えないこと。問題がない文はそのまま残すこと
- 元の語り口、流れ、長さはなるべく維持する
- ジョーク、比喩、ツッコミ、感想、余韻、勢い、情景描写は、そこ自体が事実主張でない限り残すこと
- 事務的・教科書的・無味乾燥な文章に平板化しないこと
- unsupported な固有名詞や数字が多い段落でも、段落ごと消さずに一般化して言い換えること
- 特に news / theme / soviet は、元原稿の7割未満まで短くしないこと。削る代わりに一般表現へ置き換えること
- 読み上げ用プレーンテキストのみを返す
- マークダウン、見出し、箇条書き、補足解説は禁止
- 出力形式を厳守すること

【センシティブ判定の範囲】
- あなたの役割は「事実誤認・嘘・でっちあげ」のチェックのみ
- 政治・戦争・外交・軍事・紛争・歴史的事件の話題は、事実に基づく限りそのまま通すこと。「センシティブだから」という理由で拒否・削除・弱体化してはならない
- ブロックすべきは性的コンテンツのみ。それ以外の話題は内容の正確性だけを判定すること
- 「安全ガイドライン」「コンテンツポリシー」を理由に原稿の出力を拒否することは禁止。必ず===SAFE_SCRIPT===形式で原稿を出力すること

【コーナー】
${corner_name}

【材料】
${prompt_context_trimmed}

【Web検索で集めた資料】
${web_grounding:-（外部資料なし。材料の範囲だけで保守的に直すこと）}

【補足】
${selected_news:+ニュース選択見出し: ${selected_news}}

【元原稿】
${talk_body}

【出力形式】
===SAFE_SCRIPT===
ここに安全化した最終原稿だけを書く

===ISSUES===
削った・弱めた点を短く列挙。なければ「なし」
PROMPT

	local model
	for model in "${RADIO_FACT_CHECK_AGENT:-}" "${RADIO_FACT_CHECK_FALLBACK:-}"; do
		[ -n "$model" ] || continue
		log "[RADIO:${corner_name}] fact-check中... (${model}, timeout=${factcheck_timeout}s)" >&2
		raw_output=$(RADIO_OPENCODE_TIMEOUT="$factcheck_timeout" _run_opencode_radio "$model" "$prompt_file")
		safe_script=$(printf '%s\n' "$raw_output" | _radio_cleanup_fact_checked_text | _sanitize_onair_text | _normalize_radio_tone)
		issues=$(printf '%s\n' "$raw_output" | _radio_extract_fact_check_issues)
		if _is_valid_radio_talk "$safe_script"; then
			if ! _radio_fact_check_length_ok "$talk_body" "$safe_script"; then
				last_candidate=""
				log "[RADIO:${corner_name}] fact-check短文化しすぎ (${model}) -> 次候補へ" >&2
				continue
			fi
			style_reason=$(_radio_fact_check_style_reason "$talk_body" "$safe_script" "$issues")
			if [ -n "$style_reason" ]; then
				last_candidate=""
				log "[RADIO:${corner_name}] fact-check平板化しすぎ (${model}) -> 次候補へ (${style_reason})" >&2
				continue
			fi
			last_candidate="$safe_script"
			issue_preview=$(printf '%s\n' "$issues" | sed '/^[[:space:]]*$/d' | head -n 2 | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g; s/[[:space:]]*$//')
			if [ -n "$issue_preview" ] && [ "$issue_preview" != "なし" ]; then
				log "[RADIO:${corner_name}] fact-check通過 (${model}): ${issue_preview}" >&2
			else
				log "[RADIO:${corner_name}] fact-check通過 (${model})" >&2
			fi
			rm -rf "$factcheck_dir"
			printf '%s' "$safe_script"
			return 0
		fi
	done

	log "[RADIO:${corner_name}] fact-check fallback -> claude (${RADIO_FACT_CHECK_CLAUDE_MODEL}, timeout=${factcheck_claude_timeout}s)" >&2
	raw_output=$(RADIO_CLAUDE_TIMEOUT="$factcheck_claude_timeout" _run_claude_radio_with_model "$prompt_file" "$RADIO_FACT_CHECK_CLAUDE_MODEL")
	safe_script=$(printf '%s\n' "$raw_output" | _radio_cleanup_fact_checked_text | _sanitize_onair_text | _normalize_radio_tone)
	issues=$(printf '%s\n' "$raw_output" | _radio_extract_fact_check_issues)
	if _is_valid_radio_talk "$safe_script"; then
		if ! _radio_fact_check_length_ok "$talk_body" "$safe_script"; then
			last_candidate=""
			log "[RADIO:${corner_name}] fact-check短文化しすぎ (claude:${RADIO_FACT_CHECK_CLAUDE_MODEL}) -> 元原稿へ" >&2
		else
			style_reason=$(_radio_fact_check_style_reason "$talk_body" "$safe_script" "$issues")
			if [ -n "$style_reason" ]; then
				last_candidate=""
				log "[RADIO:${corner_name}] fact-check平板化しすぎ (claude:${RADIO_FACT_CHECK_CLAUDE_MODEL}) -> 元原稿へ (${style_reason})" >&2
			else
				last_candidate="$safe_script"
				issue_preview=$(printf '%s\n' "$issues" | sed '/^[[:space:]]*$/d' | head -n 2 | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g; s/[[:space:]]*$//')
				if [ -n "$issue_preview" ] && [ "$issue_preview" != "なし" ]; then
					log "[RADIO:${corner_name}] fact-check通過 (claude:${RADIO_FACT_CHECK_CLAUDE_MODEL}): ${issue_preview}" >&2
				else
					log "[RADIO:${corner_name}] fact-check通過 (claude:${RADIO_FACT_CHECK_CLAUDE_MODEL})" >&2
				fi
				rm -rf "$factcheck_dir"
				printf '%s' "$safe_script"
				return 0
			fi
		fi
	fi

	debug_dump="$TMP_DEBUG_DIR/radio_factcheck_failed_${corner_name}_$(date +%s).txt"
	{
		echo "===ORIGINAL==="
		printf '%s\n' "$talk_body"
		echo
		echo "===RAW_CHECK_OUTPUT==="
		printf '%s\n' "$raw_output"
	} >"$debug_dump"
	if _is_valid_radio_talk "$last_candidate"; then
		log "[RADIO:${corner_name}] fact-check不調だが抽出本文を採用 (dump: $debug_dump)" >&2
		rm -rf "$factcheck_dir"
		printf '%s' "$last_candidate"
		return 0
	fi
	log "[RADIO:${corner_name}] fact-check失敗 -> 元原稿で続行 (dump: $debug_dump)" >&2
	rm -rf "$factcheck_dir"
	printf '%s' "$talk_body"
	return 0
}
