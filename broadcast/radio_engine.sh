# broadcast/radio_engine.sh - AI実行ラッパー, パース, サニタイズ, 生成&再生


#=== z.ai エンドポイント経由で claude CLI を実行 ===

_run_zai_radio() {
	local model="$1" prompt_file="$2"
	local output timeout_sec stderr_file
	# RADIO_OPENCODE_TIMEOUT による上書きも後方互換で受け付ける
	timeout_sec="${RADIO_OPENCODE_TIMEOUT:-${ZAI_TIMEOUT:-180}}"
	if [ ! -s "$prompt_file" ]; then
		return 1
	fi
	log "[RADIO] zai call (model=$model, prompt=$(wc -c < "$prompt_file" | tr -d ' ')B)" >&2
	stderr_file=$(mktemp /tmp/eloop_zai_radio_stderr_XXXXXXXX)
	local stderr_preview="" provider_error=false
	output=$(
		export ANTHROPIC_BASE_URL="$ZAI_BASE_URL"
		export ANTHROPIC_API_KEY="$ZAI_API_KEY"
		export ANTHROPIC_DEFAULT_HAIKU_MODEL="$model"
		cat "$prompt_file" | timeout "$timeout_sec" claude -p --model haiku --tools "$ZAI_RADIO_TOOLS" --permission-mode dontAsk 2>"$stderr_file"
	)
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 4000 "$stderr_file")
	fi
	if _contains_provider_error_text "$output" || { [ -n "$stderr_preview" ] && _contains_provider_error_text "$stderr_preview"; }; then
		provider_error=true
	fi
	[ -n "$stderr_preview" ] && log "[RADIO] zai stderr: $(printf '%s' "$stderr_preview" | head -c 500)" >&2
	rm -f "$stderr_file"
	if [ $rc -eq 124 ]; then
		log "[RADIO] zai timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		log "[RADIO] zai provider error treated as failure (model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[RADIO] zai failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	printf '%s' "$output"
}

# 後方互換: 既存の呼び出し元がそのまま動くようにエイリアス
_run_opencode_radio() {
	_run_zai_radio "$@"
}

_run_zai_comment() {
	local model="$1" prompt_file="$2"
	local sandbox_dir sandbox_prompt output timeout_sec
	timeout_sec="${ZAI_TIMEOUT:-180}"
	sandbox_dir=$(create_sandbox \
		"README.md" \
		"strategy.py" \
		"prompts/comment_response.md" \
		"$COMMENT_SPOKEN_HISTORY_DIR" \
		"$PAST_RADIO_TOPICS" \
		"score_history.txt" \
		"$RUSSIA_CREATION_HISTORY_FILE" \
		"$SOVIET_CREATION_HISTORY_FILE" \
		"$ROLLING_SCORES_FILE" \
		"show_status.sh" \
		"show_status_g.sh" \
		"status_dashboard.py" \
		"tmp/.comment_queue/comment_screenshot.jpg")
	if [ -z "$sandbox_dir" ] || [ ! -d "$sandbox_dir" ]; then
		log "[COMMENT] sandbox作成失敗 -> direct zai" >&2
		_run_zai_radio "$model" "$prompt_file"
		return
	fi
	sandbox_prompt="$sandbox_dir/tmp/comment_prompt.txt"
	mkdir -p "$(dirname "$sandbox_prompt")"
	cp "$prompt_file" "$sandbox_prompt" 2>/dev/null || {
		destroy_sandbox "$sandbox_dir"
		return 1
	}
	local stderr_file
	stderr_file=$(mktemp /tmp/eloop_zai_comment_stderr_XXXXXXXX)
	local stderr_preview="" provider_error=false
	output=$(
		export ANTHROPIC_BASE_URL="$ZAI_BASE_URL"
		export ANTHROPIC_API_KEY="$ZAI_API_KEY"
		export ANTHROPIC_DEFAULT_HAIKU_MODEL="$model"
		cd "$sandbox_dir" &&
			cat 'tmp/comment_prompt.txt' | timeout "$timeout_sec" claude -p --model haiku --tools "$ZAI_COMMENT_TOOLS" --permission-mode dontAsk 2>"$stderr_file"
	)
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 4000 "$stderr_file")
	fi
	if _contains_provider_error_text "$output" || { [ -n "$stderr_preview" ] && _contains_provider_error_text "$stderr_preview"; }; then
		provider_error=true
	fi
	[ -n "$stderr_preview" ] && log "[COMMENT] zai stderr: $(printf '%s' "$stderr_preview" | head -c 500)" >&2
	rm -f "$stderr_file"
	destroy_sandbox "$sandbox_dir"
	if [ $rc -eq 124 ]; then
		log "[COMMENT] zai timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		log "[COMMENT] zai provider error treated as failure (model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[COMMENT] zai failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	printf '%s' "$output"
}

# 後方互換
_run_opencode_comment() {
	_run_zai_comment "$@"
}

_run_claude_comment_with_model() {
	local prompt_file="$1"
	local model="${2:-$RADIO_CLAUDE_MODEL}"
	local sandbox_dir sandbox_prompt output timeout_sec
	timeout_sec="${COMMENT_CLAUDE_TIMEOUT:-180}"
	sandbox_dir=$(create_sandbox \
		"README.md" \
		"strategy.py" \
		"prompts/comment_response.md" \
		"$COMMENT_SPOKEN_HISTORY_DIR" \
		"$PAST_RADIO_TOPICS" \
		"score_history.txt" \
		"$RUSSIA_CREATION_HISTORY_FILE" \
		"$SOVIET_CREATION_HISTORY_FILE" \
		"$ROLLING_SCORES_FILE" \
		"show_status.sh" \
		"show_status_g.sh" \
		"status_dashboard.py" \
		"tmp/.comment_queue/comment_screenshot.jpg")
	if [ -z "$sandbox_dir" ] || [ ! -d "$sandbox_dir" ]; then
		log "[COMMENT] sandbox作成失敗 -> direct claude" >&2
		_run_claude_radio_with_model "$prompt_file" "$model"
		return
	fi
	sandbox_prompt="$sandbox_dir/tmp/comment_prompt.txt"
	mkdir -p "$(dirname "$sandbox_prompt")"
	cp "$prompt_file" "$sandbox_prompt" 2>/dev/null || {
		destroy_sandbox "$sandbox_dir"
		return 1
	}
	local stderr_file
	stderr_file=$(mktemp /tmp/eloop_claude_comment_stderr_XXXXXXXX)
	local stderr_preview="" provider_error=false login_error=false
	output=$(
		cd "$sandbox_dir" &&
			cat 'tmp/comment_prompt.txt' | timeout "$timeout_sec" claude -p --model "$model" --tools "$COMMENT_CLAUDE_TOOLS" --permission-mode dontAsk 2>"$stderr_file"
	)
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 4000 "$stderr_file")
	fi
	if _contains_provider_error_text "$output" || { [ -n "$stderr_preview" ] && _contains_provider_error_text "$stderr_preview"; }; then
		provider_error=true
	fi
	if _contains_claude_login_error_text "$output" || { [ -n "$stderr_preview" ] && _contains_claude_login_error_text "$stderr_preview"; }; then
		login_error=true
	fi
	if [ -n "$stderr_preview" ] || [ "$provider_error" = "true" ]; then
		mkdir -p "$(dirname "$COMMENT_CLAUDE_LOG_FILE")" 2>/dev/null || true
		{
			printf '[%s] rc=%s model=%s tools=%s\n' "$(date '+%F %T')" "$rc" "$model" "$COMMENT_CLAUDE_TOOLS"
			if [ -n "$stderr_preview" ]; then
				printf '[stderr]\n%s\n' "$stderr_preview"
			fi
			if [ "$provider_error" = "true" ]; then
				printf '[stdout]\n'
				printf '%s' "$output" | head -c 4000
				printf '\n'
			fi
			printf '\n\n'
		} >>"$COMMENT_CLAUDE_LOG_FILE" 2>/dev/null || true
		[ -n "$stderr_preview" ] && log "[COMMENT] claude stderr: $(printf '%s' "$stderr_preview" | head -c 500)" >&2
	fi
	[ "$login_error" = "true" ] && log "[COMMENT] claude unavailable: not logged in" >&2
	rm -f "$stderr_file"
	destroy_sandbox "$sandbox_dir"
	if [ $rc -eq 124 ]; then
		log "[COMMENT] claude timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		log "[COMMENT] claude provider/auth error treated as failure (model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[COMMENT] claude failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	printf '%s' "$output"
}

_run_claude_comment() {
	_run_claude_comment_with_model "$1" "$RADIO_CLAUDE_MODEL"
}

_run_claude_radio_with_model() {
	local prompt_file="$1"
	local model="${2:-$RADIO_CLAUDE_MODEL}"
	local prompt output timeout_sec
	timeout_sec="${RADIO_CLAUDE_TIMEOUT:-120}"
	if [ ! -s "$prompt_file" ]; then
		return 1
	fi
	# command substitution に混ざらないよう stderr に出す
	log "[RADIO] claude call (model=$model, prompt=$(wc -c < "$prompt_file" | tr -d ' ')B)" >&2
	local stderr_file
	stderr_file=$(mktemp /tmp/eloop_claude_stderr_XXXXXXXX)
	local stderr_preview="" provider_error=false login_error=false
	output=$(cat "$prompt_file" | timeout "$timeout_sec" claude -p --model "$model" 2>"$stderr_file")
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 500 "$stderr_file")
		log "[RADIO] claude stderr: $stderr_preview" >&2
	fi
	if _contains_provider_error_text "$output" || { [ -n "$stderr_preview" ] && _contains_provider_error_text "$stderr_preview"; }; then
		provider_error=true
	fi
	if _contains_claude_login_error_text "$output" || { [ -n "$stderr_preview" ] && _contains_claude_login_error_text "$stderr_preview"; }; then
		login_error=true
	fi
	[ "$login_error" = "true" ] && log "[RADIO] claude unavailable: not logged in" >&2
	rm -f "$stderr_file"
	if [ $rc -eq 124 ]; then
		log "[RADIO] claude timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		log "[RADIO] claude provider/auth error treated as failure (model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[RADIO] claude failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	printf '%s' "$output"
}

_run_claude_radio() {
	_run_claude_radio_with_model "$1" "$RADIO_CLAUDE_MODEL"
}

_write_radio_corner_status() {
	local status="$1" corner_name="$2" game_num="$3" score="$4" topic="${5:-}" reason="${6:-}" selected_news="${7:-}" extra_json="${8:-}"
	python3 - "$RADIO_CORNER_STATUS_FILE" "$status" "$corner_name" "$game_num" "$score" "$topic" "$reason" "$selected_news" "$extra_json" <<'PY' >/dev/null 2>&1
import json
import sys
from datetime import datetime, timezone

out_file, status, corner_name, game_num_raw, score_raw, topic, reason, selected_news, extra_json = sys.argv[1:9]

def to_int(value: str) -> int:
    try:
        return int(value)
    except Exception:
        return 0

payload = {
    "status": status,
    "corner": corner_name,
    "game_num": to_int(game_num_raw),
    "score": to_int(score_raw),
    "topic": topic,
    "reason": reason,
    "selected_news": selected_news,
    "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}

if extra_json:
    try:
        extra = json.loads(extra_json)
    except Exception:
        extra = {"note": extra_json}
    if isinstance(extra, dict):
        payload.update(extra)

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

_clean_comment_talk() {
	printf '%s\n' "$1" | python3 -c "$(cat <<'PY'
import re
import sys

lines = sys.stdin.read().splitlines()
clean = []
for raw in lines:
    line = raw.strip()
    if not line:
        continue
    if re.fullmatch(r'(assistant|analysis|final|tool_call|tool_result)', line, re.I):
        continue
    if re.fullmatch(r'(zai|glmflash|sonnet|claude|opencode)', line, re.I):
        continue
    if re.match(r'(agent|model|provider)\s*[:=]', line, re.I):
        continue
    if re.match(r'^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b', line, re.I):
        continue
    if re.match(r'^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
        continue
    if re.match(r'^(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
        continue
    if re.match(r'^(error|warning)\s*:', line, re.I):
        continue
    if re.search(r'file not found:|no such file or directory|permission denied|invalid arguments|could not find oldstring|no changes to apply', line, re.I):
        continue
    if line.startswith('```') or line == '^D':
        continue
    clean.append(raw.rstrip())

while clean:
    head = clean[0].strip()
    if re.match(r'^同志[^。]{0,140}という(コメント|ご質問|ご報告|ご挨拶|ご相談|ご指摘|話)ですね。?$', head):
        clean = clean[1:]
        continue
    if re.match(r'^(返信対象コメント|コメント前後文脈|直前コメント履歴|最近自分が実際に読み上げたコメント返し|前回のトーク内容|現在のゲーム状態メモ|配信UI説明メモ|ルール|再生成指示)', head):
        clean = clean[1:]
        continue
    if re.match(r'^(以下、|まず、?コメント|コメントを読み上げ)', head):
        clean = clean[1:]
        continue
    break

text = "\n".join(line for line in clean if line.strip()).strip()
text = re.sub(r'\n{3,}', '\n\n', text)
print(text, end='')
PY
)"
}

_is_valid_comment_talk() {
	local talk="$1"
	local compact
	compact=$(printf '%s' "$talk" | tr -d '[:space:]')
	[ ${#compact} -ge 24 ] || return 1
	printf '%s' "$talk" | grep -Eq '[。！？]' || return 1
	if printf '%s' "$talk" | grep -Eiq 'tool_call|tool_result|assistant_response|^analysis$|^final$|^assistant$|^provider[[:space:]]*[:=]|^model[[:space:]]*[:=]|^agent[[:space:]]*[:=]'; then
		return 1
	fi
	if _contains_provider_error_text "$talk" || printf '%s' "$talk" | grep -Eiq 'unexpected token|syntaxerror|referenceerror|typeerror|could not find oldstring|no changes to apply|rejected permission'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eiq '(^|[[:space:]])(read failed|edit failed|write failed|file not found:|no such file or directory|permission denied|invalid arguments)'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eiq '(^|[[:space:]])(read|glob|grep|ls|edit|write|multiedit)[[:space:]]+["./]'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eiq '^[[:space:]]*[✗✕×✱→►▸]' ; then
		return 1
	fi
	# 「検索できない」「データがない」系の拒否応答を検出 → 無効にしてfallbackさせる
	if printf '%s' "$talk" | grep -Eq '(リアルタイム|最新).*(データ|情報).*(持って|ありません|ございません|取得できません|アクセスできません|提供できません|確認できません)|検索(機能|ツール).*(ありません|ございません|持って|できません)|インターネット.*(アクセス|接続).*(できません|ありません)|データフィード.*(ありません|ございません)|外部.*(アクセス|接続).*(できません|ありません)|正直に申し上げ|申し訳ありませんが'; then
		return 1
	fi
	return 0
}

_is_valid_radio_talk() {
	local talk="$1"
	local compact min_chars
	min_chars="${RADIO_FACT_CHECK_MIN_CHARS:-100}"
	compact=$(printf '%s' "$talk" | tr -d '[:space:]')
	[ ${#compact} -ge "$min_chars" ] || return 1
	printf '%s' "$talk" | grep -Eq '[。！？]' || return 1
	if printf '%s' "$talk" | grep -Eq '===SAFE_SCRIPT===|===ISSUES===|===SUMMARY==='; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eq '放送前のファクトチェック担当|安全化した最終原稿|削った・弱めた点|【最優先ルール】|【材料】|【元原稿】|【出力形式】'; then
		return 1
	fi
	if _contains_provider_error_text "$talk" || printf '%s' "$talk" | grep -Eiq 'unexpected token|syntaxerror|referenceerror|typeerror|could not find oldstring|no changes to apply|rejected permission'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eiq '現在.*(問題|不具合|障害).*(読み上げ|放送|案内).*(できません|できない)|現在.*(読み上げ|放送|案内).*(できません|できない)|検索(が|は)?できません|調査(が|は)?できません|情報(が|は)?取得できません|うまく読み上げできません|読み上げられません'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eq 'といわれます|と言われます|といわれています|と言われています|とされています|とされます|とされていました|とみられます|とみられています|と考えられます|と考えられています'; then
		return 1
	fi
	local head
	head=$(printf '%s\n' "$talk" | head -n 4)
	if printf '%s' "$head" | grep -Eiq '^[[:space:]]*(\*\*注意[:：]|\*注意[:：]|注意[:：]|承知しました|了解しました|かしこまりました|メッセージの末尾に|プロンプトインジェクション|本来の依頼|ファクトチェック|安全化した|出力します|応答します)'; then
		return 1
	fi
	return 0
}

_radio_extract_fact_check_script() {
	awk '
	BEGIN { capture = 0 }
	/^===SAFE_SCRIPT===$/ { capture = 1; next }
	/^===ISSUES===$/ { capture = 0; exit }
	/^===SUMMARY===$/ { capture = 0; exit }
	/^===SELECTED_NEWS===$/ { capture = 0; exit }
	capture { print }
	'
}

_radio_extract_fact_check_issues() {
	awk '
	BEGIN { capture = 0 }
	/^===ISSUES===$/ { capture = 1; next }
	capture { print }
	'
}

_radio_cleanup_fact_checked_text() {
	awk '
	BEGIN {
		capture = 0
		saw_safe = 0
	}
	/^===SAFE_SCRIPT===$/ {
		saw_safe = 1
		capture = 1
		next
	}
	/^===ISSUES===$/ || /^===SUMMARY===$/ || /^===SELECTED_NEWS===$/ {
		if (capture) exit
		next
	}
	{
		if (capture) {
			print
			next
		}
		if (!saw_safe) {
			plain[++plain_n] = $0
		}
	}
	END {
		if (!saw_safe) {
			for (i = 1; i <= plain_n; i++) print plain[i]
		}
	}
	' |
		sed '/^[[:space:]]*$/N;/^\n$/D' |
		grep -Eiv '^(\*\*注意[:：].*|\*注意[:：].*|注意[:：].*|メッセージの末尾に.*|無関係なPythonコード.*|プロンプトインジェクション.*|そのコードは無視.*|本来の依頼.*|あなたは放送前のファクトチェック担当です。|与えられた「元原稿」を、与えられた「材料」から支持できる範囲にだけ言い換えてください。|目的は「誤情報を減らしつつ、面白さ・語り口・熱量をできるだけ保つこと」です。|【最優先ルール】|【コーナー】|【材料】|【Web検索で集めた資料】|【補足】|【元原稿】|【出力形式】|ここに安全化した最終原稿だけを書く|削った・弱めた点を短く列挙。なければ「なし」|---+)$' |
		grep -Ev '^- '
}

_radio_extract_prompt_section_value() {
	local header="$1" prompt_context="$2"
	printf '%s\n' "$prompt_context" | awk -v header="$header" '
	BEGIN { capture = 0 }
	$0 == header { capture = 1; next }
	capture {
		if ($0 ~ /^【/ ) exit
		if ($0 ~ /^[[:space:]]*$/) next
		print
		exit
	}
	'
}

_radio_extract_prompt_section_block() {
	local header="$1" prompt_context="$2"
	printf '%s\n' "$prompt_context" | awk -v header="$header" '
	BEGIN { capture = 0 }
	$0 == header { capture = 1; next }
	capture {
		if ($0 ~ /^【/ ) exit
		print
	}
	'
}

_radio_compact_fact_check_context() {
	local corner_name="$1" prompt_context="$2"
	local current_time mood situation block title_line compact
	current_time=$(_radio_extract_prompt_section_value "【現在時刻】" "$prompt_context")
	mood=$(_radio_extract_prompt_section_value "【時間帯の雰囲気】" "$prompt_context")
	situation=$(_radio_extract_prompt_section_block "【状況】" "$prompt_context")

	case "$corner_name" in
	news)
		block=$(_radio_extract_prompt_section_block "【最新ニュース - 実際の本日のニュース】" "$prompt_context")
		compact=$(cat <<EOF
【現在時刻】
${current_time}
【時間帯の雰囲気】
${mood}
【状況】
${situation}
【最新ニュース】
${block}
EOF
)
		;;
	theme)
		block=$(_radio_extract_prompt_section_block "【今回の脱線テーマ指定】" "$prompt_context")
		compact=$(cat <<EOF
【現在時刻】
${current_time}
【時間帯の雰囲気】
${mood}
【状況】
${situation}
【今回の脱線テーマ指定】
${block}
EOF
)
		;;
	soviet)
		block=$(_radio_extract_prompt_section_block "【今回の脱線テーマ指定】" "$prompt_context")
		compact=$(cat <<EOF
【現在時刻】
${current_time}
【時間帯の雰囲気】
${mood}
【状況】
${situation}
【今回の脱線テーマ指定】
${block}
EOF
)
		;;
	weather|fortune|market|dinner|deals|survival)
		compact=$(cat <<EOF
【現在時刻】
${current_time}
【時間帯の雰囲気】
${mood}
【状況】
${situation}
EOF
)
		;;
	strategy)
		block=$(_radio_extract_prompt_section_block "【作戦変更の差分】" "$prompt_context")
		compact=$(cat <<EOF
【現在時刻】
${current_time}
【時間帯の雰囲気】
${mood}
【状況】
${situation}
【作戦変更の差分】
${block}
EOF
)
		;;
	*)
		compact="$prompt_context"
		;;
	esac

	if [ ${#compact} -gt 12000 ]; then
		printf '%s' "$compact" | tail -c 12000
	else
		printf '%s' "$compact"
	fi
}

_radio_parse_output_to_files() {
	local body_file="$1" summary_file="$2" selected_news_file="$3"
	local parser_file
	parser_file=$(mktemp /tmp/eloop_radio_parser_XXXXXXXX)
	cat >"$parser_file" <<'PY'
import re
import sys
from pathlib import Path

body_path, summary_path, selected_path = sys.argv[1:4]
raw = sys.stdin.read().replace("\r", "")

raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)
raw = re.sub(
    r"</?(?:arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>",
    "",
    raw,
    flags=re.IGNORECASE,
)

lines = [line.strip() for line in raw.splitlines()]
clean_lines = []
for line in lines:
    if not line:
        continue
    if line.startswith("```"):
        continue
    if line == "^D":
        continue
    if re.fullmatch(r"/[^ ]*", line):
        continue
    if line.startswith("/Users/"):
        continue
    if re.fullmatch(r"</?[^>]+>", line):
        continue
    clean_lines.append(line)

def marker_positions(marker):
    return [idx for idx, line in enumerate(clean_lines) if line == marker]

summary_pos = marker_positions("===SUMMARY===")
selected_pos = marker_positions("===SELECTED_NEWS===")
main_lines = clean_lines[: selected_pos[0]] if selected_pos else clean_lines

selected_news = ""
if selected_pos:
    for line in clean_lines[selected_pos[0] + 1 :]:
        if not line or line.startswith("==="):
            continue
        selected_news = line
        break
selected_news = re.sub(r"</?[A-Za-z_][^>]*>", "", selected_news).strip()
selected_news = re.sub(r"\s+", " ", selected_news)[:240]

summary = ""
if summary_pos:
    summary_lines = []
    for line in main_lines[summary_pos[0] + 1 :]:
        if line.startswith("==="):
            break
        if not line:
            continue
        summary_lines.append(line)
        if len(summary_lines) >= 2:
            break
    if summary_lines:
        summary = " / ".join(summary_lines)
summary = re.sub(r"</?[A-Za-z_][^>]*>", "", summary).strip()
summary = re.sub(r"\s+", " ", summary)[:220]

segments = []
start = 0
for idx, line in enumerate(main_lines):
    if line == "===SUMMARY===":
        segments.append(main_lines[start:idx])
        start = idx + 1
segments.append(main_lines[start:])

def score_segment(seg):
    txt = " ".join(seg).strip()
    if not txt:
        return -1
    punct = len(re.findall(r"[。.!?！？]", txt))
    return len(txt) + punct * 80

body_lines = []
if segments:
    best = max(segments, key=score_segment)
    body_lines = [line for line in best if line and not line.startswith("===")]

if body_lines:
    head = body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        body_lines = body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(body_lines) >= 2:
        body_lines = body_lines[1:]

body = "\n".join(body_lines).strip()
body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

if len(body) < 100:
    used_before_summary = False
    if summary_pos and summary_pos[0] < len(main_lines):
        before_summary = [line for line in main_lines[: summary_pos[0]] if not line.startswith("===")]
        if before_summary:
            body = "\n".join(before_summary).strip()
            used_before_summary = True
    if len(body) < 100 and not used_before_summary:
        fallback_lines = [line for line in main_lines if not line.startswith("===")]
        body = "\n".join(fallback_lines).strip()
    body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

clean_body_lines = [line.strip() for line in body.splitlines() if line.strip()]
meta_prefixes = (
    "**注意:",
    "**注意：",
    "*注意:",
    "*注意：",
    "注意:",
    "注意：",
    "承知しました",
    "了解しました",
    "かしこまりました",
    "メッセージの末尾に",
    "プロンプトインジェクション",
    "本来の依頼",
    "ファクトチェック",
    "安全化した",
    "出力します",
    "応答します",
)
while clean_body_lines:
    head = clean_body_lines[0]
    if head == "---":
        clean_body_lines = clean_body_lines[1:]
        continue
    if head.startswith(meta_prefixes):
        clean_body_lines = clean_body_lines[1:]
        continue
    break
if clean_body_lines:
    head = clean_body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        clean_body_lines = clean_body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(clean_body_lines) >= 2:
        clean_body_lines = clean_body_lines[1:]
body = "\n".join(clean_body_lines).strip()

body = re.sub(r"\n{3,}", "\n\n", body)
if len(body) > 12000:
    body = body[:12000]

Path(body_path).write_text(body, encoding="utf-8")
Path(summary_path).write_text(summary, encoding="utf-8")
Path(selected_path).write_text(selected_news, encoding="utf-8")
PY
	python3 "$parser_file" "$body_file" "$summary_file" "$selected_news_file"
	local rc=$?
	rm -f "$parser_file"
	return $rc
}

_radio_dedup_text() {
	python3 -c "
import sys
text = sys.stdin.read()
lines = text.split('\n')
seen_repeat = 0
cut_at = len(lines)
for i in range(1, len(lines)):
    if lines[i].strip() and lines[i] == lines[i-1]:
        seen_repeat += 1
        if seen_repeat >= 3:
            cut_at = i - 2
            break
    else:
        seen_repeat = 0
from collections import Counter
chunk_size = 20
chunks = [text[i:i+chunk_size] for i in range(0, len(text)-chunk_size)]
freq = Counter(chunks)
repeat_phrase = None
for phrase, count in freq.most_common(1):
    if count >= 5 and len(phrase.strip()) > 5:
        repeat_phrase = phrase
        break
result = '\n'.join(lines[:cut_at])
if repeat_phrase:
    idx = 0
    for _ in range(3):
        idx = result.find(repeat_phrase, idx)
        if idx == -1:
            break
        idx += len(repeat_phrase)
    if idx > 0:
        result = result[:idx]
if len(result) > 10000:
    result = result[:10000]
print(result, end='')
	"
}

_sanitize_onair_text() {
	python3 -c "$(cat <<'PY'
import re
import sys

text = sys.stdin.read()
drop_line_patterns = [
    r'failed to authenticat(?:e|ed)',
    r'api error[: ]',
    r'authentication_error',
    r'invalid bearer token',
    r'request_id',
    r'\binvalid error token\b',
    r'\binvalid token\b',
    r'\bunexpected token\b',
    r'\bsyntaxerror\b',
    r'\breferenceerror\b',
    r'\btypeerror\b',
    r'could not find oldstring',
    r'no changes to apply',
    r'the user rejected permission',
    r'permission to use this specific tool call',
    r'^\s*[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b.*$',
    r'^\s*[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b.*$',
    r'^\s*(read|glob|grep|ls|edit|write|multiedit)\b.*$',
    r'^\s*(error|warning)\s*:.*$',
    r'file not found:',
    r'no such file or directory',
    r'permission denied',
    r'invalid arguments',
    r'^\s*\{.*\"type\"\s*:\s*\"error\".*\}\s*$',
    r'現在.*(問題|不具合|障害).*(読み上げ|放送|案内).*(できません|できない)',
    r'現在.*(読み上げ|放送|案内).*(できません|できない)',
    r'検索(が|は)?できません',
    r'調査(が|は)?できません',
    r'情報(が|は)?取得できません',
    r'うまく読み上げできません',
    r'読み上げられません',
]
patterns = [
    (r'誰も(聞いて|見て)い(?:ない|ません)', 'みなさんに届くように'),
    (r'聞き手(?:が|は)?い(?:ない|ません)', '聞き手に届くように'),
    (r'リスナー(?:が|は)?い(?:ない|ません)', 'リスナーに届くように'),
    (r'視聴者(?:が|は)?い(?:ない|ません)', '視聴者に届くように'),
    (r'誰に向けてやってるのか', 'みなさんに向けて'),
    (r'過疎(?:配信|放送)?', 'この配信'),
    (r'無人(?:配信|放送)', '配信'),
    (r'誰もいない', 'みなさんがいる'),
    (r'マージ', '併合'),
    (r'合体', '併合'),
]
filtered_lines = []
for raw_line in text.splitlines():
    line = raw_line.strip()
    if line:
        low = line.lower()
        if any(re.search(pat, low, flags=re.IGNORECASE) for pat in drop_line_patterns):
            continue
    filtered_lines.append(raw_line)
out = "\n".join(filtered_lines)
for pat, repl in patterns:
    out = re.sub(pat, repl, out, flags=re.IGNORECASE)
out = re.sub(r'\n{3,}', '\n\n', out).strip()
sys.stdout.write(out)
PY
)"
}

_normalize_radio_tone() {
	python3 -c "
import re
import sys

text = sys.stdin.read()
out = text

rules = [
    (r'なんですよね(?=\\s|$|[。！？、])', 'なんです'),
    (r'なんですよ(?=\\s|$|[。！？、])', 'なんです'),
    (r'ですよね(?=\\s|$|[。！？、])', 'です'),
    (r'ですよ(?=\\s|$|[。！？、])', 'です'),
    (r'ますよね(?=\\s|$|[。！？、])', 'ます'),
    (r'ますね(?=\\s|$|[。！？、])', 'ます'),
    (r'ですね(?=\\s|$|[。！？、])', 'です'),
    (r'ですけどね(?=\\s|$|[。！？、])', 'ですけど'),
    (r'ますけどね(?=\\s|$|[。！？、])', 'ますけど'),
    (r'なんですけどね(?=\\s|$|[。！？、])', 'なんですけど'),
    (r'でしょうね(?=\\s|$|[。！？、])', 'でしょう'),
]
for pat, repl in rules:
    out = re.sub(pat, repl, out)
sys.stdout.write(out)
		"
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
		rakugo) announce="深夜の落語創作コーナーです。" ;;
		*)        announce="" ;;
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
	intro_line="${greet}、${_rc_period}の放送です。現在時刻は${_rc_time_spoken}です。"

	printf '%s\n%s' "$intro_line" "$text"
}

_radio_generate_and_play() {
	local prompt_file="$1" game_num="$2" score="$3" corner_name="$4"
	shift 4
	local no_preempt=true
	local selected_news=""
	local topic=""
	while [ $# -gt 0 ]; do
		case "$1" in
		--no-preempt) no_preempt=true ;;
		--selected-news) shift; selected_news="$1" ;;
		--topic) shift; topic="$1" ;;
		esac
		shift
	done

	# 同一 game_num + corner の二重生成/二重再生を防止
	local done_marker="$TMP_MARKERS_DIR/.radio_done_${game_num}_${corner_name}"
	if [ -f "$done_marker" ]; then
		log "[RADIO:${corner_name}] duplicate skip: already done for game=${game_num}"
		_write_radio_corner_status "duplicate_done" "$corner_name" "$game_num" "$score" "$topic" "already_done" "$selected_news"
		return 0
	fi
	local inflight_dir="$TMP_MARKERS_DIR/.radio_inflight_${game_num}_${corner_name}"
	if ! mkdir "$inflight_dir" 2>/dev/null; then
		log "[RADIO:${corner_name}] duplicate skip: in-flight for game=${game_num}"
		_write_radio_corner_status "duplicate_inflight" "$corner_name" "$game_num" "$score" "$topic" "already_inflight" "$selected_news"
		return 0
	fi

	_radio_set_state "generating" "$corner_name"
	_write_radio_corner_status "generating" "$corner_name" "$game_num" "$score" "$topic" "" "$selected_news"
	log "[RADIO:${corner_name}] トーク生成中..."
	local talk prompt_snapshot debug_dump=""
	local host_mode_generated=""
	host_mode_generated=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	prompt_snapshot=$(cat "$prompt_file" 2>/dev/null)
	talk=$(_run_opencode_radio "$RADIO_AGENT" "$prompt_file")
	if [ -z "$talk" ]; then
		talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$prompt_file")
	fi
	if [ -z "$talk" ]; then
		talk=$(_run_claude_radio "$prompt_file")
	fi
	rm -f "$prompt_file"

	if [ -z "$talk" ]; then
		debug_dump="$TMP_DEBUG_DIR/radio_failed_${corner_name}_$(date +%s).txt"
		{
			echo "reason=generation_empty"
			echo "corner=${corner_name}"
			echo "game=${game_num}"
			echo "score=${score}"
			echo "selected_news=${selected_news}"
			echo
			echo "===PROMPT==="
			printf '%s\n' "$prompt_snapshot"
		} >"$debug_dump"
		log "[RADIO:${corner_name}] トーク生成失敗: empty output (dump: $debug_dump)"
		_write_radio_corner_status "generation_failed" "$corner_name" "$game_num" "$score" "$topic" "generation_empty" "$selected_news"
		_radio_clear_state "$corner_name" "generation_failed"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	local talk_body talk_summary parse_dir
	parse_dir=$(mktemp -d /tmp/eloop_radio_parse_XXXXXXXX)
	printf '%s' "$talk" | _radio_parse_output_to_files "$parse_dir/body.txt" "$parse_dir/summary.txt" "$parse_dir/selected_news.txt"
	talk_body=$(cat "$parse_dir/body.txt" 2>/dev/null)
	talk_summary=$(cat "$parse_dir/summary.txt" 2>/dev/null)
	rm -rf "$parse_dir"
	[ -z "$talk_summary" ] && talk_summary="(要約なし)"

	if [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
		local news_source attribution
		news_source=$(_extract_news_source_name "$selected_news")
		if [ -n "$news_source" ]; then
			attribution="出典は${news_source}です。"
			talk_body=$(printf '%s\n' "$talk_body" | awk -v attribution="$attribution" 'NR==1 { print; print attribution; next } { print }')
		fi
	fi

	local talk_body_parsed talk_body_sanitized talk_body_dedup
	talk_body_parsed="$talk_body"
	if _contains_provider_error_text "$talk" || _contains_provider_error_text "$talk_body_parsed"; then
		debug_dump="$TMP_DEBUG_DIR/radio_failed_${corner_name}_$(date +%s).txt"
		{
			echo "reason=provider_error_text"
			echo "corner=${corner_name}"
			echo "game=${game_num}"
			echo "score=${score}"
			echo "selected_news=${selected_news}"
			echo
			echo "===RAW==="
			printf '%s\n' "$talk"
			echo
			echo "===PARSED==="
			printf '%s\n' "$talk_body_parsed"
		} >"$debug_dump"
		log "[RADIO:${corner_name}] provider error text detected in generated talk -> skip (dump: $debug_dump)"
		_write_radio_corner_status "generation_failed" "$corner_name" "$game_num" "$score" "$topic" "provider_error_text" "$selected_news"
		_radio_clear_state "$corner_name" "generation_failed"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi
	talk_body_sanitized=$(printf '%s' "$talk_body_parsed" | _sanitize_onair_text)
	talk_body_dedup=$(printf '%s' "$talk_body_sanitized" | _radio_dedup_text)

	# dedup が過剰に効いて短文化した場合は、まず非dedup本文に戻す
	if [ ${#talk_body_dedup} -lt 100 ] && [ ${#talk_body_sanitized} -ge 100 ]; then
		log "[RADIO:${corner_name}] dedup短縮が過剰 (${#talk_body_sanitized} -> ${#talk_body_dedup}字) → 非dedup本文を採用"
		talk_body="$talk_body_sanitized"
	else
		talk_body="$talk_body_dedup"
	fi

	# パーサ結果が短い場合は、生の出力から本文を再抽出して救済
	if [ ${#talk_body} -lt 100 ]; then
		local fallback_body
		fallback_body=$(printf '%s\n' "$talk" | sed '/^===SUMMARY===/,$d' | sed '/^===SELECTED_NEWS===/,$d')
		fallback_body=$(printf '%s' "$fallback_body" | _sanitize_onair_text)
		if [ ${#fallback_body} -ge 100 ]; then
			log "[RADIO:${corner_name}] 本文再抽出フォールバック採用 (${#fallback_body}字)"
			talk_body="$fallback_body"
		fi
	fi

	# 挨拶・時刻言及が抜けた出力を補完（ニュースはタイトル行を先頭維持）
	local talk_with_intro
	talk_with_intro=$(_ensure_radio_intro "$talk_body" "$corner_name")
	[ -n "$talk_with_intro" ] && talk_body="$talk_with_intro"
	talk_body=$(printf '%s' "$talk_body" | _normalize_radio_tone)

	if [ ${#talk_body} -lt 100 ]; then
		debug_dump="$TMP_DEBUG_DIR/radio_short_${corner_name}_$(date +%s).txt"
		{
			echo "reason=body_too_short"
			echo "corner=${corner_name}"
			echo "game=${game_num}"
			echo "score=${score}"
			echo "raw_chars=${#talk}"
			echo "parsed_chars=${#talk_body_parsed}"
			echo "sanitized_chars=${#talk_body_sanitized}"
			echo "dedup_chars=${#talk_body_dedup}"
			echo "final_chars=${#talk_body}"
			echo
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
		log "[RADIO:${corner_name}] WARNING: 本文が短すぎる raw=${#talk} parsed=${#talk_body_parsed} sanitized=${#talk_body_sanitized} dedup=${#talk_body_dedup} final=${#talk_body} -> skip (dump: $debug_dump)"
		_write_radio_corner_status "body_too_short" "$corner_name" "$game_num" "$score" "$topic" "body_too_short" "$selected_news"
		_radio_clear_state "$corner_name" "body_too_short"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	if _radio_should_fact_check "$corner_name"; then
		local fact_checked_body
		_radio_set_state "verifying" "$corner_name"
		_write_radio_corner_status "verifying" "$corner_name" "$game_num" "$score" "$topic" "" "$selected_news"
		fact_checked_body=$(_radio_fact_check_body "$corner_name" "$prompt_snapshot" "$talk_body" "$selected_news") || {
			debug_dump="$TMP_DEBUG_DIR/radio_factcheck_input_${corner_name}_$(date +%s).txt"
			{
				echo "reason=fact_check_failed"
				echo "corner=${corner_name}"
				echo "game=${game_num}"
				echo "score=${score}"
				echo "selected_news=${selected_news}"
				echo "body_chars=${#talk_body}"
				echo
				echo "===PROMPT==="
				printf '%s\n' "$prompt_snapshot"
				echo
				echo "===BODY==="
				printf '%s\n' "$talk_body"
			} >"$debug_dump"
			log "[RADIO:${corner_name}] fact-check失敗 (dump: $debug_dump)"
			_write_radio_corner_status "fact_check_failed" "$corner_name" "$game_num" "$score" "$topic" "fact_check_failed" "$selected_news"
			_radio_clear_state "$corner_name" "fact_check_failed"
			rmdir "$inflight_dir" 2>/dev/null || true
			return 1
		}
		talk_body="$fact_checked_body"
		if ! _is_valid_radio_talk "$talk_body"; then
			debug_dump="$TMP_DEBUG_DIR/radio_factcheck_invalid_${corner_name}_$(date +%s).txt"
			{
				echo "reason=fact_checked_body_invalid"
				echo "corner=${corner_name}"
				echo "game=${game_num}"
				echo "score=${score}"
				echo "body_chars=${#talk_body}"
				echo
				printf '%s\n' "$talk_body"
			} >"$debug_dump"
			log "[RADIO:${corner_name}] fact-check後の本文が不正/短文 -> 中止 (dump: $debug_dump)"
			_write_radio_corner_status "fact_checked_body_invalid" "$corner_name" "$game_num" "$score" "$topic" "fact_checked_body_invalid" "$selected_news"
			_radio_clear_state "$corner_name" "fact_checked_body_invalid"
			rmdir "$inflight_dir" 2>/dev/null || true
			return 1
		fi
	fi

	# コーナーアナウンス差し込み（fact-check後に強制挿入）
	talk_body=$(_ensure_corner_announce "$talk_body" "$corner_name")

	# say待ちは say_enqueue.sh 内で行われるため、ここでは不要

	local talk_file
	local comment_queued=0 comment_playing=0 comment_total=0
	local deferred_file=""
	local history_line=""
	local play_rc=0
	talk_file=$(mktemp /tmp/eloop_radio_talk_XXXXXXXX)
	echo "$talk_body" >"$talk_file"
	history_line="[$(date '+%H:%M')] Game#${game_num} ${score}pts [${corner_name}]: ${talk_summary}"
	log "[RADIO:${corner_name}] ${#talk_body}字"

	local host_mode_now=""
	host_mode_now=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	if [ "$host_mode_now" != "$host_mode_generated" ]; then
		log "[RADIO:${corner_name}] mode changed during generation (${host_mode_generated} -> ${host_mode_now}) -> discard"
		_write_radio_corner_status "stale_mode_discarded" "$corner_name" "$game_num" "$score" "$topic" "mode_changed" "$selected_news" "{\"expected_mode\": \"${host_mode_generated}\", \"current_mode\": \"${host_mode_now}\"}"
		rm -f "$talk_file"
		_radio_clear_state "$corner_name" "stale_mode_discarded"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 0
	fi

	# コメント未消化がある間は再生を deferred キューへ積み、生成は止めない
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	if [ "$comment_total" -gt 0 ]; then
		deferred_file=$(_enqueue_deferred_radio_talk "$talk_file" "$game_num" "$corner_name" "$host_mode_generated" "$history_line" || true)
		# deferred再生時のCC投稿用にニュースタイトルを保存
		if [ -n "$deferred_file" ] && [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
			echo "$selected_news" > "${deferred_file%.txt}.news_title"
			local deferred_cc_text=""
			deferred_cc_text=$(_build_cc_attribution_text "$selected_news")
			[ -n "$deferred_cc_text" ] && printf '%s' "$deferred_cc_text" > "${deferred_file%.txt}.cc_text"
		fi
		if [ -n "$deferred_file" ]; then
			_radio_set_state "queued" "$corner_name"
			_write_radio_corner_status "queued" "$corner_name" "$game_num" "$score" "$topic" "comment_backlog" "$selected_news" "{\"comment_queued\": ${comment_queued:-0}, \"comment_playing\": ${comment_playing:-0}, \"deferred_file\": \"$(basename "$deferred_file")\"}"
			log "[RADIO:${corner_name}] deferred: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}) -> $(basename "$deferred_file")"
		else
			log "[RADIO:${corner_name}] deferred enqueue失敗 (comment backlog=${comment_total})"
			_write_radio_corner_status "deferred_enqueue_failed" "$corner_name" "$game_num" "$score" "$topic" "deferred_enqueue_failed" "$selected_news"
			_radio_clear_state "$corner_name" "deferred_enqueue_failed"
			rm -f "$talk_file" 2>/dev/null || true
			rmdir "$inflight_dir" 2>/dev/null || true
			return 1
		fi
		else
			_radio_set_state "playing" "$corner_name"
			_write_radio_corner_status "playing" "$corner_name" "$game_num" "$score" "$topic" "" "$selected_news"
			# CC表記は say_enqueue.sh の再生開始時に投稿（SAY_CC_TEXT 経由）
			local immediate_cc_text=""
			if [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
				immediate_cc_text=$(_build_cc_attribution_text "$selected_news")
			fi
			local radio_vo_speaker=""
			radio_vo_speaker=$(_radio_voicevox_speaker_override 2>/dev/null || true)
			_refresh_radio_intro_for_playback_file "$talk_file" "$corner_name"
			if [ "$no_preempt" = true ]; then
				SAY_CC_TEXT="$immediate_cc_text" SAY_VOICEVOX_SPEAKER_OVERRIDE="$radio_vo_speaker" SAY_CONTEXT_LABEL="radio:${corner_name}" ./say_enqueue.sh --no-preempt "$talk_file" "$RADIO_SAY_RATE" 0 || play_rc=$?
			else
				SAY_CC_TEXT="$immediate_cc_text" SAY_VOICEVOX_SPEAKER_OVERRIDE="$radio_vo_speaker" SAY_CONTEXT_LABEL="radio:${corner_name}" ./say_enqueue.sh "$talk_file" "$RADIO_SAY_RATE" 0 || play_rc=$?
			fi
			if [ "$play_rc" -ne 0 ]; then
				debug_dump="$TMP_DEBUG_DIR/radio_play_failed_${corner_name}_$(date +%s).txt"
				{
					echo "reason=play_failed"
					echo "corner=${corner_name}"
					echo "game=${game_num}"
					echo "score=${score}"
					echo "play_rc=${play_rc}"
					echo
					printf '%s\n' "$talk_body"
				} >"$debug_dump"
				log "[RADIO:${corner_name}] 再生失敗 rc=${play_rc} (dump: $debug_dump)"
				_write_radio_corner_status "play_failed" "$corner_name" "$game_num" "$score" "$topic" "play_failed" "$selected_news" "{\"play_rc\": ${play_rc:-1}}"
				rm -f "$talk_file"
				_radio_clear_state "$corner_name" "play_failed"
				rmdir "$inflight_dir" 2>/dev/null || true
				return 1
			fi
			_radio_append_spoken_history_line "$history_line"
		fi
	rm -f "$talk_file"
	_radio_mark_done "$done_marker"
	_radio_clear_state "$corner_name" "completed"
	_write_radio_corner_status "completed" "$corner_name" "$game_num" "$score" "$topic" "" "$selected_news"
	rmdir "$inflight_dir" 2>/dev/null || true
	if [ -n "$deferred_file" ]; then
		log "[RADIO:${corner_name}] トーク終了 (再生待ちキュー)"
	else
		log "[RADIO:${corner_name}] トーク終了"
	fi
}
