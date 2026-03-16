#!/bin/bash
# lib/eloop_ai.sh - AI実行・スピナー・プロンプト構築

declare -p CLAUDE_CMD >/dev/null 2>&1 || CLAUDE_CMD=(env -u ANTHROPIC_AUTH_TOKEN claude)

#=== スピナー ===

_spinner_pid=0
start_spinner() {
	local label="$1"
	(
		local frames=('⣾' '⣽' '⣻' '⢿' '⡿' '⣟' '⣯' '⣷')
		local i=0 start=$SECONDS
		while true; do
			local e=$((SECONDS - start))
			local m=$((e / 60)) s=$((e % 60))
			printf '\r  \033[1;35m%s\033[0m \033[1m%s\033[0m \033[2m%d:%02d\033[0m  ' \
				"${frames[i % ${#frames[@]}]}" "$label" "$m" "$s" >&2
			sleep 0.12
			((i++))
		done
	) &
	_spinner_pid=$!
}
stop_spinner() {
	if [ "${_spinner_pid:-0}" -ne 0 ]; then
		kill "$_spinner_pid" 2>/dev/null
		wait "$_spinner_pid" 2>/dev/null
		printf '\r\033[K' >&2
		_spinner_pid=0
	fi
}

#=== プロンプト構築 ===

build_prompt() {
	local pf="$1"
	shift
	local p
	p=$(cat "$pf" 2>/dev/null) || return 1
	local c=""
	for f in "$@"; do
		[ -f "$f" ] && c+=$'\n--- '"$f"$' ---\n'"$(cat "$f")"$'\n---\n'
	done
	[ -n "$c" ] && p="参照データ:${c}
${p}"
	echo "$p"
}

#=== コマンド実行 ===

run_cmd() {
	local spec="$1" prompt="$2"
	local type="${spec%%:*}" agent="${spec#*:}"
	[ "$type" = "$agent" ] && agent=""
	local target="$type"
	[ -n "$agent" ] && target="${type}:${agent}"
	local cmd_log_file="${RUN_CMD_LOG_FILE:-}"
	local cmd_log_tag="${RUN_CMD_LOG_TAG:-$type}"

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_prompt.XXXXXX)
	printf '%s' "$prompt" >"$prompt_file"
	log "[CMD] $(wc -c <"$prompt_file" | tr -d ' ')B → $type"
	if [ -n "$cmd_log_file" ]; then
		mkdir -p "$(dirname "$cmd_log_file")" 2>/dev/null || true
		_trim_log_file "$cmd_log_file" "$IMPROVE_AI_LOG_KEEP_LINES" "$IMPROVE_AI_LOG_TRIM_LINES"
		printf '[%s] [AI:%s] START spec=%s target=%s\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$spec" "$target" >>"$cmd_log_file" 2>/dev/null || true
	fi

	case "$type" in
	glm)
		if [ -n "$cmd_log_file" ]; then
			opencode run "$(cat "$prompt_file")" --agent="zai" >>"$cmd_log_file" 2>&1 &
		else
			opencode run "$(cat "$prompt_file")" --agent="zai" &
		fi
		;;
	gemini)
		if [ -n "$cmd_log_file" ]; then
			gemini -p "$(cat "$prompt_file")" -y -s >>"$cmd_log_file" 2>&1 &
		else
			gemini -p "$(cat "$prompt_file")" -y -s &
		fi
		;;
	gemini-flash)
		if [ -n "$cmd_log_file" ]; then
			gemini -p "$(cat "$prompt_file")" -y -s --model=gemini-2.5-flash >>"$cmd_log_file" 2>&1 &
		else
			gemini -p "$(cat "$prompt_file")" -y -s --model=gemini-2.5-flash &
		fi
		;;
	sonnet)
		if [ -n "$cmd_log_file" ]; then
			"${CLAUDE_CMD[@]}" -p "$(cat "$prompt_file")" --model=sonnet --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
		else
			"${CLAUDE_CMD[@]}" -p "$(cat "$prompt_file")" --model=sonnet --permission-mode=acceptEdits &
		fi
		;;
	opus)
		if [ -n "$cmd_log_file" ]; then
			"${CLAUDE_CMD[@]}" -p "$(cat "$prompt_file")" --model=opus --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
		else
			"${CLAUDE_CMD[@]}" -p "$(cat "$prompt_file")" --model=opus --permission-mode=acceptEdits &
		fi
		;;
	claude)
		if [ -n "$cmd_log_file" ]; then
			"${CLAUDE_CMD[@]}" -p "$(cat "$prompt_file")" --model=Haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
		else
			"${CLAUDE_CMD[@]}" -p "$(cat "$prompt_file")" --model=Haiku --permission-mode=acceptEdits &
		fi
		;;
	opencode)
		if [ -n "$cmd_log_file" ]; then
			opencode run "$(cat "$prompt_file")" --agent="${agent:-glmflash}" >>"$cmd_log_file" 2>&1 &
		else
			opencode run "$(cat "$prompt_file")" --agent="${agent:-glmflash}" &
		fi
		;;
	esac
	local cmd_pid=$!

	start_spinner "$type thinking..."

	local prev_int_trap interrupted
	prev_int_trap=$(trap -p INT || true)
	interrupted=0
	trap 'interrupted=1; stop_spinner; kill "$cmd_pid" 2>/dev/null; wait "$cmd_pid" 2>/dev/null; log "Interrupted"' INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?
	if [ "$interrupted" -eq 1 ]; then
		ret=130
	fi
	if [ -n "$cmd_log_file" ]; then
		printf '[%s] [AI:%s] END rc=%s\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$ret" >>"$cmd_log_file" 2>/dev/null || true
		_trim_log_file "$cmd_log_file" "$IMPROVE_AI_LOG_KEEP_LINES" "$IMPROVE_AI_LOG_TRIM_LINES"
	fi

	stop_spinner
	if [ -n "$prev_int_trap" ]; then
		eval "$prev_int_trap"
	else
		trap - INT
	fi

	rm -f "$prompt_file"

	return $ret
}

#=== AIステップ ===

run_ai() {
	local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"
	shift 5
	local prompt
	prompt=$(build_prompt "$pf" "$@")
	if [ -z "$prompt" ]; then
		log "[$label] prompt missing"
		return 1
	fi

	local expect_mtime_before=""
	if [ -n "$expect" ] && [ -f "$expect" ]; then
		expect_mtime_before=$(stat -f '%m' "$expect" 2>/dev/null)
	fi

	local primary_attempts="${RUN_AI_PRIMARY_RETRIES:-1}"
	case "$primary_attempts" in
	''|*[!0-9]*) primary_attempts=1 ;;
	esac
	[ "$primary_attempts" -lt 1 ] && primary_attempts=1

	log "[$label] primary=$primary (attempts=$primary_attempts)"
	local prev_cmd_log_tag="${RUN_CMD_LOG_TAG:-}"
	local primary_ret=1
	local attempt=1
	while [ "$attempt" -le "$primary_attempts" ]; do
		if [ "$primary_attempts" -gt 1 ]; then
			RUN_CMD_LOG_TAG="${label}:primary#${attempt}"
		else
			RUN_CMD_LOG_TAG="${label}:primary"
		fi
		run_cmd "$primary" "$prompt"
		primary_ret=$?
		if [ -n "$expect" ]; then
			local expect_mtime_after=""
			[ -f "$expect" ] && expect_mtime_after=$(stat -f '%m' "$expect" 2>/dev/null)
			if [ -s "$expect" ] && [ "$expect_mtime_after" != "$expect_mtime_before" ]; then
				if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
				log "[$label] primary OK ($expect written, attempt ${attempt}/${primary_attempts})"
				return 0
			fi
		else
			[ "$primary_ret" -eq 0 ] && {
				if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
				return 0
			}
		fi
		if [ "$attempt" -lt "$primary_attempts" ]; then
			log "[$label] primary attempt ${attempt}/${primary_attempts} failed"
		fi
		attempt=$((attempt + 1))
	done

	log "[$label] primary failed → fallback=$fallback"
	RUN_CMD_LOG_TAG="${label}:fallback"
	run_cmd "$fallback" "$prompt"
	if [ -n "$expect" ]; then
		local expect_mtime_fb=""
		[ -f "$expect" ] && expect_mtime_fb=$(stat -f '%m' "$expect" 2>/dev/null)
		if [ ! -s "$expect" ] || [ "$expect_mtime_fb" = "$expect_mtime_before" ]; then
			if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
			log "[$label] fallback also failed ($expect not written)"
			return 1
		fi
	fi
	if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
}
