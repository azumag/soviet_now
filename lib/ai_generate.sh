# lib/ai_generate.sh - AI生成の共通ディスパッチ
#
# コメント生成とラジオ生成の AI 呼び出しを統一するバックエンド。
# モデル選択・フォールバック・タイムアウト・エラー検出を一箇所で管理する。
#
# 使い方:
#   ai_generate "RADIO" "$prompt_file" "$primary_agent" "$fallback_agent"
#   output は stdout に返る。呼び出し元がファイルに書くかキューに積むか判断する。

_ai_guard_model_output() {
	local guard_root="${ELOOP_LIB_DIR:-.}"
	# C4 (common_parts_chat_c4.md C-S2): 出力ガードの正典は docich 側。
	# DOCICH_BIN が利用可能なら docich ai-guard へ委譲し、無い環境 (CI・探索モード等)
	# では従来どおりローカルの model_output_guard.py を使う (互換維持・fail-open)。
	local _docich_bin="${DOCICH_BIN:-}"
	if [ -z "$_docich_bin" ]; then
		_docich_bin="$(command -v docich 2>/dev/null || true)"
	fi
	if [ -n "$_docich_bin" ] && [ -x "$_docich_bin" ]; then
		"$_docich_bin" ai-guard
		return $?
	fi
	python3 "$guard_root/lib/model_output_guard.py"
}

# ai_generate_list がモデルをバックオフするのは、プロバイダが明示的に
# レート制限を返した場合だけにする。形式不正・空出力・認証/CLI失敗は
# 次の候補へフォールバックするが、モデル自体は止めない。
AI_RATE_LIMIT_RC=79

_ai_rate_limit_text_detected() {
	printf '%s' "${1:-}" | grep -Eiq \
		'(^|[^[:alnum:]])429([^[:alnum:]]|$)|too[[:space:]_-]*many[[:space:]_-]*requests|rate[[:space:]_-]*limit([[:space:]_-]*(ed|exceeded))?|quota[[:space:]_-]*(exceeded|exhausted|limit)|resource[[:space:]_-]*exhausted|usage[[:space:]_-]*limit'
}

# === 統一バックエンド ===

AI_GENERATION_QUEUE_LAST_TOKEN=""

_ai_lock_sanitize_key() {
	local raw="${1:-default}"
	printf '%s' "$raw" |
		tr '[:upper:]' '[:lower:]' |
		sed -E 's/[^a-z0-9._-]+/_/g; s/^_+//; s/_+$//'
}

_ai_model_is_local() {
	local model="$1" item norm_model norm_item
	norm_model=$(_ai_lock_sanitize_key "$model")
	for item in ${LOCAL_LLM_AGENTS:-qwen35e qwen3.5:9b gemma4nt ccogent gemma4nt_ccogent}; do
		norm_item=$(_ai_lock_sanitize_key "$item")
		[ -n "$norm_item" ] || continue
		[ "$norm_model" = "$norm_item" ] && return 0
	done
	return 1
}

_ai_queue_lock_scope() {
	local label="${1:-AI}" rest scope
	case "$label" in
	*:local:*) printf '%s' "local"; return 0 ;;
	*:remote:*)
		rest="${label#*:remote:}"
		scope="remote:$rest"
		printf '%s' "$(_ai_lock_sanitize_key "$scope")"
		return 0
		;;
	esac
	printf '%s' "$(_ai_lock_sanitize_key "$label")"
}

_ai_generation_queue_lock_dir() {
	local label="${1:-AI}" base scope
	if [ -n "${AI_GENERATION_QUEUE_LOCK_DIR:-}" ]; then
		printf '%s\n' "$AI_GENERATION_QUEUE_LOCK_DIR"
		return 0
	fi
	if [ -n "${ELOOP_LIB_DIR:-}" ]; then
		base="$ELOOP_LIB_DIR/tmp/state/.ai_generation_locks"
	else
		base="tmp/state/.ai_generation_locks"
	fi
	scope=$(_ai_queue_lock_scope "$label")
	printf '%s/%s\n' "$base" "${scope:-default}"
}

_ai_queue_label() {
	local label="$1" provider="$2" model="$3" locality="remote"
	if _ai_model_is_local "$model"; then
		locality="local"
	fi
	printf '%s:%s:%s:%s' "$label" "$locality" "$provider" "$model"
}

_ai_generation_queue_enter() {
	local label="${1:-AI}"
	local lock_dir
	local wait_sec="${AI_GENERATION_QUEUE_WAIT_SEC:-2}"
	local stale_sec="${AI_GENERATION_QUEUE_STALE_SEC:-900}"
	local waited=0 token now mt age owner_summary=""
	lock_dir=$(_ai_generation_queue_lock_dir "$label")

	case "$wait_sec" in
	'' | *[!0-9]*) wait_sec=2 ;;
	esac
	[ "$wait_sec" -lt 1 ] && wait_sec=1
	case "$stale_sec" in
	'' | *[!0-9]*) stale_sec=900 ;;
	esac
	[ "$stale_sec" -lt 60 ] && stale_sec=60

	mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true
	token="${BASHPID:-$$}:$RANDOM:$(date +%s)"

	while ! mkdir "$lock_dir" 2>/dev/null; do
		now=$(date +%s)
		mt=$(stat -f %m "$lock_dir" 2>/dev/null) \
			|| mt=$(stat -c %Y "$lock_dir" 2>/dev/null) \
			|| mt="$now"
		age=$((now - mt))
		if [ "$age" -gt "$stale_sec" ]; then
			log "[AIQ:${label}] stale generation lock cleared (age=${age}s)" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [ "$waited" -eq 0 ] || [ $((waited % 30)) -eq 0 ]; then
			owner_summary=$(tr '\n' ' ' <"$lock_dir/owner" 2>/dev/null | sed 's/[[:space:]]\+/ /g')
			log "[AIQ:${label}] queued: waiting for generation slot${owner_summary:+ (${owner_summary})}" >&2
		fi
		sleep "$wait_sec"
		waited=$((waited + wait_sec))
	done

	{
		printf 'token=%s\n' "$token"
		printf 'pid=%s\n' "${BASHPID:-$$}"
		printf 'label=%s\n' "$label"
		printf 'started_at=%s\n' "$(date '+%F %T')"
	} >"$lock_dir/owner" 2>/dev/null || true
	AI_GENERATION_QUEUE_LAST_TOKEN="$token"
	[ "$waited" -gt 0 ] && log "[AIQ:${label}] generation slot acquired after ${waited}s" >&2
	return 0
}

_ai_generation_queue_leave() {
	local token="${1:-}" label="${2:-AI}"
	local lock_dir
	local current_token=""
	[ -n "$token" ] || return 0
	lock_dir=$(_ai_generation_queue_lock_dir "$label")
	[ -d "$lock_dir" ] || return 0
	current_token=$(sed -n 's/^token=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
	if [ "$current_token" = "$token" ]; then
		rm -rf "$lock_dir" 2>/dev/null || true
	else
		log "[AIQ:${label}] generation lock owner changed; skip release" >&2
	fi
}

_ai_generation_queue_run() {
	local label="${1:-AI}"
	shift
	local token rc
	if [ "${AI_GENERATION_QUEUE_ENABLED:-1}" != "1" ]; then
		"$@"
		return $?
	fi
	case "$label" in
	RADIO* | NEWS* | JIJI* | CELEBRATION*) ;;
	*)
		"$@"
		return $?
		;;
	esac
	_ai_generation_queue_enter "$label" || return 1
	token="$AI_GENERATION_QUEUE_LAST_TOKEN"
	"$@"
	rc=$?
	_ai_generation_queue_leave "$token" "$label"
	return "$rc"
}

OPENCODE_RUN_LOCK_LAST_TOKEN=""

_opencode_run_lock_dir() {
	local agent="${1:-}" base scope
	if [ -n "${OPENCODE_RUN_LOCK_DIR:-}" ]; then
		printf '%s\n' "$OPENCODE_RUN_LOCK_DIR"
	elif [ -n "$agent" ]; then
		if [ -n "${ELOOP_LIB_DIR:-}" ]; then
			base="$ELOOP_LIB_DIR/tmp/state/.opencode_run_locks"
		else
			base="tmp/state/.opencode_run_locks"
		fi
		if _ai_model_is_local "$agent"; then
			scope="local"
		else
			scope="remote:opencode:$agent"
		fi
		printf '%s/%s\n' "$base" "$(_ai_lock_sanitize_key "$scope")"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/.opencode_run_lock\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/.opencode_run_lock\n'
	fi
}

_opencode_xdg_state_home() {
	if [ -n "${OPENCODE_XDG_STATE_HOME:-}" ]; then
		printf '%s\n' "$OPENCODE_XDG_STATE_HOME"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/xdg_state\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/xdg_state\n'
	fi
}

_opencode_xdg_data_home() {
	if [ -n "${OPENCODE_XDG_DATA_HOME:-}" ]; then
		printf '%s\n' "$OPENCODE_XDG_DATA_HOME"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/xdg_data\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/xdg_data\n'
	fi
}

_opencode_sync_auth_to_xdg() {
	local src="${OPENCODE_AUTH_SOURCE:-$HOME/.local/share/opencode/auth.json}"
	local dst_dir dst
	[ -s "$src" ] || return 0
	dst_dir="$(_opencode_xdg_data_home)/opencode"
	dst="$dst_dir/auth.json"
	mkdir -p "$dst_dir" 2>/dev/null || return 0
	if [ ! -s "$dst" ] || ! cmp -s "$src" "$dst" 2>/dev/null; then
		cp "$src" "$dst" 2>/dev/null || true
	fi
}

_opencode_cleanup_internal_locks() {
	local locks_dir stale_sec now lock mt age
	locks_dir="$(_opencode_xdg_state_home)/opencode/locks"
	stale_sec="${OPENCODE_INTERNAL_LOCK_STALE_SEC:-60}"
	case "$stale_sec" in
	'' | *[!0-9]*) stale_sec=60 ;;
	esac
	[ "$stale_sec" -lt 10 ] && stale_sec=10
	[ -d "$locks_dir" ] || return 0
	now=$(date +%s)
	while IFS= read -r lock; do
		[ -n "$lock" ] || continue
		mt=$(stat -f %m "$lock" 2>/dev/null) \
			|| mt=$(stat -c %Y "$lock" 2>/dev/null) \
			|| mt="$now"
		age=$((now - mt))
		if [ "$age" -gt "$stale_sec" ]; then
			rm -rf "$lock" 2>/dev/null || true
		fi
	done < <(find "$locks_dir" -mindepth 1 -maxdepth 1 -type d -name '*.lock' 2>/dev/null)
}

_opencode_run_lock_enter() {
	local label="${1:-opencode}"
	local agent="${2:-}"
	local lock_dir wait_sec stale_sec max_wait_sec waited=0 token now mt age owner_summary="" owner_pid="" postmortem_stale_sec
	if [ "${OPENCODE_RUN_LOCK_ENABLED:-1}" != "1" ]; then
		OPENCODE_RUN_LOCK_LAST_TOKEN=""
		return 0
	fi
	lock_dir=$(_opencode_run_lock_dir "$agent")
	wait_sec="${OPENCODE_RUN_LOCK_WAIT_SEC:-2}"
	stale_sec="${OPENCODE_RUN_LOCK_STALE_SEC:-1800}"
	max_wait_sec="${OPENCODE_RUN_LOCK_MAX_WAIT_SEC:-0}"
	case "$wait_sec" in
	'' | *[!0-9]*) wait_sec=2 ;;
	esac
	[ "$wait_sec" -lt 1 ] && wait_sec=1
	case "$stale_sec" in
	'' | *[!0-9]*) stale_sec=1800 ;;
	esac
	[ "$stale_sec" -lt 60 ] && stale_sec=60
	case "$max_wait_sec" in
	'' | *[!0-9]*) max_wait_sec=0 ;;
	esac

	mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true
	token="${BASHPID:-$$}:$RANDOM:$(date +%s)"

	while ! mkdir "$lock_dir" 2>/dev/null; do
		now=$(date +%s)
		mt=$(stat -f %m "$lock_dir" 2>/dev/null) \
			|| mt=$(stat -c %Y "$lock_dir" 2>/dev/null) \
			|| mt="$now"
		age=$((now - mt))
		owner_pid=$(sed -n 's/^pid=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
		owner_summary=$(tr '\n' ' ' <"$lock_dir/owner" 2>/dev/null | sed 's/[[:space:]]\+/ /g')
		postmortem_stale_sec="${ROLLBACK_POSTMORTEM_OPENCODE_LOCK_STALE_SEC:-240}"
		case "$postmortem_stale_sec" in
		'' | *[!0-9]*) postmortem_stale_sec=240 ;;
		esac
		[ "$postmortem_stale_sec" -lt 60 ] && postmortem_stale_sec=60
		if [ "$max_wait_sec" -gt 0 ] && [ "$postmortem_stale_sec" -ge "$max_wait_sec" ]; then
			postmortem_stale_sec=$((max_wait_sec - wait_sec))
			[ "$postmortem_stale_sec" -lt 60 ] && postmortem_stale_sec=60
		fi
		if [[ "$owner_summary" == *ROLLBACK-POSTMORTEM* ]] && [ "$age" -gt "$postmortem_stale_sec" ]; then
			log "[OPENCODE:${label}] stale rollback-postmortem run lock cleared (age=${age}s, ${owner_summary})" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
			log "[OPENCODE:${label}] stale run lock cleared (dead owner pid=${owner_pid}, age=${age}s)" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [ "$age" -gt "$stale_sec" ]; then
			log "[OPENCODE:${label}] stale run lock cleared (age=${age}s)" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [ "$waited" -eq 0 ] || [ $((waited % 30)) -eq 0 ]; then
			log "[OPENCODE:${label}] queued: waiting for opencode slot${owner_summary:+ (${owner_summary})}" >&2
		fi
		if [ "$max_wait_sec" -gt 0 ] && [ "$waited" -ge "$max_wait_sec" ]; then
			log "[OPENCODE:${label}] opencode slot wait exceeded ${max_wait_sec}s${owner_summary:+ (${owner_summary})}" >&2
			OPENCODE_RUN_LOCK_LAST_TOKEN=""
			return 124
		fi
		sleep "$wait_sec"
		waited=$((waited + wait_sec))
	done

	{
		printf 'token=%s\n' "$token"
		printf 'pid=%s\n' "${BASHPID:-$$}"
		printf 'label=%s\n' "$label"
		printf 'started_at=%s\n' "$(date '+%F %T')"
	} >"$lock_dir/owner" 2>/dev/null || true
	OPENCODE_RUN_LOCK_LAST_TOKEN="$token"
	[ "$waited" -gt 0 ] && log "[OPENCODE:${label}] opencode slot acquired after ${waited}s" >&2
	return 0
}

_opencode_run_lock_leave() {
	local token="${1:-}" label="${2:-opencode}" agent="${3:-}"
	local lock_dir current_token=""
	[ -n "$token" ] || return 0
	lock_dir=$(_opencode_run_lock_dir "$agent")
	[ -d "$lock_dir" ] || return 0
	current_token=$(sed -n 's/^token=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
	if [ "$current_token" = "$token" ]; then
		rm -rf "$lock_dir" 2>/dev/null || true
	else
		log "[OPENCODE:${label}] run lock owner changed; skip release" >&2
	fi
}

# _ai_call_claude LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_claude_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-$RADIO_CLAUDE_MODEL}"
	local timeout_sec="${4:-${RADIO_CLAUDE_TIMEOUT:-120}}"
	local output stderr_file stderr_preview provider_error=false login_error=false rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] claude call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	stderr_file=$(mktemp /tmp/ai_claude_stderr_XXXXXXXX)
	output=$(cat "$prompt_file" | timeout "$timeout_sec" claude -p --model "$model" 2>"$stderr_file")
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 500 "$stderr_file")
		log "[${label}] claude stderr: $stderr_preview" >&2
	fi
	if _contains_provider_error_text "$output" || { [ -n "${stderr_preview:-}" ] && _contains_provider_error_text "$stderr_preview"; }; then
		provider_error=true
	fi
	if { [ "$rc" -ne 0 ] || [ -n "${stderr_preview:-}" ]; } && [ -n "${stderr_preview:-}" ]; then
		if _ai_rate_limit_text_detected "$stderr_preview"; then
			rate_limited=true
		fi
	fi
	if _contains_claude_login_error_text "$output" || { [ -n "${stderr_preview:-}" ] && _contains_claude_login_error_text "$stderr_preview"; }; then
		login_error=true
	fi
	[ "$login_error" = "true" ] && log "[${label}] claude unavailable: not logged in" >&2
	rm -f "$stderr_file"
	if [ $rc -eq 124 ]; then
		log "[${label}] claude timeout (${timeout_sec}s, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		log "[${label}] claude provider/auth error (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] claude failed (rc=$rc, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$output"
}

# _ai_call_minimax LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_minimax_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	local timeout_sec="${4:-${RADIO_CLAUDE_TIMEOUT:-120}}"
	local output_file output raw_failure rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] minimax call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	output_file=$(mktemp /tmp/ai_minimax_output_XXXXXXXX)
	_run_minimax_claude_prompt_file "$prompt_file" "$output_file" "$model" "$timeout_sec" "acceptEdits"
	local rc=$?
	local stderr_preview="${MINIMAX_CLAUDE_LAST_STDERR:-}"
	local provider_error="${MINIMAX_CLAUDE_LAST_PROVIDER_ERROR:-false}"
	local login_error="${MINIMAX_CLAUDE_LAST_LOGIN_ERROR:-false}"
	[ -n "$stderr_preview" ] && log "[${label}] minimax stderr: $stderr_preview" >&2
	if [ $rc -ne 0 ] || [ -n "$stderr_preview" ]; then
		raw_failure="$stderr_preview"
		_ai_rate_limit_text_detected "$raw_failure" && rate_limited=true
	fi
	[ "$login_error" = "true" ] && log "[${label}] minimax unavailable: not logged in" >&2
	if [ $rc -eq 124 ]; then
		rm -f "$output_file"
		log "[${label}] minimax timeout (${timeout_sec}s, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		rm -f "$output_file"
		log "[${label}] minimax provider/auth error (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		rm -f "$output_file"
		log "[${label}] minimax failed (rc=$rc, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	output=$(cat "$output_file" 2>/dev/null)
	rm -f "$output_file"
	printf '%s' "$output"
}

# _ai_call_ollama LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_ollama_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-${RADIO_OLLAMA_MODEL:-qwen3.5:9b}}"
	local timeout_sec="${4:-${RADIO_OLLAMA_TIMEOUT:-180}}"
	local prompt output stderr_file stderr_preview rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] ollama call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	prompt=$(cat "$prompt_file")
	stderr_file=$(mktemp /tmp/ai_ollama_stderr_XXXXXXXX)
	output=$(
		ANTHROPIC_AUTH_TOKEN="ollama" \
			ANTHROPIC_BASE_URL="$OLLAMA_BASE_URL" \
			ANTHROPIC_API_KEY="" \
			timeout "$timeout_sec" claude -p "$prompt" --model="$model" --permission-mode=acceptEdits 2>"$stderr_file"
	)
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 500 "$stderr_file")
		log "[${label}] ollama stderr: $stderr_preview" >&2
	fi
	if [ $rc -ne 0 ] && [ -n "$stderr_preview" ]; then
		_ai_rate_limit_text_detected "$stderr_preview" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] ollama timeout (${timeout_sec}s, model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] ollama failed (rc=$rc, model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if _contains_provider_error_text "$output"; then
		log "[${label}] ollama provider error (model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	rm -f "$stderr_file"
	printf '%s' "$output"
}

# _ai_call_qwencode LABEL PROMPT_FILE [TIMEOUT]
_ai_call_qwencode_unqueued() {
	local label="$1" prompt_file="$2"
	local timeout_sec="${3:-${RADIO_QWENCODE_TIMEOUT:-120}}"
	local output rc stderr_file stderr_preview rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] qwencode call (prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	stderr_file=$(mktemp /tmp/ai_qwencode_stderr_XXXXXXXX)
	output=$(timeout "$timeout_sec" qwen -p "$(cat "$prompt_file")" -y 2>"$stderr_file")
	local rc=$?
	stderr_preview=$(head -c 500 "$stderr_file" 2>/dev/null || true)
	if [ $rc -ne 0 ] && [ -n "$stderr_preview" ]; then
		_ai_rate_limit_text_detected "$stderr_preview" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] qwencode timeout (${timeout_sec}s)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] qwencode failed (rc=$rc)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ -z "$output" ]; then
		log "[${label}] qwencode empty output" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	rm -f "$stderr_file"
	printf '%s' "$output"
}


# _ai_call_opencode LABEL AGENT PROMPT_FILE [TIMEOUT] [PERMISSION]
_ai_call_opencode_unqueued() {
	local label="$1" agent="$2" prompt_file="$3"
	local timeout_sec="${4:-${RADIO_OPENCODE_TIMEOUT:-180}}"
	local permission="${5:-${RADIO_OPENCODE_PERMISSION:-}}"
	local raw_file stderr_file raw_text cleaned lock_token="" lock_rc rate_limited=false
	local old_lock_wait="${OPENCODE_RUN_LOCK_WAIT_SEC-}"
	local old_lock_max_wait="${OPENCODE_RUN_LOCK_MAX_WAIT_SEC-}"
	local had_lock_wait=0
	local had_lock_max_wait=0
	[ "${OPENCODE_RUN_LOCK_WAIT_SEC+x}" = "x" ] && had_lock_wait=1
	[ "${OPENCODE_RUN_LOCK_MAX_WAIT_SEC+x}" = "x" ] && had_lock_max_wait=1

	[ -s "$prompt_file" ] || return 1
	if [[ "$label" == COMMENT* ]] && [ -z "${OPENCODE_RUN_LOCK_MAX_WAIT_SEC:-}" ]; then
		OPENCODE_RUN_LOCK_WAIT_SEC="${COMMENT_OPENCODE_LOCK_POLL_SEC:-1}"
		OPENCODE_RUN_LOCK_MAX_WAIT_SEC="${COMMENT_OPENCODE_LOCK_MAX_WAIT_SEC:-4}"
	fi
	_opencode_run_lock_enter "${label}:opencode:${agent}" "$agent"
	lock_rc=$?
	if [ "$had_lock_wait" -eq 1 ]; then
		OPENCODE_RUN_LOCK_WAIT_SEC="$old_lock_wait"
	else
		unset OPENCODE_RUN_LOCK_WAIT_SEC
	fi
	if [ "$had_lock_max_wait" -eq 1 ]; then
		OPENCODE_RUN_LOCK_MAX_WAIT_SEC="$old_lock_max_wait"
	else
		unset OPENCODE_RUN_LOCK_MAX_WAIT_SEC
	fi
	if [ "$lock_rc" -ne 0 ]; then
		log "[${label}] opencode slot unavailable quickly; fallback preferred for live comment (agent=$agent)" >&2
		return 1
	fi
	lock_token="$OPENCODE_RUN_LOCK_LAST_TOKEN"
	mkdir -p "$(_opencode_xdg_state_home)/opencode/locks" 2>/dev/null || true
	mkdir -p "$(_opencode_xdg_data_home)/opencode" 2>/dev/null || true
	_opencode_sync_auth_to_xdg
	_opencode_cleanup_internal_locks
	raw_file=$(mktemp /tmp/ai_opencode_raw_XXXXXXXX)
	stderr_file=$(mktemp /tmp/ai_opencode_stderr_XXXXXXXX)
	# opencode 1.3.x 以降は非 TTY でも動くため、旧 script(1) pty ラッパは廃止
	XDG_STATE_HOME="$(_opencode_xdg_state_home)" XDG_DATA_HOME="$(_opencode_xdg_data_home)" OPENCODE_PERMISSION="$permission" LC_ALL=en_US.UTF-8 \
		timeout --kill-after=10s "$timeout_sec" opencode run --agent "$agent" "$(cat "$prompt_file")" \
		>"$raw_file" 2>"$stderr_file"
	local rc=$?
	_opencode_run_lock_leave "$lock_token" "${label}:opencode:${agent}" "$agent"
	if [ $rc -ne 0 ] && [ -s "$stderr_file" ]; then
		_ai_rate_limit_text_detected "$(head -c 4000 "$stderr_file" 2>/dev/null)" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] opencode timeout (${timeout_sec}s, agent=$agent)" >&2
		rm -f "$raw_file"
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ "$rate_limited" = "true" ]; then
		log "[${label}] opencode explicit rate limit (agent=$agent)" >&2
		rm -f "$raw_file" "$stderr_file"
		return "$AI_RATE_LIMIT_RC"
	fi
	# script コマンドの rc は子プロセスの終了コードと一致しないことがある。
	# raw_file に出力があれば成功とみなし、rc != 0 でも続行する。
	if [ $rc -ne 0 ] && [ ! -s "$raw_file" ]; then
		log "[${label}] opencode failed (rc=$rc, no output, agent=$agent)" >&2
		rm -f "$raw_file"
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	raw_text=$(cat "$raw_file")
	_notify_webfetch_failure "$label" "$agent" "$raw_text" "dispatch" || true
	cleaned=$(printf '%s' "$raw_text" | _ai_guard_model_output)
	rm -f "$raw_file"
	rm -f "$stderr_file"
	if _contains_provider_error_text "$cleaned"; then
		log "[${label}] opencode provider error (agent=$agent)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$cleaned"
}

_ai_call_claude() {
	local label="${1:-AI}" model="${3:-$RADIO_CLAUDE_MODEL}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "claude" "$model")" _ai_call_claude_unqueued "$@"
}

_ai_call_minimax() {
	local label="${1:-AI}" model="${3:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "minimax" "$model")" _ai_call_minimax_unqueued "$@"
}

_ai_call_ollama() {
	local label="${1:-AI}" model="${3:-${RADIO_OLLAMA_MODEL:-qwen3.5:9b}}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "ollama" "$model")" _ai_call_ollama_unqueued "$@"
}

_ai_call_qwencode() {
	local label="${1:-AI}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "qwencode" "qwencode")" _ai_call_qwencode_unqueued "$@"
}

_ai_call_opencode() {
	local label="${1:-AI}" agent="${2:-opencode}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "opencode" "$agent")" _ai_call_opencode_unqueued "$@"
}

# === Codex (統一ハーネス) ===

# codex:<model> は指定モデルを使い、codex または従来形式の agent 名は
# CODEX_MODEL にフォールバックする。従来の agent 名を壊さず、明示した
# codex モデルだけを確実に -m へ渡す。
_ai_codex_model_from_agent() {
	local agent="${1:-}" model=""
	case "$agent" in
	codex:*) model="${agent#codex:}" ;;
	*) model="${CODEX_MODEL:-deepseek-v4-flash}" ;;
	esac
	[ -n "$model" ] || model="${CODEX_MODEL:-deepseek-v4-flash}"
	printf '%s' "$model"
}

# MiniMax 系が最終出力へ含めることがある非公開推論ブロックを、本文ごと除去する。
# タグだけを消すと推論本文が読み上げ対象に残るため、複数行を一括処理する。
_ai_strip_reasoning_blocks() {
	python3 -c '
import re
import sys

text = sys.stdin.read()
for tag in ("think", "analysis"):
    text = re.sub(
        rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        rf"<{tag}\b[^>]*>.*\Z",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
text = re.sub(
    r"</?(?:final|assistant_response)\b[^>]*>",
    "",
    text,
    flags=re.IGNORECASE,
)
sys.stdout.write(text.strip())
'
}

# _ai_call_codex LABEL AGENT PROMPT_FILE [TIMEOUT]
#   codex CLI 経由で agent が示すモデルを呼ぶ。
_ai_call_codex_unqueued() {
	local label="$1" agent="$2" prompt_file="$3"
	local timeout_sec="${4:-${CODEX_TIMEOUT:-300}}"
	local model
	model=$(_ai_codex_model_from_agent "$agent")
	local codex_bin="${CODEX_BIN:-codex}"
	local out_file stderr_file stderr_preview raw_failure rc cleaned rate_limited=false
	[ -s "$prompt_file" ] || return 1
	out_file=$(mktemp /tmp/ai_codex_out_XXXXXXXX)
	stderr_file=$(mktemp /tmp/ai_codex_stderr_XXXXXXXX)
	case "$timeout_sec" in
	'' | *[!0-9]*) timeout_sec=300 ;;
	esac
	[ "$timeout_sec" -lt 1 ] && timeout_sec=1
	log "[${label}] codex call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	timeout --kill-after=10s "$timeout_sec" "$codex_bin" exec \
		--skip-git-repo-check -m "$model" -o "$out_file" "$(cat "$prompt_file")" \
		>/dev/null 2>"$stderr_file"
	rc=$?
	stderr_preview=$(head -c 4000 "$stderr_file" 2>/dev/null || true)
	raw_failure="$stderr_preview"
	# -o の本文はモデル出力であり、通常の失敗時に「rate limit」という
	# 語を含むだけでもバックオフを発火させてしまうため判定対象にしない。
	# 明示的なプロバイダ診断はCLIのstderr（rc非0/出力欠落時）だけを見る。
	if [ $rc -ne 0 ] || [ ! -s "$out_file" ]; then
		_ai_rate_limit_text_detected "$raw_failure" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] codex timeout (${timeout_sec}s, model=$model)" >&2
		rm -f "$out_file"
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] codex failed (rc=$rc, model=$model)" >&2
		rm -f "$out_file"
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	cleaned=$(_ai_strip_reasoning_blocks <"$out_file")
	rm -f "$out_file"
	rm -f "$stderr_file"
	if [ -z "$cleaned" ]; then
		log "[${label}] codex empty after cleanup (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if _contains_provider_error_text "$cleaned"; then
		log "[${label}] codex provider error (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$cleaned"
}

_ai_call_codex() {
	local label="${1:-AI}" agent="${2:-codex}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "codex" "$agent")" _ai_call_codex_unqueued "$@"
}

# === 統一ディスパッチャ ===

# _ai_dispatch LABEL AGENT PROMPT_FILE [TIMEOUT]
#   agent 識別子に基づいて適切なバックエンドを呼ぶ。
#   stdout: 生成テキスト
_ai_dispatch() {
	local label="$1" agent="$2" prompt_file="$3"
	local timeout_override="${4:-}"

	# プロンプトと生成結果をログディレクトリに保存
	local _dispatch_log_dir="tmp/debug/ai_dispatch"
	mkdir -p "$_dispatch_log_dir" 2>/dev/null || true
	local _dispatch_ts
	_dispatch_ts=$(date +%Y%m%d_%H%M%S)
	local _dispatch_tag="${_dispatch_ts}_${label}_${agent}"
	_dispatch_tag=$(printf '%s' "$_dispatch_tag" | tr '/:' '_')
	if [ -s "$prompt_file" ]; then
		cp "$prompt_file" "$_dispatch_log_dir/${_dispatch_tag}_prompt.txt" 2>/dev/null || true
	fi

	local _dispatch_output_file="$_dispatch_log_dir/${_dispatch_tag}_output.txt"

	# ハーネスは codex CLI に統一。codex:<model> はそのモデルを選択し、
	# 従来形式のエージェント識別子は CODEX_MODEL を使う。
	local _codex_timeout="$timeout_override"
	case "$agent" in
	'' )
		return 1
		;;
	*)
		if [[ "$label" == COMMENT* ]] && [ -z "$_codex_timeout" ]; then
			_codex_timeout="${COMMENT_CODEX_TIMEOUT:-90}"
		fi
		if [[ "$label" == RADIO* ]] && [ -z "$_codex_timeout" ]; then
			_codex_timeout="${RADIO_CODEX_TIMEOUT:-240}"
		fi
		_ai_call_codex "$label" "$agent" "$prompt_file" "$_codex_timeout" | tee "$_dispatch_output_file"
		;;
	esac
	local _dispatch_rc=${PIPESTATUS[0]}
	# 空出力ならログファイル削除
	[ -s "$_dispatch_output_file" ] || rm -f "$_dispatch_output_file" 2>/dev/null
	return "$_dispatch_rc"
}

# === フォールバック付き生成 ===

# ai_generate LABEL PROMPT_FILE PRIMARY_AGENT [FALLBACK_AGENT] [TIMEOUT] [VALIDATOR]
#   PRIMARY_AGENT で生成を試み、失敗したら FALLBACK_AGENT にフォールバック。
#   stdout: 生成テキスト
#   メタデータ: AI_GENERATE_LAST_AGENT に実際に使用した agent を設定
AI_GENERATE_LAST_AGENT=""

ai_generate() {
	local label="$1" prompt_file="$2" primary="$3"
	local fallback="${4:-}"
	local timeout_override="${5:-}"
	local validator="${6:-}"
	local output

	AI_GENERATE_LAST_AGENT=""

	# Primary
	output=$(_ai_dispatch "$label" "$primary" "$prompt_file" "$timeout_override")
	rc=$?
	if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
		AI_GENERATE_LAST_AGENT="$primary"
		printf '%s' "$output"
		return 0
	fi
	if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
		log "[${label}] primary ($primary) returned invalid output" >&2
	fi

	# Fallback
	if [ -n "$fallback" ]; then
		log "[${label}] primary ($primary) failed → fallback ($fallback)" >&2
		output=$(_ai_dispatch "$label" "$fallback" "$prompt_file" "$timeout_override")
		rc=$?
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
			AI_GENERATE_LAST_AGENT="$fallback"
			printf '%s' "$output"
			return 0
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
			log "[${label}] fallback ($fallback) returned invalid output" >&2
		fi
	fi

	log "[${label}] all agents failed (primary=$primary, fallback=${fallback:-none})" >&2
	return 1
}

# === バックオフ付きエージェントリスト ===

_ai_backoff_dir() {
	if [ -n "${AI_BACKOFF_DIR:-}" ]; then
		printf '%s\n' "$AI_BACKOFF_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/ai_backoff\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/ai_backoff\n'
	fi
}

# コメント・ラジオでは、一度 primary から fallback へ進んだ後に同じ
# unavailable provider を毎回待たない。agent単位のbackoffファイルは両経路で
# 共有されるため、どちらかでDeepSeekが失敗すれば両方が5時間直接MiniMaxへ進む。
_ai_backoff_sec_for_label() {
	local label="${1:-AI}" value default_value
	case "$label" in
	COMMENT*) value="${COMMENT_AGENT_BACKOFF_SEC:-18000}"; default_value=18000 ;;
	RADIO*) value="${RADIO_AGENT_BACKOFF_SEC:-18000}"; default_value=18000 ;;
	*) value="${AI_AGENT_BACKOFF_SEC:-600}"; default_value=600 ;;
	esac
	case "$value" in
	'' | *[!0-9]*) value="$default_value" ;;
	esac
	[ "$value" -lt 1 ] && value="$default_value"
	printf '%s\n' "$value"
}

# _ai_backoff_check AGENT  → 0: 使用可 / 1: バックオフ中
_ai_backoff_check() {
	local agent="$1" key bf_file now bf_until
	key=$(_ai_lock_sanitize_key "$agent")
	bf_file="$(_ai_backoff_dir)/${key}"
	[ -f "$bf_file" ] || return 0
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo "0")
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	if [ "$now" -lt "$bf_until" ]; then
		return 1
	fi
	rm -f "$bf_file" 2>/dev/null || true
	return 0
}

# _ai_backoff_set AGENT [SEC]
_ai_backoff_set() {
	local agent="$1" backoff_sec="${2:-${AI_AGENT_BACKOFF_SEC:-600}}"
	local key bf_file bf_until
	key=$(_ai_lock_sanitize_key "$agent")
	mkdir -p "$(_ai_backoff_dir)" 2>/dev/null || true
	bf_file="$(_ai_backoff_dir)/${key}"
	bf_until=$(( $(date +%s) + backoff_sec ))
	printf '%s\n' "$bf_until" > "$bf_file" 2>/dev/null || true
}

# _ai_backoff_remaining AGENT  → 残りバックオフ秒数 (0: バックオフなし)
_ai_backoff_remaining() {
	local agent="$1" key bf_file now bf_until rem
	key=$(_ai_lock_sanitize_key "$agent")
	bf_file="$(_ai_backoff_dir)/${key}"
	[ -f "$bf_file" ] || { printf '0\n'; return; }
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo "0")
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	rem=$(( bf_until - now ))
	[ "$rem" -lt 0 ] && rem=0
	printf '%s\n' "$rem"
}

# ai_generate_list LABEL PROMPT_FILE AGENT_LIST [TIMEOUT] [VALIDATOR] [LAST_AGENT_FILE] [FAILURE_KIND_FILE]
#   AGENT_LIST: カンマ区切りのエージェント識別子（優先度順）
#     例: "opencode:minimax-m3,opencode:qwen35pgo,qwen35e,opencode:glmflash"
#   バックオフ中のエージェントをスキップし、明示的なレート制限を返した
#   エージェントだけにバックオフを設定する。形式不正・空出力・通常の
#   CLI/provider失敗は、次候補へ進むがモデルのバックオフにはしない。
#   全エージェントがバックオフ中の場合は期限を尊重して再試行せず、呼び出し元へ失敗を返す。
#   stdout: 生成テキスト
#   AI_GENERATE_LAST_AGENT に実際に使用したエージェントを設定
#   LAST_AGENT_FILE を指定すると、command substitution の外側でも実使用モデルを読める。
AI_GENERATE_LIST_LAST_AGENT=""

ai_generate_list() {
	local label="$1" prompt_file="$2" agent_list_raw="$3"
	local timeout_override="${4:-}"
	local validator="${5:-}"
	local last_agent_file="${6:-}"
	local failure_kind_file="${7:-}"
	local backoff_sec
	local _bd agent output rc _rem attempted_count=0 saw_rate_limit=0
	backoff_sec=$(_ai_backoff_sec_for_label "$label")

	AI_GENERATE_LAST_AGENT=""
	AI_GENERATE_LIST_LAST_AGENT=""
	[ -n "$last_agent_file" ] && : >"$last_agent_file"
	[ -n "$failure_kind_file" ] && : >"$failure_kind_file"

	_bd=$(_ai_backoff_dir)
	mkdir -p "$_bd" 2>/dev/null || true

	# カンマ区切りをリストに展開 (bash配列)
	local agents=()
	local _IFS_save="$IFS"
	IFS=',' read -ra agents <<< "$agent_list_raw"
	IFS="$_IFS_save"

	local skipped_backoff=()

	# 第1パス: バックオフ中でないエージェントを順に試行
	for agent in "${agents[@]}"; do
		# 前後の空白を除去
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -z "$agent" ] && continue

		if ! _ai_backoff_check "$agent"; then
			_rem=$(_ai_backoff_remaining "$agent")
			log "[${label}] backoff skip: ${agent} (${_rem}s remaining)" >&2
			skipped_backoff+=("$agent")
			continue
		fi

		attempted_count=$((attempted_count + 1))
		output=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override")
		rc=$?
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
			AI_GENERATE_LAST_AGENT="$agent"
			AI_GENERATE_LIST_LAST_AGENT="$agent"
			[ -n "$last_agent_file" ] && printf '%s\n' "$agent" >"$last_agent_file"
			printf '%s' "$output"
			return 0
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
			log "[${label}] ${agent} returned invalid output → fallback" >&2
		else
			log "[${label}] ${agent} failed → fallback" >&2
		fi
		if [ "$rc" -eq "$AI_RATE_LIMIT_RC" ]; then
			saw_rate_limit=1
			log "[${label}] ${agent} explicit rate limit → backoff ${backoff_sec}s" >&2
			_ai_backoff_set "$agent" "$backoff_sec"
		else
			log "[${label}] ${agent} no model backoff (outcome=${rc})" >&2
		fi
	done

	# 全候補がバックオフ中なら、期限を尊重して即時失敗する。以前の
	# 「末尾agentだけ強制再試行」は429を連打し、明示的なレート制限を
	# 回避してしまうため廃止した。期限切れは次回の通常チェックで解除される。
	if [ "$attempted_count" -eq 0 ] && [ ${#skipped_backoff[@]} -gt 0 ]; then
		log "[${label}] all agents are in explicit rate-limit backoff; retry later" >&2
		saw_rate_limit=1
	fi

	log "[${label}] all agents failed (list=${agent_list_raw})" >&2
	if [ -n "$failure_kind_file" ]; then
		if [ "$saw_rate_limit" -eq 1 ]; then
			printf 'rate_limit\n' >"$failure_kind_file"
		else
			printf 'failed\n' >"$failure_kind_file"
		fi
	fi
	return 1
}
