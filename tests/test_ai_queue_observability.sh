#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export AI_STATS_DIR="$TMP/ai_stats"
export AI_GENERATION_QUEUE_ENABLED=1
export AI_GENERATION_QUEUE_LOCK_DIR="$TMP/generation_lock"
export AI_GENERATION_QUEUE_WAIT_SEC=1

log() { printf '[test] %s\n' "$*" >&2; }
source "$ROOT/core/helpers.sh"
source "$ROOT/lib/ai_generate.sh"
source "$ROOT/lib/ai_queue_observability.sh"

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

check '[ "$(_ai_queue_observability_holder_category "RADIO:x:prepass:remote:vercel:m")" = "radio_prepass" ]' 'prepass holder is fixed radio_prepass category'
check '[ "$(_ai_queue_observability_holder_category "RADIO:main:remote:vercel:m")" = "radio_main" ]' 'radio holder is fixed radio_main category'
check '[ "$(_ai_queue_observability_holder_category "NEWS:spam_check:remote:vercel:m")" = "news" ]' 'news holder is fixed news category'
check '[ "$(_ai_queue_observability_holder_category "JIJI:research:remote:vercel:m")" = "jiji" ]' 'jiji holder is fixed jiji category'
check '[ "$(_ai_queue_observability_holder_category "CELEBRATION:x:remote:vercel:m")" = "celebration" ]' 'celebration holder is fixed celebration category'
check '[ "$(_ai_queue_observability_holder_category "COMMENT:x")" = "other" ]' 'non-radio holder is fixed other category'
check '[ "$(_ai_queue_observability_holder_category "")" = "unknown" ]' 'missing holder is fixed unknown category'

mkdir -p "$AI_GENERATION_QUEUE_LOCK_DIR"
printf 'token=busy-holder\npid=%s\nlabel=RADIO:main:remote:vercel:TOP-SECRET-SENTINEL\n' "$$" >"$AI_GENERATION_QUEUE_LOCK_DIR/owner"
(
	export AI_GENERATION_QUEUE_MAX_WAIT_SEC=1
	_ai_generation_queue_enter "RADIO:request:remote:codex:model" >/dev/null 2>&1
)
rc=$?
check '[ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]' 'bounded queue still returns queue giveup rc'
stats_file=$(ls "$AI_STATS_DIR/"*.jsonl 2>/dev/null | head -n 1)
check '[ -n "$stats_file" ]' 'queue giveup detail writes ai_stats record'
if [ -n "$stats_file" ]; then
	check 'grep -q '"'"'"event":"queue_giveup_detail"'"'"' "$stats_file"' 'detail event uses dedicated fixed event name'
	check 'grep -Eq '"'"'wait=[1-9][0-9]*;holder=radio_main'"'"' "$stats_file"' 'detail contains capped wait and fixed holder category'
	check '! grep -q '"'"'TOP-SECRET-SENTINEL'"'"' "$stats_file"' 'raw owner label is not copied into stats'
	check '! grep -Eq '"'"'vercel|codex'"'"' "$stats_file"' 'detail record does not expose provider identifiers'
fi

rm -rf "$AI_GENERATION_QUEUE_LOCK_DIR"
mkdir -p "$AI_GENERATION_QUEUE_LOCK_DIR"
(
	export AI_GENERATION_QUEUE_MAX_WAIT_SEC=1
	_ai_generation_queue_enter "RADIO:request:remote:codex:model" >/dev/null 2>&1
)
rc=$?
check '[ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]' 'missing owner metadata still fails closed with queue giveup'
if [ -n "$stats_file" ]; then
	check '[ "$(grep -c '"'"'"event":"queue_giveup_detail"'"'"' "$stats_file")" -eq 2 ]' 'one detail event is emitted per giveup'
	check 'tail -n 1 "$stats_file" | grep -q '"'"'holder=unknown'"'"'' 'missing owner metadata is categorized as unknown'
fi

rm -rf "$AI_GENERATION_QUEUE_LOCK_DIR"
_ai_generation_queue_enter "RADIO:success" >/dev/null 2>&1
success_rc=$?
token="$AI_GENERATION_QUEUE_LAST_TOKEN"
_ai_generation_queue_leave "$token" "RADIO:success" >/dev/null 2>&1 || true
check '[ "$success_rc" -eq 0 ]' 'successful acquisition behavior is unchanged'
if [ -n "$stats_file" ]; then
	check '[ "$(grep -c '"'"'"event":"queue_giveup_detail"'"'"' "$stats_file")" -eq 2 ]' 'successful acquisition emits no giveup detail'
fi

printf '\n%d ok, %d not ok\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
