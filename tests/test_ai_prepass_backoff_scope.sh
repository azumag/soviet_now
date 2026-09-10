#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log() { :; }
source "$ROOT/core/helpers.sh"
source "$ROOT/lib/ai_generate.sh"
source "$ROOT/lib/ai_generate_policy.sh"

export AI_BACKOFF_DIR="$TMP/rate_backoff"
export AI_FAILURE_BACKOFF_DIR="$TMP/failure_backoff"
export AI_FAIL_STREAK_DIR="$TMP/fail_streak"
export AI_STATS_DIR="$TMP/stats"
export AI_BACKOFF_FAILURE_SEC=300
export AI_FAILURE_STREAK_MAX_BACKOFF_SEC=3600
export ATTEMPT_LOG="$TMP/attempts.log"

prompt="$TMP/prompt.txt"
printf 'test prompt\n' >"$prompt"

ok=0
fail=0
pass() { printf 'ok - %s\n' "$1"; ok=$((ok + 1)); }
fail_case() { printf 'not ok - %s\n' "$1" >&2; fail=$((fail + 1)); }
reset_state() {
	rm -rf "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR" "$AI_STATS_DIR"
	mkdir -p "$AI_BACKOFF_DIR" "$AI_FAILURE_BACKOFF_DIR" "$AI_FAIL_STREAK_DIR" "$AI_STATS_DIR"
	: >"$ATTEMPT_LOG"
}

TEST_MODE=""
_ai_dispatch() {
	local label="$1" agent="$2"
	printf '%s|%s\n' "$label" "$agent" >>"$ATTEMPT_LOG"
	case "$TEST_MODE" in
	prepass_fail_main_ok)
		case "${label,,}" in
		*:prepass*) return 1 ;;
		*) printf 'main ok'; return 0 ;;
		esac
		;;
	main_fail_prepass_ok)
		case "${label,,}" in
		*:prepass*) printf 'prepass ok'; return 0 ;;
		*) return 1 ;;
		esac
		;;
	prepass_rate_limit)
		case "${label,,}" in
		*:prepass*) return "$AI_RATE_LIMIT_RC" ;;
		*) printf 'must not run'; return 0 ;;
		esac
		;;
	always_fail) return 1 ;;
	*) printf 'ok'; return 0 ;;
	esac
}

agent='opencode:test-shared'

# 1. optional prepass の generic failure は、同一 agent の本文生成を遮断しない。
reset_state
TEST_MODE=prepass_fail_main_ok
ai_generate_list 'RADIO:news:prepass' "$prompt" "$agent" >/dev/null 2>&1 || true
main_out=$(ai_generate_list 'RADIO:news' "$prompt" "$agent" 2>/dev/null || true)
if [ "$main_out" = 'main ok' ] && [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 2 ]; then
	pass 'prepass generic failure does not block main generation'
else
	fail_case 'prepass generic failure does not block main generation'
fi

# 2. main 側の generic failure も prepass 側を汚染しない。
reset_state
TEST_MODE=main_fail_prepass_ok
ai_generate_list 'RADIO:news' "$prompt" "$agent" >/dev/null 2>&1 || true
prepass_out=$(ai_generate_list 'RADIO:news:prepass' "$prompt" "$agent" 2>/dev/null || true)
if [ "$prepass_out" = 'prepass ok' ] && [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 2 ]; then
	pass 'main generic failure does not block prepass generation'
else
	fail_case 'main generic failure does not block prepass generation'
fi

# 3. 同一スコープ内では generic failure の短期 circuit breaker を維持する。
reset_state
TEST_MODE=always_fail
ai_generate_list 'RADIO:weather:prepass' "$prompt" "$agent" >/dev/null 2>&1 || true
ai_generate_list 'RADIO:weather:prepass' "$prompt" "$agent" >/dev/null 2>&1 || true
if [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 1 ]; then
	pass 'prepass provider failure still suppresses repeated dead-provider attempts'
else
	fail_case 'prepass provider failure still suppresses repeated dead-provider attempts'
fi

# 4. 明示的 429/rate-limit は global backoff として本文にも共有する。
reset_state
TEST_MODE=prepass_rate_limit
kind="$TMP/failure-kind.txt"
ai_generate_list 'RADIO:fortune:prepass' "$prompt" "$agent" '' '' '' "$kind" >/dev/null 2>&1 || true
TEST_MODE=prepass_fail_main_ok
ai_generate_list 'RADIO:fortune' "$prompt" "$agent" '' '' '' "$kind" >/dev/null 2>&1 || true
if [ "$(wc -l <"$ATTEMPT_LOG" | tr -d ' ')" -eq 1 ] && [ "$(cat "$kind" 2>/dev/null)" = 'rate_limit' ]; then
	pass 'explicit rate limit remains shared across prepass and main'
else
	fail_case 'explicit rate limit remains shared across prepass and main'
fi

# 5. runtime shim が policy layer を ai_generate.sh の直後に読むことを固定する。
if grep -Fq 'source "$ELOOP_LIB_DIR/lib/ai_generate_policy.sh"' "$ROOT/eloop_lib.sh"; then
	pass 'runtime shim loads scoped backoff policy'
else
	fail_case 'runtime shim loads scoped backoff policy'
fi

printf '# pass=%d fail=%d\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
