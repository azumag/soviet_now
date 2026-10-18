#!/usr/bin/env bash
# mixed_language の局所修正経路をAI呼び出しスタブで検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d /tmp/radio-mixed-repair-test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

ELOOP_LIB_DIR="$ROOT"
export ELOOP_LIB_DIR
# radio_engine.sh は関数定義だけを読み込むため、テストに必要な境界だけスタブ化する。
source "$ROOT/broadcast/radio_engine.sh"
_ai_guard_model_output() { cat; }
_contains_provider_error_text() { return 1; }
_remove_retired_minimax_agents() { printf '%s' "$1" | sed -E 's/[^,]*minimax[^,]*,?//Ig'; }

REPAIR_OUTPUT=''
REPAIR_CALLS=0
ai_generate_list() {
	local prompt_file="$2" last_agent_file="${6:-}"
	REPAIR_CALLS=$((REPAIR_CALLS + 1))
	[ -z "$last_agent_file" ] || printf '%s\n' 'fixture:repair' >"$last_agent_file"
	[ -z "${CAPTURE_PROMPT:-}" ] || cp "$prompt_file" "$CAPTURE_PROMPT"
	printf '%s' "$REPAIR_OUTPUT"
}

input='こんばんは。A bridge is more than just a structure. 地元の人々は思い出を語っています。'
REPAIR_OUTPUT='橋は単なる構造物ではなく、人が暮らす場所です。'
meta="$TMP/success.meta"
out=$(_radio_repair_mixed_language "$input" news 'fixture:repair,opencode:minimax-m3' "$meta")
if [[ "$out" != *'A bridge is more than just a structure.'* ]] &&
	[[ "$out" == *'橋は単なる構造物ではなく、人が暮らす場所です。'* ]] &&
	grep -q '^status=ok$' "$meta" && grep -q '^spans=1$' "$meta"; then
	ok 'one mixed sentence is repaired without rewriting the surrounding text'
else
	not_ok 'one mixed sentence is repaired without rewriting the surrounding text'
fi
if grep -q 'opencode:minimax-m3' "$meta"; then
	not_ok 'retired MiniMax candidate was removed from the repair chain'
else
	ok 'retired MiniMax candidate was removed from the repair chain'
fi

sanitized=$(
	AI_COMMON_AGENTS='opencode:ok,opencode:minimax-m3' \
	RADIO_AGENTS='opencode:ok,opencode:minimax-m3' \
	RADIO_QUALITY_REPAIR_AGENTS='opencode:minimax-m3,amd:DeepSeek-V4-Flash' \
	ELOOP_LIB_DIR="$ROOT" bash -c \
		'set -u; source "$ELOOP_LIB_DIR/core/config.sh"; printf "%s" "$RADIO_QUALITY_REPAIR_AGENTS"'
)
if [[ "$sanitized" != *minimax* ]] && [[ "$sanitized" == *'amd:DeepSeek-V4-Flash'* ]]; then
	ok 'config sanitizes MiniMax from the dedicated repair chain'
else
	not_ok 'config sanitizes MiniMax from the dedicated repair chain'
fi

input='A bridge is more than just a structure. This is a simple test sentence. 日本語の本文です。'
REPAIR_OUTPUT='日本語の文に置き換えます。'
meta="$TMP/multiple.meta"
out=$(_radio_repair_mixed_language "$input" news 'fixture:repair' "$meta")
if [[ "$out" != *'A bridge is more than just a structure.'* ]] &&
	[[ "$out" != *'This is a simple test sentence.'* ]] &&
	[[ "$out" == *'日本語の文に置き換えます。'* ]] &&
	grep -q '^spans=2$' "$meta"; then
	ok 'multiple spans are replaced from the end without offset corruption'
else
	not_ok 'multiple spans are replaced from the end without offset corruption'
fi

REPAIR_CALLS=0
RADIO_QUALITY_REPAIR_MAX_SPANS=1
meta="$TMP/preflight.meta"
if _radio_repair_mixed_language "$input" news 'fixture:repair' "$meta" >"$TMP/preflight.out" 2>/dev/null; then
	not_ok 'span limits reject before any local repair call'
elif [ "$REPAIR_CALLS" -eq 0 ] &&
	grep -q '^status=too_many_spans$' "$meta" && grep -q '^spans=2$' "$meta"; then
	ok 'span limits reject before any local repair call'
else
	not_ok 'span limits reject before any local repair call'
fi
unset RADIO_QUALITY_REPAIR_MAX_SPANS

input='こんばんは。The price is 2026 dollars. これは日本語の本文です。'
REPAIR_OUTPUT='価格は2025ドルです。'
meta="$TMP/protected.meta"
if _radio_repair_mixed_language "$input" news 'fixture:repair' "$meta" >/dev/null 2>&1; then
	not_ok 'a replacement that changes a protected number is rejected'
else
	ok 'a replacement that changes a protected number is rejected'
fi

exit "$FAIL"
