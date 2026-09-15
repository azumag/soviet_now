#!/usr/bin/env python3
from pathlib import Path


policy = Path("lib/ai_generate_policy.sh")
text = policy.read_text(encoding="utf-8")

anchor = '''_ai_failure_streak_file() {
'''
insert = '''_ai_family_backoff_dir() {
	if [ -n "${AI_FAMILY_BACKOFF_DIR:-}" ]; then
		printf '%s\\n' "$AI_FAMILY_BACKOFF_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/ai_family_backoff\\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/ai_family_backoff\\n'
	fi
}

_ai_family_backoff_file() {
	local family="$1"
	printf '%s/%s\\n' "$(_ai_family_backoff_dir)" "$(_ai_lock_sanitize_key "$family")"
}

_ai_family_backoff_check() {
	local family="$1" bf_file now bf_until
	bf_file=$(_ai_family_backoff_file "$family")
	[ -f "$bf_file" ] || return 0
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo 0)
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	if [ "$now" -lt "$bf_until" ]; then
		return 1
	fi
	rm -f "$bf_file" 2>/dev/null || true
	return 0
}

_ai_family_backoff_set() {
	local family="$1" backoff_sec="$2" bf_file bf_until
	bf_file=$(_ai_family_backoff_file "$family")
	mkdir -p "$(dirname "$bf_file")" 2>/dev/null || true
	bf_until=$(( $(date +%s) + backoff_sec ))
	printf '%s\\n' "$bf_until" >"$bf_file" 2>/dev/null || true
}

_ai_family_backoff_remaining() {
	local family="$1" bf_file now bf_until rem
	bf_file=$(_ai_family_backoff_file "$family")
	[ -f "$bf_file" ] || { printf '0\\n'; return; }
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo 0)
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	rem=$((bf_until - now))
	[ "$rem" -lt 0 ] && rem=0
	printf '%s\\n' "$rem"
}

_ai_family_backoff_sec() {
	local family="$1" value=60
	case "$family" in
	vercel) value="${AI_VERCEL_FAMILY_BACKOFF_SEC:-60}" ;;
	esac
	case "$value" in ''|*[!0-9]*) value=60 ;; esac
	[ "$value" -lt 1 ] && value=60
	# Family suppression is deliberately short; agent-level quota backoff keeps
	# its existing longer duration. Do not let a config typo turn this into a
	# multi-hour provider outage.
	[ "$value" -gt 300 ] && value=300
	printf '%s\\n' "$value"
}

_ai_failure_streak_file() {
'''
assert text.count(anchor) == 1, "family helper anchor drifted"
text = text.replace(anchor, insert, 1)

anchor = '''	local skipped_rate_backoff=()
	local skipped_failure_backoff=()
'''
replace = '''	local skipped_rate_backoff=()
	local skipped_family_backoff=()
	local skipped_failure_backoff=()
'''
assert text.count(anchor) == 1, "skip arrays anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''		if ! _ai_agent_spec_valid "$agent"; then
			log "[${label}] invalid agent spec skipped: ${agent}" >&2
			continue
		fi

		# 明示的 429/quota backoff は用途を跨いで共有する。
'''
replace = '''		if ! _ai_agent_spec_valid "$agent"; then
			log "[${label}] invalid agent spec skipped: ${agent}" >&2
			continue
		fi

		# A single Vercel 429 remains agent-scoped. Only a previous chain that
		# proved >=2 distinct Vercel 429s, or this chain after its second distinct
		# Vercel 429, activates the short provider-family breaker. Non-Vercel
		# candidates are never suppressed by this check.
		case "$agent" in
		vercel:*)
			if ! _ai_family_backoff_check "vercel"; then
				_rem=$(_ai_family_backoff_remaining "vercel")
				log "[${label}] Vercel family rate-limit backoff skip: ${agent} (${_rem}s remaining)" >&2
				skipped_family_backoff+=("$agent")
				continue
			fi
			;;
		esac

		# 明示的 429/quota backoff は用途を跨いで共有する。
'''
assert text.count(anchor) == 1, "agent validation anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''				[ "$_vercel_seen" -eq 1 ] || vercel_rate_limit_agents+=("$agent")
				;;
'''
replace = '''				[ "$_vercel_seen" -eq 1 ] || vercel_rate_limit_agents+=("$agent")
				if [ "${#vercel_rate_limit_agents[@]}" -ge 2 ]; then
					local family_backoff_sec
					family_backoff_sec=$(_ai_family_backoff_sec "vercel")
					_ai_family_backoff_set "vercel" "$family_backoff_sec"
					log "[${label}] multiple distinct Vercel rate limits in one chain → family backoff ${family_backoff_sec}s" >&2
				fi
				;;
'''
assert text.count(anchor) == 1, "vercel counter anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''	if [ "$attempted_count" -eq 0 ] && [ ${#skipped_rate_backoff[@]} -gt 0 ]; then
		log "[${label}] all available agents include explicit rate-limit backoff; retry later" >&2
		saw_rate_limit=1
'''
replace = '''	if [ "$attempted_count" -eq 0 ] && { [ ${#skipped_rate_backoff[@]} -gt 0 ] || [ ${#skipped_family_backoff[@]} -gt 0 ]; }; then
		log "[${label}] all available agents include explicit rate-limit or family backoff; retry later" >&2
		saw_rate_limit=1
'''
assert text.count(anchor) == 1, "all-skipped anchor drifted"
text = text.replace(anchor, replace, 1)

policy.write_text(text, encoding="utf-8")


test = Path("tests/test_ai_prepass_backoff_scope.sh")
text = test.read_text(encoding="utf-8")

anchor = '''export AI_FAILURE_BACKOFF_DIR="$TMP/failure_backoff"
export AI_FAIL_STREAK_DIR="$TMP/fail_streak"
'''
replace = '''export AI_FAILURE_BACKOFF_DIR="$TMP/failure_backoff"
export AI_FAMILY_BACKOFF_DIR="$TMP/family_backoff"
export AI_FAIL_STREAK_DIR="$TMP/fail_streak"
'''
assert text.count(anchor) == 1, "test env anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''export AI_FAILURE_STREAK_MAX_BACKOFF_SEC=3600
export VERCEL_FREE_AGENTS="vercel:minimax/minimax-m3-free vercel:poolside/laguna-s-2.1-free"
'''
replace = '''export AI_FAILURE_STREAK_MAX_BACKOFF_SEC=3600
export AI_VERCEL_FAMILY_BACKOFF_SEC=60
export VERCEL_FREE_AGENTS="vercel:minimax/minimax-m3-free vercel:poolside/laguna-s-2.1-free vercel:third/provider-free"
'''
assert text.count(anchor) == 1, "test family sec anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''	rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR" "$AI_STATS_DIR"
	mkdir -p "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR" "$AI_STATS_DIR"
'''
replace = '''	rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAMILY_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR" "$AI_STATS_DIR"
	mkdir -p "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAMILY_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR" "$AI_STATS_DIR"
'''
assert text.count(anchor) == 1, "test reset anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''	chain_two_vercel_fail)
		case "$agent" in
		vercel:minimax/minimax-m3-free|vercel:poolside/laguna-s-2.1-free) return "$AI_RATE_LIMIT_RC" ;;
		esac
		return 1
		;;
	always_fail) return 1 ;;
'''
replace = '''	chain_two_vercel_fail)
		case "$agent" in
		vercel:minimax/minimax-m3-free|vercel:poolside/laguna-s-2.1-free) return "$AI_RATE_LIMIT_RC" ;;
		esac
		return 1
		;;
	chain_two_vercel_then_third_then_amd)
		case "$agent" in
		vercel:minimax/minimax-m3-free|vercel:poolside/laguna-s-2.1-free) return "$AI_RATE_LIMIT_RC" ;;
		vercel:third/provider-free) printf 'family breaker failed'; return 0 ;;
		amd:DeepSeek-V4-Flash) printf 'fallback ok'; return 0 ;;
		esac
		return 1
		;;
	chain_two_vercel_generic_then_amd)
		case "$agent" in
		vercel:minimax/minimax-m3-free|vercel:poolside/laguna-s-2.1-free) return 1 ;;
		amd:DeepSeek-V4-Flash) printf 'generic fallback ok'; return 0 ;;
		esac
		return 1
		;;
	chain_vercel_ok)
		case "$agent" in vercel:*) printf 'vercel ok'; return 0 ;; esac
		return 1
		;;
	always_fail) return 1 ;;
'''
assert text.count(anchor) == 1, "test dispatch modes anchor drifted"
text = text.replace(anchor, replace, 1)

anchor = '''# 8. runtime shim が policy layer を ai_generate.sh の直後に読むことを固定する。
if grep -Fq 'source "$ELOOP_LIB_DIR/lib/ai_generate_policy.sh"' "$ROOT/eloop_lib.sh"; then
	pass 'runtime shim loads scoped backoff policy'
else
	fail_case 'runtime shim loads scoped backoff policy'
fi
'''
replace = '''# 8. Vercel A=429, B=success では family breaker を発火しない。
reset_state
TEST_MODE=chain_one_vercel_then_vercel
chain_out=$(ai_generate_list 'RADIO:news' "$prompt" 'vercel:minimax/minimax-m3-free,vercel:poolside/laguna-s-2.1-free' 2>/dev/null || true)
family_file=$(_ai_family_backoff_file vercel)
if [ "$chain_out" = 'vercel recovered' ] && [ ! -f "$family_file" ]; then
	pass 'single Vercel 429 does not trip provider-family breaker'
else
	fail_case 'single Vercel 429 does not trip provider-family breaker'
fi

# 9. 同一chainで異なる2 agentが429なら短いfamily breakerを発火し、
# 後続Vercel候補だけを飛ばして非Vercel fallbackへ進む。
reset_state
TEST_MODE=chain_two_vercel_then_third_then_amd
chain_out=$(ai_generate_list 'RADIO:news' "$prompt" 'vercel:minimax/minimax-m3-free,vercel:poolside/laguna-s-2.1-free,vercel:third/provider-free,amd:DeepSeek-V4-Flash' 2>/dev/null || true)
family_file=$(_ai_family_backoff_file vercel)
if [ "$chain_out" = 'fallback ok' ] \
	&& [ -f "$family_file" ] \
	&& [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 3 ] \
	&& ! grep -Fq 'vercel:third/provider-free' "$ATTEMPT_LOG" \
	&& grep -Fq 'amd:DeepSeek-V4-Flash' "$ATTEMPT_LOG"; then
	pass 'two distinct Vercel 429s suppress later Vercel but preserve non-Vercel fallback'
else
	fail_case 'two distinct Vercel 429s suppress later Vercel but preserve non-Vercel fallback'
fi

# 10. breaker は短時間だけ全chainで共有し、その間も非Vercelは止めない。
: >"$ATTEMPT_LOG"
TEST_MODE=chain_two_vercel_then_third_then_amd
chain_out=$(ai_generate_list 'COMMENT' "$prompt" 'vercel:third/provider-free,amd:DeepSeek-V4-Flash' 2>/dev/null || true)
if [ "$chain_out" = 'fallback ok' ] \
	&& [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 1 ] \
	&& grep -Fq 'amd:DeepSeek-V4-Flash' "$ATTEMPT_LOG"; then
	pass 'active Vercel family breaker is shared but does not block non-Vercel candidates'
else
	fail_case 'active Vercel family breaker is shared but does not block non-Vercel candidates'
fi

# 11. expiry後は、個別429を受けていないVercel候補を再試行できる。
printf '0\\n' >"$family_file"
: >"$ATTEMPT_LOG"
TEST_MODE=chain_vercel_ok
chain_out=$(ai_generate_list 'COMMENT' "$prompt" 'vercel:third/provider-free' 2>/dev/null || true)
if [ "$chain_out" = 'vercel ok' ] \
	&& [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 1 ] \
	&& [ ! -f "$family_file" ]; then
	pass 'expired Vercel family breaker permits retry and cleans its state'
else
	fail_case 'expired Vercel family breaker permits retry and cleans its state'
fi

# 12. generic provider failure はfamily breaker条件に数えない。
reset_state
TEST_MODE=chain_two_vercel_generic_then_amd
chain_out=$(ai_generate_list 'RADIO:news' "$prompt" 'vercel:minimax/minimax-m3-free,vercel:poolside/laguna-s-2.1-free,amd:DeepSeek-V4-Flash' 2>/dev/null || true)
family_file=$(_ai_family_backoff_file vercel)
if [ "$chain_out" = 'generic fallback ok' ] && [ ! -f "$family_file" ]; then
	pass 'generic Vercel failures do not trip provider-family rate-limit breaker'
else
	fail_case 'generic Vercel failures do not trip provider-family rate-limit breaker'
fi

# 13. 上限は5分に固定し、設定typoでprovider全体を長時間止めない。
AI_VERCEL_FAMILY_BACKOFF_SEC=99999
if [ "$(_ai_family_backoff_sec vercel)" = '300' ]; then
	pass 'Vercel family breaker duration is capped at five minutes'
else
	fail_case 'Vercel family breaker duration is capped at five minutes'
fi
AI_VERCEL_FAMILY_BACKOFF_SEC=60

# 14. runtime shim が policy layer を ai_generate.sh の直後に読むことを固定する。
if grep -Fq 'source "$ELOOP_LIB_DIR/lib/ai_generate_policy.sh"' "$ROOT/eloop_lib.sh"; then
	pass 'runtime shim loads scoped backoff policy'
else
	fail_case 'runtime shim loads scoped backoff policy'
fi
'''
assert text.count(anchor) == 1, "test append anchor drifted"
text = text.replace(anchor, replace, 1)

test.write_text(text, encoding="utf-8")
