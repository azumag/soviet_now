#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

ok=0
fail=0
pass() { echo "ok - $1"; ok=$((ok + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

# Isolated base functions: the policy must wrap, not replace, the established
# generation and validation contracts.
_append_comment_reply_contract() { printf '%s\n' 'BASE_CONTRACT' >>"$1"; }
_is_valid_comment_talk() { return 0; }
generate_comment_response() { printf '%s\n' "base:${1:-twitch}" >>"$TMP/generated"; }

source broadcast/comment_runtime_policy.sh
# Re-sourcing alone must not recursively wrap our own wrappers.
source broadcast/comment_runtime_policy.sh

prompt="$TMP/prompt.txt"
: >"$prompt"
_append_comment_reply_contract "$prompt"
grep -qF '必ず「同志○○」' "$prompt" && pass 'final contract requires comrade viewer address' || not_ok 'comrade address contract missing'
grep -qF '同じ視聴者による連続したカードガチャ獲得通知だけで2件以上' "$prompt" && pass 'final contract allows same-viewer card burst consolidation' || not_ok 'card consolidation contract missing'
grep -qF 'カード通知と通常コメントが混在する場合' "$prompt" && pass 'mixed batches keep one-comment ordering' || not_ok 'mixed-batch safety contract missing'
[ "$(grep -c '^BASE_CONTRACT$' "$prompt")" -eq 1 ] && pass 'base reply contract runs exactly once after re-source' || not_ok 'base reply contract wrapped recursively'

if _is_valid_comment_talk '同志alice、ありがとうございます。'; then
	pass '同志alice address is accepted'
else
	not_ok '同志alice address was rejected'
fi
if _is_valid_comment_talk 'aliceさん、ありがとうございます。'; then
	not_ok 'plain -san viewer address was accepted'
else
	pass 'plain -san viewer address is rejected for regeneration'
fi
if _is_valid_comment_talk 'みなさん、ありがとうございます。'; then
	pass 'generic audience phrase is not mistaken for individual address'
else
	not_ok 'generic audience phrase was rejected'
fi

# Test the debounce algorithm without wall-clock sleeps/network. The fake
# fetch emits one card on the first poll and a second draw on the next poll;
# the quiet window must keep polling until the burst settles.
export YOUTUBE_CHAT_OUTFILE="$TMP/youtube_comments.txt"
_calls="$TMP/calls"
_clock="$TMP/clock"
printf '0\n' >"$_calls"
printf '0\n' >"$_clock"
_comment_debounce_fetch() {
	local n
	n=$(cat "$_calls")
	n=$((n + 1))
	printf '%s\n' "$n" >"$_calls"
	case "$n" in
	1) printf '%s\n' 'dociai: alice が【カードA】赤いカードを獲得しました' >"$YOUTUBE_CHAT_OUTFILE" ;;
	2) printf '%s\n' 'dociai: alice が【カードB】青いカードを獲得しました' >>"$YOUTUBE_CHAT_OUTFILE" ;;
	esac
}
_comment_debounce_now() {
	local n
	n=$(cat "$_clock")
	n=$((n + 1))
	printf '%s\n' "$n" >"$_clock"
	printf '%s\n' "$n"
}
_comment_debounce_sleep() { :; }
COMMENT_DEBOUNCE_SEC=1 COMMENT_CARD_DEBOUNCE_SEC=2 COMMENT_DEBOUNCE_MAX_SEC=6 \
	_comment_debounce_wait youtube
[ "$(wc -l <"$YOUTUBE_CHAT_OUTFILE" | tr -d ' ')" -eq 2 ] && pass 'card burst arrivals are accumulated before generation' || not_ok 'card burst was not accumulated'
[ "$(cat "$_calls")" -ge 3 ] && pass 'new arrival resets bounded quiet window' || not_ok 'debounce stopped before quiet window settled'

# Wrapper still invokes the original generator exactly once after debounce.
_comment_debounce_wait() { return 0; }
: >"$TMP/generated"
generate_comment_response youtube
[ "$(cat "$TMP/generated")" = 'base:youtube' ] && pass 'debounced wrapper calls base generator once' || not_ok 'generator wrapper did not preserve base call'

# Static integration: eloop_lib must source the policy after comment.sh.
comment_line=$(grep -n 'broadcast/comment.sh' eloop_lib.sh | head -1 | cut -d: -f1)
policy_line=$(grep -n 'broadcast/comment_runtime_policy.sh' eloop_lib.sh | head -1 | cut -d: -f1)
if [ -n "$comment_line" ] && [ -n "$policy_line" ] && [ "$policy_line" -gt "$comment_line" ]; then
	pass 'runtime policy loads after concrete comment implementation'
else
	not_ok 'runtime policy source ordering is unsafe'
fi

echo "$ok ok / $fail failed"
[ "$fail" -eq 0 ]
