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
	printf '%s' "$1" | grep -Eiq 'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|request_id|invalid error token|invalid token|not logged in|please run /login|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy'
}

_contains_claude_login_error_text() {
	printf '%s' "$1" | grep -Eiq 'not logged in|please run /login'
}
