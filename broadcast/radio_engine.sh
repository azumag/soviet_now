# broadcast/radio_engine.sh - AI実行ラッパー, パース, サニタイズ, 生成&再生

#=== opencode run を疑似TTY付きで実行 ===

_run_opencode_radio_unqueued() {
	local agent="$1" prompt_file="$2"
	local raw_file cleaned
	raw_file=$(mktemp /tmp/eloop_radio_raw_XXXXXXXX)
	mkdir -p "$(_opencode_xdg_state_home)/opencode/locks" 2>/dev/null || true
	mkdir -p "$(_opencode_xdg_data_home)/opencode" 2>/dev/null || true
	_opencode_sync_auth_to_xdg
	# opencode 1.3.x 以降は非 TTY でも動くため script(1) pty ラッパは廃止
	XDG_STATE_HOME="$(_opencode_xdg_state_home)" XDG_DATA_HOME="$(_opencode_xdg_data_home)" OPENCODE_PERMISSION="$RADIO_OPENCODE_PERMISSION" LC_ALL=en_US.UTF-8 \
		timeout "${RADIO_OPENCODE_TIMEOUT}" \
		opencode run --agent "$agent" "$(cat "$prompt_file")" \
		>"$raw_file" 2>&1
	local rc=$?
	if [ $rc -eq 124 ]; then
		log "[RADIO] opencode timeout (${RADIO_OPENCODE_TIMEOUT}s, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	if [ $rc -ne 0 ] && [ ! -s "$raw_file" ]; then
		log "[RADIO] opencode failed (rc=$rc, no output, agent=$agent)" >&2
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
		grep -v '^[[:space:]]*⚙' |
		grep -v '^[[:space:]]*{[[:space:]]*"query"' |
		sed -E 's#</?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>##g' |
		sed '/^[[:space:]]*$/d')
	rm -f "$raw_file"
	if _contains_provider_error_text "$cleaned"; then
		log "[RADIO] opencode provider error treated as failure (agent=$agent)" >&2
		return 1
	fi
	printf '%s' "$cleaned"
}

_run_opencode_radio() {
	local agent="$1"
	_ai_generation_queue_run "RADIO:opencode:${agent}" _run_opencode_radio_unqueued "$@"
}

_run_opencode_comment_unqueued() {
	local agent="$1" prompt_file="$2"
	local raw_file sandbox_dir sandbox_prompt timeout_sec
	timeout_sec="${COMMENT_OPENCODE_TIMEOUT:-$RADIO_OPENCODE_TIMEOUT}"
	raw_file=$(mktemp /tmp/eloop_comment_raw_XXXXXXXX)
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
		"status_dashboard.py")
	if [ -z "$sandbox_dir" ] || [ ! -d "$sandbox_dir" ]; then
		log "[COMMENT] sandbox作成失敗 -> direct opencode" >&2
		rm -f "$raw_file"
		_run_opencode_radio "$agent" "$prompt_file"
		return
	fi
	sandbox_prompt="$sandbox_dir/tmp/comment_prompt.txt"
	mkdir -p "$(dirname "$sandbox_prompt")"
	cp "$prompt_file" "$sandbox_prompt" 2>/dev/null || {
		destroy_sandbox "$sandbox_dir"
		rm -f "$raw_file"
		return 1
	}
	# opencode 1.3.x 以降は非 TTY でも動くため script(1) pty ラッパは廃止
	mkdir -p "$(_opencode_xdg_state_home)/opencode/locks" 2>/dev/null || true
	mkdir -p "$(_opencode_xdg_data_home)/opencode" 2>/dev/null || true
	_opencode_sync_auth_to_xdg
	(
		cd "$sandbox_dir" || exit 1
		XDG_STATE_HOME="$(_opencode_xdg_state_home)" XDG_DATA_HOME="$(_opencode_xdg_data_home)" OPENCODE_PERMISSION="$COMMENT_OPENCODE_PERMISSION" LC_ALL=en_US.UTF-8 \
			timeout "$timeout_sec" \
			opencode run --agent "$agent" "$(cat tmp/comment_prompt.txt)"
	) >"$raw_file" 2>&1
	local rc=$?
	destroy_sandbox "$sandbox_dir"
	if [ $rc -eq 124 ]; then
		log "[COMMENT] opencode timeout (${timeout_sec}s, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	if [ $rc -ne 0 ] && [ ! -s "$raw_file" ]; then
		log "[COMMENT] opencode failed (rc=$rc, no output, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	cat "$raw_file" |
		_strip_ansi |
		grep -v '^>' |
		grep -v '^\^D' |
		grep -v '^Script started on ' |
		grep -v '^Script done on ' |
		grep -v '^/[^ ]*$' |
		grep -v '^[[:space:]]*/Users/' |
		sed -E 's#</?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>##g' |
		sed '/^[[:space:]]*$/d'
	rm -f "$raw_file"
}

_run_opencode_comment() {
	_ai_generation_queue_run "COMMENT:opencode:${1:-unknown}" _run_opencode_comment_unqueued "$@"
}

_run_claude_comment_with_model_unqueued() {
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
		"status_dashboard.py")
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

_run_claude_comment_with_model() {
	_ai_generation_queue_run "COMMENT:claude:${2:-$RADIO_CLAUDE_MODEL}" _run_claude_comment_with_model_unqueued "$@"
}

_run_claude_comment() {
	_run_claude_comment_with_model "$1" "$RADIO_CLAUDE_MODEL"
}

_run_minimax_comment_unqueued() {
	local prompt_file="$1"
	local model="${2:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	local timeout_sec="${3:-${COMMENT_CLAUDE_TIMEOUT:-180}}"
	local sandbox_dir sandbox_prompt output_file output
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
		"status_dashboard.py")
	if [ -z "$sandbox_dir" ] || [ ! -d "$sandbox_dir" ]; then
		log "[COMMENT] sandbox作成失敗 -> direct minimax" >&2
		sandbox_prompt="$prompt_file"
	else
		sandbox_prompt="$sandbox_dir/tmp/comment_prompt.txt"
		mkdir -p "$(dirname "$sandbox_prompt")"
		cp "$prompt_file" "$sandbox_prompt" 2>/dev/null || {
			destroy_sandbox "$sandbox_dir"
			return 1
		}
	fi
	output_file=$(mktemp /tmp/eloop_minimax_comment_output_XXXXXXXX)
	_run_minimax_claude_prompt_file "$sandbox_prompt" "$output_file" "$model" "$timeout_sec" "acceptEdits"
	local rc=$?
	local stderr_preview="${MINIMAX_CLAUDE_LAST_STDERR:-}"
	local provider_error="${MINIMAX_CLAUDE_LAST_PROVIDER_ERROR:-false}"
	local login_error="${MINIMAX_CLAUDE_LAST_LOGIN_ERROR:-false}"
	local stdout_preview="${MINIMAX_CLAUDE_LAST_STDOUT_PREVIEW:-}"
	if [ -n "$stderr_preview" ] || [ "$provider_error" = "true" ]; then
		mkdir -p "$(dirname "$COMMENT_CLAUDE_LOG_FILE")" 2>/dev/null || true
		{
			printf '[%s] rc=%s model=%s provider=minimax tools=%s\n' "$(date '+%F %T')" "$rc" "$model" "$COMMENT_CLAUDE_TOOLS"
			if [ -n "$stderr_preview" ]; then
				printf '[stderr]\n%s\n' "$stderr_preview"
			fi
			if [ "$provider_error" = "true" ]; then
				printf '[stdout]\n'
				printf '%s' "$stdout_preview"
				printf '\n'
			fi
			printf '\n\n'
		} >>"$COMMENT_CLAUDE_LOG_FILE" 2>/dev/null || true
		[ -n "$stderr_preview" ] && log "[COMMENT] minimax stderr: $(printf '%s' "$stderr_preview" | head -c 500)" >&2
	fi
	[ "$login_error" = "true" ] && log "[COMMENT] minimax unavailable: not logged in" >&2
	[ -n "$sandbox_dir" ] && destroy_sandbox "$sandbox_dir"
	if [ $rc -eq 124 ]; then
		rm -f "$output_file"
		log "[COMMENT] minimax timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		rm -f "$output_file"
		log "[COMMENT] minimax provider/auth error treated as failure (model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		rm -f "$output_file"
		log "[COMMENT] minimax failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	output=$(cat "$output_file" 2>/dev/null)
	rm -f "$output_file"
	printf '%s' "$output"
}

_run_minimax_comment() {
	_ai_generation_queue_run "COMMENT:minimax:${2:-${MINIMAX_MODEL:-MiniMax-M2.7}}" _run_minimax_comment_unqueued "$@"
}

_run_comment_agent() {
	local agent="$1" prompt_file="$2"
	if [[ "$agent" == opencode:* || "$agent" != *:* && "$agent" != ollama && "$agent" != qwen35e && "$agent" != gemma4e && "$agent" != haiku && "$agent" != claude && "$agent" != minimax && "$agent" != ccmm && "$agent" != qwencode ]]; then
		local _comment_opencode_lock=""
		_comment_opencode_lock=$(_opencode_run_lock_dir 2>/dev/null || printf '%s' "tmp/state/.opencode_run_lock")
		if [ -d "$_comment_opencode_lock" ]; then
			log "[COMMENT] opencode busy -> skip agent=${agent} for low-latency comment fallback" >&2
			return 1
		fi
	fi
	_ai_dispatch "COMMENT" "$agent" "$prompt_file"
}

_run_claude_radio_with_model_unqueued() {
	local prompt_file="$1"
	local model="${2:-$RADIO_CLAUDE_MODEL}"
	local prompt output timeout_sec
	timeout_sec="${RADIO_CLAUDE_TIMEOUT:-120}"
	if [ ! -s "$prompt_file" ]; then
		return 1
	fi
	# command substitution に混ざらないよう stderr に出す
	log "[RADIO] claude call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
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

_run_claude_radio_with_model() {
	_ai_generation_queue_run "RADIO:claude:${2:-$RADIO_CLAUDE_MODEL}" _run_claude_radio_with_model_unqueued "$@"
}

_run_claude_radio() {
	_ai_call_claude "RADIO" "$1" "$RADIO_CLAUDE_MODEL"
}

_run_minimax_radio_unqueued() {
	local prompt_file="$1"
	local model="${2:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	local timeout_sec="${3:-${RADIO_CLAUDE_TIMEOUT:-120}}"
	local output_file output
	if [ ! -s "$prompt_file" ]; then
		return 1
	fi
	log "[RADIO] minimax call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	output_file=$(mktemp /tmp/eloop_minimax_radio_output_XXXXXXXX)
	_run_minimax_claude_prompt_file "$prompt_file" "$output_file" "$model" "$timeout_sec" "acceptEdits"
	local rc=$?
	local stderr_preview="${MINIMAX_CLAUDE_LAST_STDERR:-}"
	local provider_error="${MINIMAX_CLAUDE_LAST_PROVIDER_ERROR:-false}"
	local login_error="${MINIMAX_CLAUDE_LAST_LOGIN_ERROR:-false}"
	if [ -n "$stderr_preview" ]; then
		log "[RADIO] minimax stderr: $stderr_preview" >&2
	fi
	[ "$login_error" = "true" ] && log "[RADIO] minimax unavailable: not logged in" >&2
	if [ $rc -eq 124 ]; then
		rm -f "$output_file"
		log "[RADIO] minimax timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		rm -f "$output_file"
		log "[RADIO] minimax provider/auth error treated as failure (model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		rm -f "$output_file"
		log "[RADIO] minimax failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	output=$(cat "$output_file" 2>/dev/null)
	rm -f "$output_file"
	printf '%s' "$output"
}

_run_minimax_radio() {
	_ai_generation_queue_run "RADIO:minimax:${2:-${MINIMAX_MODEL:-MiniMax-M2.7}}" _run_minimax_radio_unqueued "$@"
}

_run_radio_agent() {
	local agent="$1" prompt_file="$2"
	_ai_dispatch "RADIO" "$agent" "$prompt_file"
}

# _radio_stage_research <corner_name> <topic> <raw_grounding_data>
#   Webグラウンディングデータを軽量AIで2000字以内のブリーフィングに凝縮する。
#   RADIO_BRIEFING_ENABLED=0(default) のときはパススルー(truncate only)。
#   stdout: condensed briefing text
_radio_stage_research() {
	local corner_name="$1" topic="$2" raw_data="$3"

	# データなし → 空を返す
	[ -z "$raw_data" ] && return 0

	local max_chars="${RADIO_BRIEFING_MAX_CHARS:-2000}"

	# データが十分小さい場合はAI凝縮不要
	if [ "${#raw_data}" -le "$max_chars" ]; then
		printf '%s' "$raw_data"
		return 0
	fi

	# RADIO_BRIEFING_ENABLED=0 のときはそのままtruncate
	if [ "${RADIO_BRIEFING_ENABLED:-0}" != "1" ]; then
		printf '%s' "${raw_data:0:$max_chars}"
		return 0
	fi

	# 軽量AIで凝縮
	local briefing_model="${RADIO_BRIEFING_MODEL:-${RADIO_CLAUDE_MODEL:-claude-haiku-4-5-20251001}}"
	local briefing_prompt_file briefing
	briefing_prompt_file=$(mktemp /tmp/eloop_radio_briefing_XXXXXXXX)
	cat >"$briefing_prompt_file" <<BPROMPT
以下はウェブ検索結果です。このデータから「${topic}」について放送に使える重要な事実・ポイントを日本語で${max_chars}字以内にまとめてください。

ルール:
- 事実のみ抽出（広告・SEOスパム・重複を除去）
- 固有名詞・数値・日付を正確に保持
- 放送DJ向けの簡潔な要約として書く
- 「まとめ：」などの前置きなしで、要約内容をそのまま書き出すこと
- 確認できた範囲で書き、不明な点は断定しないこと

【検索結果】
${raw_data}
BPROMPT

	log "[RADIO:${corner_name}] Stage1: グラウンディング凝縮 (${#raw_data}字 → ${max_chars}字以内, model=${briefing_model})" >&2
	briefing=$(_run_claude_radio_with_model "$briefing_prompt_file" "$briefing_model" 2>/dev/null)
	rm -f "$briefing_prompt_file"

	if [ -n "$briefing" ] && [ "${#briefing}" -ge 50 ]; then
		log "[RADIO:${corner_name}] Stage1: 凝縮完了 (${#briefing}字)" >&2
		printf '%s' "$briefing"
	else
		# 凝縮失敗 → truncateにフォールバック
		log "[RADIO:${corner_name}] Stage1: 凝縮失敗 → truncateフォールバック" >&2
		printf '%s' "${raw_data:0:$max_chars}"
	fi
}

_run_ollama_radio_unqueued() {
	local prompt_file="$1"
	local model="${2:-$RADIO_OLLAMA_MODEL}"
	local timeout_sec="${3:-$RADIO_OLLAMA_TIMEOUT}"
	local prompt output
	if [ ! -s "$prompt_file" ]; then
		return 1
	fi
	log "[RADIO] ollama call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	prompt=$(cat "$prompt_file")
	local stderr_file
	stderr_file=$(mktemp /tmp/eloop_ollama_stderr_XXXXXXXX)
	output=$(
		ANTHROPIC_AUTH_TOKEN="ollama" \
			ANTHROPIC_BASE_URL="$OLLAMA_BASE_URL" \
			ANTHROPIC_API_KEY="" \
			timeout "$timeout_sec" claude -p "$prompt" --model="$model" --permission-mode=acceptEdits 2>"$stderr_file"
	)
	local rc=$?
	local stderr_preview=""
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 500 "$stderr_file")
		log "[RADIO] ollama stderr: $stderr_preview" >&2
	fi
	rm -f "$stderr_file"
	if [ $rc -eq 124 ]; then
		log "[RADIO] ollama timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[RADIO] ollama failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	if _contains_provider_error_text "$output"; then
		log "[RADIO] ollama provider error treated as failure (model=$model)" >&2
		return 1
	fi
	printf '%s' "$output"
}

_run_ollama_radio() {
	_ai_generation_queue_run "RADIO:ollama:${2:-$RADIO_OLLAMA_MODEL}" _run_ollama_radio_unqueued "$@"
}

_run_ollama_comment_unqueued() {
	local prompt_file="$1"
	local model="${2:-$COMMENT_OLLAMA_MODEL}"
	local timeout_sec="${3:-$COMMENT_OLLAMA_TIMEOUT}"
	local prompt output
	if [ ! -s "$prompt_file" ]; then
		return 1
	fi
	prompt=$(cat "$prompt_file")
	log "[COMMENT] ollama call (model=$model, prompt=$(printf '%s' "$prompt" | wc -c | tr -d ' ')B)" >&2
	local stderr_file
	stderr_file=$(mktemp /tmp/eloop_ollama_comment_stderr_XXXXXXXX)
	output=$(
		ANTHROPIC_AUTH_TOKEN="ollama" \
			ANTHROPIC_BASE_URL="$OLLAMA_BASE_URL" \
			ANTHROPIC_API_KEY="" \
			timeout "$timeout_sec" claude -p "$prompt" --model="$model" --permission-mode=acceptEdits 2>"$stderr_file"
	)
	local rc=$?
	if [ -s "$stderr_file" ]; then
		log "[COMMENT] ollama stderr: $(head -c 500 "$stderr_file")" >&2
	fi
	rm -f "$stderr_file"
	if [ $rc -eq 124 ]; then
		log "[COMMENT] ollama timeout (${timeout_sec}s, model=$model)" >&2
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[COMMENT] ollama failed (rc=$rc, model=$model)" >&2
		return 1
	fi
	if _contains_provider_error_text "$output"; then
		log "[COMMENT] ollama provider error treated as failure (model=$model)" >&2
		return 1
	fi
	printf '%s' "$output"
}

_run_ollama_comment() {
	_ai_generation_queue_run "COMMENT:ollama:${2:-$COMMENT_OLLAMA_MODEL}" _run_ollama_comment_unqueued "$@"
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
	printf '%s\n' "$1" | python3 -c "$(
		cat <<'PY'
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
	if printf '%s' "$talk" | grep -Eiq '^[[:space:]]*[✗✕×✱→►▸]'; then
		return 1
	fi
	# 「検索できない」「データがない」系の拒否応答を検出 → 無効にしてfallbackさせる
	if printf '%s' "$talk" | grep -Eq '(リアルタイム|最新).*(データ|情報).*(持って|ありません|ございません|取得できません|アクセスできません|提供できません|確認できません)|検索(機能|ツール).*(ありません|ございません|持って|できません)|インターネット.*(アクセス|接続).*(できません|ありません)|データフィード.*(ありません|ございません)|外部.*(アクセス|接続).*(できません|ありません)|正直に申し上げ|申し訳ありませんが'; then
		return 1
	fi
	# ツール使用・汎用対話メタ応答の検出 (ollama モデルが返す場合がある)
	if printf '%s' "$talk" | grep -Eiq 'I can use the .* tool|WebFetch tool|Before I can proceed|grant permission|Would you like me to proceed'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eq '具体的な(質問|指示|情報)を|何について知りたい|どのようなご用件|遠慮なくお話し|今日のテーマは何|具体的に何について|準備はできています|お話を聞く準備'; then
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
	# ツール使用・汎用対話メタ応答の検出 (ollama モデルが返す場合がある)
	if printf '%s' "$talk" | grep -Eiq 'I can use the .* tool|WebFetch tool|Before I can proceed|grant permission|Would you like me to proceed'; then
		return 1
	fi
	if printf '%s' "$talk" | grep -Eq '具体的な(質問|指示|情報)を|何について知りたい|どのようなご用件|遠慮なくお話し|今日のテーマは何|具体的に何について|準備はできています|お話を聞く準備|まずは具体的な情報を調べてから'; then
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
		compact=$(
			cat <<EOF
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
		compact=$(
			cat <<EOF
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
		compact=$(
			cat <<EOF
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
	weather | fortune | market | dinner | deals | survival)
		compact=$(
			cat <<EOF
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
		compact=$(
			cat <<EOF
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

def normalize_compare_text(text):
    return re.sub(r"[\W_]+", "", text)

def looks_like_keyword_list(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 220:
        return False
    if re.search(r"[。.!?！？]", stripped):
        return False
    return stripped.count(",") + stripped.count("、") >= 3

summary_like_header = re.compile(
    r"^(?:要約|まとめ|総括|要点|キーワード|一言要約|今回のまとめ|本日のまとめ)(?:\s*[:：]\s*.*)?$"
)
summary_like_short_lead = re.compile(
    r"^(?:要するに|一言で言うと|ひとことで言うと|まとめると|結論だけ言うと|要点だけ言うと)"
)
summary_parts_normalized = [
    normalize_compare_text(part) for part in summary.split(" / ") if normalize_compare_text(part)
]

def matches_summary_part(line):
    norm = normalize_compare_text(line)
    if len(norm) < 6:
        return False
    for part in summary_parts_normalized:
        if len(part) < 6:
            continue
        if norm == part or norm in part or part in norm:
            return True
    return False

def is_summaryish_edge_line(line):
    stripped = line.strip(" \t\u3000-・*")
    if not stripped:
        return False
    if summary_like_header.match(stripped):
        return True
    if looks_like_keyword_list(stripped):
        return True
    if len(stripped) <= 60 and summary_like_short_lead.match(stripped):
        return True
    if len(stripped) <= 120 and matches_summary_part(stripped):
        return True
    return False

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
    if is_summaryish_edge_line(head):
        clean_body_lines = clean_body_lines[1:]
        continue
    break
if clean_body_lines:
    head = clean_body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        clean_body_lines = clean_body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(clean_body_lines) >= 2:
        clean_body_lines = clean_body_lines[1:]
while clean_body_lines and is_summaryish_edge_line(clean_body_lines[-1]):
    clean_body_lines = clean_body_lines[:-1]
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
	python3 -c "$(
		cat <<'PY'
import re
import sys

text = sys.stdin.read()
drop_line_patterns = [
    r'^\s*https?://\S*\s*$',
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
    r'^\s*⚙\s*\w',
    r'^\s*\{\s*"query"\s*:',
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
    (r'https?://\S+', ''),
]
def _is_chinese_line(s):
    """ひらがな/カタカナが無くCJK漢字が多い行は中国語と判定"""
    cjk = len(re.findall(r'[\u4e00-\u9fff]', s))
    kana = len(re.findall(r'[\u3040-\u30ff]', s))
    if cjk >= 4 and kana == 0:
        return True
    if cjk >= 8 and kana <= 1:
        return True
    return False

filtered_lines = []
for raw_line in text.splitlines():
    line = raw_line.strip()
    if line:
        low = line.lower()
        if any(re.search(pat, low, flags=re.IGNORECASE) for pat in drop_line_patterns):
            continue
        if _is_chinese_line(line):
            continue
    filtered_lines.append(raw_line)
out = "\n".join(filtered_lines)
for pat, repl in patterns:
    out = re.sub(pat, repl, out, flags=re.IGNORECASE)
out = re.sub(r'[#＃]', '', out)
# 句点区切りの文レベルで中国語除去（行内に中国語文が混ざるケース）
def _remove_chinese_sentences(t):
    parts = re.split(r'(。)', t)
    result = []
    for i in range(0, len(parts) - 1, 2):
        sent = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ''
        if _is_chinese_line(sent):
            continue
        result.append(sent + sep)
    if len(parts) % 2 == 1 and parts[-1].strip():
        if not _is_chinese_line(parts[-1]):
            result.append(parts[-1])
    return ''.join(result)
out = _remove_chinese_sentences(out)
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
	soviet) announce="ソ連共産主義ネタコーナーです。" ;;
	news) announce="本日のニュースです。" ;;
	weather) announce="ソ連天気予報コーナーです。" ;;
	fortune) announce="今日のソ連占いコーナーです。" ;;
	market) announce="本日の株価・経済動向コーナーです。" ;;
	dinner) announce="今日の夕飯の献立を考えようコーナーです。" ;;
	deals) announce="お得情報コーナーです。" ;;
	survival) announce="明日を生き延びるサバイバル知識コーナーです。" ;;
	jiji) announce="時事ニュースコーナーです。" ;;
	rollback) announce="粛清ラジオです。" ;;
	rakugo) announce="深夜の落語創作コーナーです。" ;;
	finance) announce="金融の仕組みコーナーです。" ;;
	music_knowledge) announce="音楽知識コーナーです。" ;;
	ai_knowledge) announce="AI知識・最新AIツール紹介コーナーです。" ;;
	*) announce="" ;;
	esac
	[ -z "$announce" ] && {
		printf '%s' "$text"
		return 0
	}
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
	intro_line="${greet}、現在時刻は${_rc_time_spoken}です。"

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
		--selected-news)
			shift
			selected_news="$1"
			;;
		--topic)
			shift
			topic="$1"
			;;
		esac
		shift
	done

	# 同一 game_num + corner の二重生成/二重再生を防止
	local marker_game_num=""
	case "$game_num" in
	'' | *[!0-9]* | 0) marker_game_num="" ;;
	*) marker_game_num="$game_num" ;;
	esac

	local done_marker=""
	if [ -n "$marker_game_num" ]; then
		done_marker="$TMP_MARKERS_DIR/.radio_done_${marker_game_num}_${corner_name}"
		if [ -f "$done_marker" ]; then
			log "[RADIO:${corner_name}] duplicate skip: already done for game=${marker_game_num}"
			_write_radio_corner_status "duplicate_done" "$corner_name" "$game_num" "$score" "$topic" "already_done" "$selected_news"
			return 0
		fi
	fi

	local inflight_dir=""
	if [ -n "$marker_game_num" ]; then
		inflight_dir="$TMP_MARKERS_DIR/.radio_inflight_${marker_game_num}_${corner_name}"
	else
		inflight_dir="$TMP_MARKERS_DIR/.radio_inflight_nogame_${corner_name}"
	fi
	if ! mkdir "$inflight_dir" 2>/dev/null; then
		if [ -n "$marker_game_num" ]; then
			log "[RADIO:${corner_name}] duplicate skip: in-flight for game=${marker_game_num}"
		else
			log "[RADIO:${corner_name}] duplicate skip: in-flight without game_num"
		fi
		_write_radio_corner_status "duplicate_inflight" "$corner_name" "$game_num" "$score" "$topic" "already_inflight" "$selected_news"
		return 0
	fi

	_radio_set_state "generating" "$corner_name"
	_write_radio_corner_status "generating" "$corner_name" "$game_num" "$score" "$topic" "" "$selected_news"
	log "[RADIO:${corner_name}] 生成キュー投入..."
	local talk="" prompt_snapshot debug_dump="" provider_used=""
	local host_mode_generated=""
	local radio_primary_agent="" radio_second_agent="" radio_third_agent=""
	local radio_prepass_agent=""
	local radio_allow_claude_fallback=true
	host_mode_generated=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	if [ "$host_mode_generated" = "soren91" ]; then
		radio_primary_agent="${RADIO_SOREN91_AGENT:-haiku}"
		radio_second_agent="${RADIO_SOREN91_FALLBACK:-gemma4e}"
		radio_third_agent=""
		radio_allow_claude_fallback=false
	else
		radio_primary_agent="${RADIO_MAIN_AGENT:-opencode:qwen35pgo}"
		radio_prepass_agent="${RADIO_MAIN_PREPASS_AGENT:-opencode:minimax}"
		radio_second_agent="${RADIO_MAIN_FALLBACK:-gemma4e}"
		radio_third_agent="${RADIO_MAIN_OLLAMA_FALLBACK:-opencode:glmflash}"
	fi
	prompt_snapshot=$(cat "$prompt_file" 2>/dev/null)

	# --- attempt loop: 生成+パース+ノーマライズ を品質チェックのリライト分だけ繰り返す ---
	# プロンプトファイルはループ内で削除。リライト用に保存コピーを保持する。
	local _saved_prompt _current_prompt_file _radio_attempt _radio_max_attempts
	local _quality_ok _quality_fail_reason
	_saved_prompt=$(mktemp /tmp/eloop_radio_saved_XXXXXXXX)
	cp "$prompt_file" "$_saved_prompt" 2>/dev/null || true
	_current_prompt_file="$prompt_file"
	_radio_max_attempts=$((${RADIO_MAX_RETRIES:-1} + 1))
	_quality_ok=false
	_quality_fail_reason=""
	local talk_body="" talk_summary=""

	if [ -n "$radio_prepass_agent" ] && [ "$radio_prepass_agent" != "$radio_primary_agent" ]; then
		local _prepass_prompt_file _prepass_output _prepass_enhanced_prompt
		_prepass_prompt_file=$(mktemp /tmp/eloop_radio_prepass_prompt_XXXXXXXX)
		cat >"$_prepass_prompt_file" <<PREPASS
以下はラジオ本文生成用の元プロンプトです。
あなたの役割は、本文を書くことではなく、WebFetchを必要に応じて使って、次の本文生成者が使うための事前調査メモだけを作ることです。

ルール:
- 読み上げ本文は書かない
- 事実、日付、固有名詞、数値、注意すべき未確認点だけを日本語で箇条書きにする
- WebFetchが失敗した場合、その失敗文を出力しない。手元で確認できた範囲だけを書く
- 1200字以内

【元プロンプト】
${prompt_snapshot}
PREPASS
		log "[RADIO:${corner_name}] prepass: ${radio_prepass_agent} -> ${radio_primary_agent}"
		_prepass_output=$(_run_radio_agent "$radio_prepass_agent" "$_prepass_prompt_file" 2>/dev/null || true)
		rm -f "$_prepass_prompt_file" 2>/dev/null || true
		if [ -n "$_prepass_output" ] && ! _contains_provider_error_text "$_prepass_output"; then
			_prepass_enhanced_prompt=$(mktemp /tmp/eloop_radio_prepass_enhanced_XXXXXXXX)
			cat "$_saved_prompt" >"$_prepass_enhanced_prompt" 2>/dev/null || true
			cat >>"$_prepass_enhanced_prompt" <<PREPASS_APPEND

---
【事前調査メモ】
以下は前段の調査AIがWebFetchを含めて確認した材料です。本文生成ではこの材料を優先し、追加のWebFetchは本当に必要な場合だけ使ってください。WebFetchの失敗文やツールログは本文に絶対に含めないでください。

${_prepass_output}
PREPASS_APPEND
			cp "$_prepass_enhanced_prompt" "$_saved_prompt" 2>/dev/null || true
			_current_prompt_file="$_prepass_enhanced_prompt"
			log "[RADIO:${corner_name}] prepass OK (${#_prepass_output}字)"
		else
			log "[RADIO:${corner_name}] prepass empty/failed -> direct ${radio_primary_agent}"
		fi
	fi

	for _radio_attempt in $(seq 1 "$_radio_max_attempts"); do

		talk=""
		provider_used=""
		talk=$(_run_radio_agent "$radio_primary_agent" "$_current_prompt_file")
		[ -n "$talk" ] && provider_used="$radio_primary_agent" && log "[RADIO:${corner_name}] ${radio_primary_agent} OK"
		if [ -z "$talk" ]; then
			log "[RADIO:${corner_name}] ${radio_primary_agent} fail -> ${radio_second_agent}"
			talk=$(_run_radio_agent "$radio_second_agent" "$_current_prompt_file")
			[ -n "$talk" ] && provider_used="$radio_second_agent" && log "[RADIO:${corner_name}] ${radio_second_agent} OK"
		fi
		local _radio_fallback_source="$radio_second_agent"
		if [ -z "$talk" ] && [ "$radio_allow_claude_fallback" = "true" ]; then
			log "[RADIO:${corner_name}] ${radio_second_agent} fail -> claude:${RADIO_CLAUDE_MODEL}"
			talk=$(_run_claude_radio "$_current_prompt_file")
			[ -n "$talk" ] && provider_used="claude:${RADIO_CLAUDE_MODEL}" && log "[RADIO:${corner_name}] claude:${RADIO_CLAUDE_MODEL} OK"
			_radio_fallback_source="claude:${RADIO_CLAUDE_MODEL}"
		fi
		if [ -z "$talk" ] && [ -n "$radio_third_agent" ]; then
			log "[RADIO:${corner_name}] ${_radio_fallback_source} fail -> ${radio_third_agent}"
			talk=$(_run_radio_agent "$radio_third_agent" "$_current_prompt_file")
			[ -n "$talk" ] && provider_used="$radio_third_agent" && log "[RADIO:${corner_name}] ${radio_third_agent} OK"
		fi
		# 使い終わったプロンプトを削除（保存コピーは除く）
		[ "$_current_prompt_file" != "$_saved_prompt" ] && rm -f "$_current_prompt_file" 2>/dev/null || true

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
			rm -f "$_saved_prompt" 2>/dev/null || true
			_write_radio_corner_status "generation_failed" "$corner_name" "$game_num" "$score" "$topic" "generation_empty" "$selected_news"
			_radio_clear_state "$corner_name" "generation_failed"
			rmdir "$inflight_dir" 2>/dev/null || true
			return 1
		fi
		log "[RADIO:${corner_name}] 生成プロバイダ: ${provider_used:-unknown} (attempt=${_radio_attempt})"

		local talk_body_parsed talk_body_sanitized talk_body_dedup parse_dir
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
			rm -f "$_saved_prompt" 2>/dev/null || true
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
			rm -f "$_saved_prompt" 2>/dev/null || true
			_write_radio_corner_status "body_too_short" "$corner_name" "$game_num" "$score" "$topic" "body_too_short" "$selected_news"
			_radio_clear_state "$corner_name" "body_too_short"
			rmdir "$inflight_dir" 2>/dev/null || true
			return 1
		fi

		# 品質チェック（中国語/非日本語/無限ループ/文字化け）
		if [ "${RADIO_QUALITY_CHECK_ENABLED:-1}" = "1" ]; then
			local _qr
			_qr=$(_radio_quality_check "$talk_body" "$corner_name")
			if [ "$_qr" = "OK" ]; then
				_quality_ok=true
				break
			else
				_quality_fail_reason="$_qr"
				log "[RADIO:${corner_name}] 品質チェック失敗 attempt=${_radio_attempt}/${_radio_max_attempts}: ${_qr}"
				if [ "$_radio_attempt" -lt "$_radio_max_attempts" ]; then
					_current_prompt_file=$(_radio_build_rewrite_prompt "$_saved_prompt" "${talk_body:0:200}" "$_qr")
					continue
				fi
			fi
		else
			_quality_ok=true
			break
		fi

	done # attempt loop end

	rm -f "$_saved_prompt" 2>/dev/null || true

	# 品質チェック最終失敗
	if [ "${RADIO_QUALITY_CHECK_ENABLED:-1}" = "1" ] && [ "$_quality_ok" != "true" ]; then
		debug_dump="$TMP_DEBUG_DIR/radio_quality_${corner_name}_$(date +%s).txt"
		{
			echo "reason=quality_check_failed"
			echo "corner=${corner_name}"
			echo "game=${game_num}"
			echo "score=${score}"
			echo "quality_fail_reason=${_quality_fail_reason}"
			echo "body_chars=${#talk_body}"
			echo
			printf '%s\n' "$talk_body"
		} >"$debug_dump"
		log "[RADIO:${corner_name}] 品質チェック最終失敗 (${_quality_fail_reason}) → スキップ (dump: $debug_dump)"
		_write_radio_corner_status "quality_check_failed" "$corner_name" "$game_num" "$score" "$topic" "$_quality_fail_reason" "$selected_news"
		_radio_clear_state "$corner_name" "quality_check_failed"
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

	# fact-check/コーナーアナウンス後に再度トーン正規化（「ございます」等の再混入防止）
	talk_body=$(printf '%s' "$talk_body" | _normalize_radio_tone)

	# say待ちは say_enqueue.sh 内で行われるため、ここでは不要

	local talk_file
	local comment_queued=0 comment_playing=0 comment_total=0
	local deferred_file=""
	local history_line=""
	local play_rc=0
	talk_file=$(mktemp /tmp/eloop_radio_talk_XXXXXXXX)
	echo "$talk_body" >"$talk_file"
	_radio_store_generation_meta \
		"$talk_file" \
		"$corner_name" \
		"$host_mode_generated" \
		"${provider_used:-unknown}" \
		"$game_num" \
		"$score" \
		"$_radio_attempt" \
		"$topic" \
		"$selected_news" \
		"$radio_primary_agent" \
		"$radio_second_agent" \
		"$radio_third_agent"
	history_line="[$(date '+%H:%M')] Game#${game_num} ${score}pts [${corner_name}]: ${talk_summary}"
	log "[RADIO:${corner_name}] ${#talk_body}字"

	local host_mode_now=""
	host_mode_now=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	if [ "$host_mode_now" != "$host_mode_generated" ]; then
		log "[RADIO:${corner_name}] mode changed during generation (${host_mode_generated} -> ${host_mode_now}) -> discard"
		_write_radio_corner_status "stale_mode_discarded" "$corner_name" "$game_num" "$score" "$topic" "mode_changed" "$selected_news" "{\"expected_mode\": \"${host_mode_generated}\", \"current_mode\": \"${host_mode_now}\"}"
		_radio_clear_generation_meta "$talk_file" 2>/dev/null || true
		rm -f "$talk_file"
		_radio_clear_state "$corner_name" "stale_mode_discarded"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 0
	fi

	# 再生は常に deferred キューへ積み、audio_worker に委譲する
	#（radio_worker は文章生成のみを担当し、say_enqueue は audio_worker が実行する）
	deferred_file=$(_enqueue_deferred_radio_talk "$talk_file" "$game_num" "$corner_name" "$host_mode_generated" "$history_line" || true)
	# deferred再生時のCC投稿用にニュースタイトルを保存
	if [ -n "$deferred_file" ] && [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
		echo "$selected_news" >"${deferred_file%.txt}.news_title"
		local deferred_cc_text=""
		deferred_cc_text=$(_build_cc_attribution_text "$selected_news")
		[ -n "$deferred_cc_text" ] && printf '%s' "$deferred_cc_text" >"${deferred_file%.txt}.cc_text"
	fi
	if [ -n "$deferred_file" ]; then
		_radio_set_state "queued" "$corner_name"
		_write_radio_corner_status "queued" "$corner_name" "$game_num" "$score" "$topic" "deferred" "$selected_news" "{\"deferred_file\": \"$(basename "$deferred_file")\"}"
		log "[RADIO:${corner_name}] deferred queue投入: $(basename "$deferred_file")"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 0
	else
		log "[RADIO:${corner_name}] deferred enqueue失敗"
		_write_radio_corner_status "deferred_enqueue_failed" "$corner_name" "$game_num" "$score" "$topic" "deferred_enqueue_failed" "$selected_news"
		_radio_clear_state "$corner_name" "deferred_enqueue_failed"
		_radio_clear_generation_meta "$talk_file" 2>/dev/null || true
		rm -f "$talk_file" 2>/dev/null || true
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi
}
