# core/helpers.sh - log, commands_empty, _trim_log_file 等

#=== コアヘルパー ===

# score_history.txt からスコアのみ抽出（新旧両形式対応）
_last_score() {
	local line
	line=$(tail -1 score_history.txt 2>/dev/null) || { echo 0; return; }
	printf '%s\n' "${line##*	}"
}
_recent_scores() {
	local n="${1:-10}"
	tail -"$n" score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}'
}

_append_celebration_history() {
	local kind="$1" score="${2:-0}" turns="${3:-0}" game_num="${4:-0}"
	local history_file=""
	case "$kind" in
	russia) history_file="$RUSSIA_CREATION_HISTORY_FILE" ;;
	soviet) history_file="$SOVIET_CREATION_HISTORY_FILE" ;;
	*) return 1 ;;
	esac
	mkdir -p "$(dirname "$history_file")" 2>/dev/null || true
	if [ -f "$history_file" ]; then
		local last_line last_key new_key
		last_line=$(tail -1 "$history_file" 2>/dev/null || true)
		last_key=$(printf '%s' "$last_line" | awk -F'\t' 'NR==1{print $3 "\t" $4 "\t" $5}')
		new_key=$(printf '%s\t%s\t%s' "$game_num" "$score" "$turns")
		if [ "$last_key" = "$new_key" ]; then
			return 0
		fi
	fi
	local iso_ts local_ts
	iso_ts=$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/')
	local_ts=$(date '+%Y-%m-%d %H:%M %Z')
	printf '%s\t%s\t%s\t%s\t%s\n' "$iso_ts" "$local_ts" "$game_num" "$score" "$turns" >>"$history_file"
	if [ -f "$history_file" ] && [ "$(wc -l < "$history_file")" -gt "$CELEBRATION_HISTORY_KEEP_LINES" ]; then
		tail -"$CELEBRATION_HISTORY_KEEP_LINES" "$history_file" >"${history_file}.tmp" 2>/dev/null && \
			mv "${history_file}.tmp" "$history_file" 2>/dev/null
	fi
}

commands_empty() { [ -z "$(tr -d '[:space:]' <"$COMMANDS" 2>/dev/null)" ]; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }
# bash 3.2 (macOS /bin/bash) には BASHPID がないため、サブシェルPID取得のポータブル関数
_my_pid() { sh -c 'echo $PPID'; }
clear_commands_file() { : >"$COMMANDS"; }
_clear_stale_commands_if_any() {
	local reason="${1:-unknown}"
	[ -f "$COMMANDS" ] || return 0
	local cmd_preview
	cmd_preview=$(tr '\n' ' ' <"$COMMANDS" 2>/dev/null | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
	[ -n "$cmd_preview" ] || return 0
	if [ "${#cmd_preview}" -gt 120 ]; then
		cmd_preview="${cmd_preview:0:117}..."
	fi
	log "[COMMANDS] stale commandsをクリア (${reason}): ${cmd_preview}"
	clear_commands_file
}
_trim_log_file() {
	local f="$1" keep="${2:-2000}" trim="${3:-4000}"
	[ -n "$f" ] || return 0
	[ -f "$f" ] || return 0
	local n
	n=$(wc -l <"$f" 2>/dev/null | tr -d ' ')
	[ "${n:-0}" -le "$trim" ] && return 0
	local tmpf="${f}.tmp"
	tail -n "$keep" "$f" >"$tmpf" 2>/dev/null && mv "$tmpf" "$f" 2>/dev/null || true
}

#=== ANSIエスケープ除去 ===

_strip_ansi() {
	perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

_contains_provider_error_text() {
	printf '%s' "$1" | grep -Eiq 'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|bad request|request_id|invalid error token|invalid token|not logged in|please run /login|unexpected error, check log file|failed to run the query|pragma wal_checkpoint|insufficient balance|no resource package|rate limit exceeded|freeusagelimiterror|degraded function cannot be invoked|function id .*degraded|providermodelnotfounderror|model not found|no such model|modelid|providerid|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|(^|[^[:alnum:]])error:[[:space:]]*gone|status["[:space:]]*:[[:space:]]*410|reached its end of life|is no longer available'
}

_contains_webfetch_failure_text() {
	printf '%s' "$1" | grep -Eiq '((WebFetch|WebSearch).*(取得できなかった|取得できません|確認が入りました|許可|permission|denied|rejected)|((取得できなかった|取得できません|確認が入りました|許可|permission|denied|rejected).*(WebFetch|WebSearch)))'
}

_notify_webfetch_failure() {
	local label="${1:-AI}" agent="${2:-unknown}" text="${3:-}" context="${4:-}"
	_contains_webfetch_failure_text "$text" || return 1

	local state_dir="${TMP_STATE_DIR:-tmp/state}"
	local throttle="${WEBFETCH_FAILURE_NOTIFY_THROTTLE_SEC:-180}"
	local key marker now mt age
	key=$(printf '%s_%s_%s' "$label" "$agent" "$context" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/_/g; s/^_+//; s/_+$//')
	[ -n "$key" ] || key="webfetch"
	marker="${state_dir}/webfetch_failure_notify_${key}"
	now=$(date +%s)
	case "$throttle" in
	'' | *[!0-9]*) throttle=180 ;;
	esac
	if [ -f "$marker" ]; then
		mt=$(stat -f %m "$marker" 2>/dev/null || stat -c %Y "$marker" 2>/dev/null || echo 0)
		age=$((now - mt))
		[ "$age" -lt "$throttle" ] && return 0
	fi

	mkdir -p "$state_dir" 2>/dev/null || true
	: >"$marker" 2>/dev/null || true
	log "[${label}] Web取得失敗を検出; on-air本文から除去済み (agent=${agent}${context:+ context=${context}})" >&2
	if [ -x ./overlay_notify.sh ]; then
		./overlay_notify.sh radio "Web取得失敗" "label=${label} agent=${agent}${context:+ context=${context}} | 音声本文からは除去" "warn" >/dev/null 2>&1 || true
	fi
	return 0
}

_contains_claude_login_error_text() {
	printf '%s' "$1" | grep -Eiq 'not logged in|please run /login'
}

# soren_loop.sh の同名関数をバックグラウンド実行版で上書き。
# eloop_lib.sh は毎ループ source されるためこちらが優先される。
# フォアグラウンド実行だと monitor が詰まった際にメインループ全体がブロックされる。
_run_improve_runtime_monitor() {
	[ -x ./monitor_improve_runtime.sh ] || return 0
	local now interval
	now=$(date +%s)
	interval="${SOREN_IMPROVE_MONITOR_INTERVAL_SEC:-15}"
	case "$interval" in
	'' | *[!0-9]*) interval=15 ;;
	esac
	if [ "${_SOREN_IMPROVE_MONITOR_TS:-0}" -gt 0 ] && [ $((now - _SOREN_IMPROVE_MONITOR_TS)) -lt "$interval" ]; then
		return 0
	fi
	_SOREN_IMPROVE_MONITOR_TS=$now
	./monitor_improve_runtime.sh >/dev/null 2>&1 &
}
