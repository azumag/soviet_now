#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

unset RADIO_CODEX_TIMEOUT
source "$ROOT/core/radio_timeout_guard.sh"

# Unset keeps ai_generate.sh's own 240s default path intact.
[ -z "${RADIO_CODEX_TIMEOUT:-}" ]

RADIO_CODEX_TIMEOUT=20
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

RADIO_CODEX_TIMEOUT=59
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

RADIO_CODEX_TIMEOUT=60
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "60" ]

RADIO_CODEX_TIMEOUT=180
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "180" ]

RADIO_CODEX_TIMEOUT=not-a-number
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

# Production の長寿命 shell では、guard を source した後から継承環境などで
# RADIO_CODEX_TIMEOUT が再注入される余地がある。RADIO の通常 chain は
# dispatch 直前にも floor を再適用し、旧20秒値を process budget に渡さない。
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export AI_BACKOFF_DIR="$TMP/backoff"
export AI_FAIL_STREAK_DIR="$TMP/streak"
export AI_FAILURE_BACKOFF_DIR="$TMP/failure_backoff"
AI_RATE_LIMIT_RC=79
AI_GATE_GIVEUP_RC=78
AI_QUEUE_GIVEUP_RC=77
AI_DISPATCH_VALIDATOR=""
AI_GENERATE_LAST_AGENT=""
AI_GENERATE_LIST_LAST_AGENT=""

log() { :; }
_ai_backoff_dir() { printf '%s\n' "$AI_BACKOFF_DIR"; }
_ai_lock_sanitize_key() { printf '%s\n' "$1" | tr -c '[:alnum:]_.-' '_'; }
_ai_fail_streak_dir() { printf '%s\n' "$AI_FAIL_STREAK_DIR"; }
_ai_agent_spec_valid() { return 0; }
_ai_backoff_check() { return 0; }
_ai_backoff_remaining() { printf '0\n'; }
_ai_stats_record() { :; }
_ai_resolved_model_from_agent() { printf '%s\n' "$1"; }
_ai_backoff_sec_for_agent() { printf '60\n'; }
_ai_backoff_set() { :; }

CAPTURE="$TMP/dispatch.txt"
_ai_dispatch() {
	printf '%s|%s\n' "${RADIO_CODEX_TIMEOUT:-unset}" "${4:-}" >"$CAPTURE"
	printf 'ok'
}

source "$ROOT/lib/ai_generate_policy.sh"
printf 'prompt' >"$TMP/prompt"

RADIO_CODEX_TIMEOUT=20
ai_generate_list "RADIO:jiji:prepass" "$TMP/prompt" "vercel:test" "" >/dev/null
[ "$(cat "$CAPTURE")" = "240|" ]

# 明示 per-call timeout は個別補助処理の契約なので、runtime floor より優先する。
RADIO_CODEX_TIMEOUT=20
ai_generate_list "RADIO:test" "$TMP/prompt" "vercel:test" "30" >/dev/null
[ "$(cat "$CAPTURE")" = "20|30" ]

echo "radio timeout guard test: PASS"
