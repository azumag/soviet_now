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
# generation, country-normalization and validation contracts.
_append_comment_reply_contract() { printf '%s\n' 'BASE_CONTRACT' >>"$1"; }
_is_valid_comment_talk() { return 0; }
_comment_replace_country_references() { cat; }
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
for honorific in さん 様 くん ちゃん; do
	if _is_valid_comment_talk "alice${honorific}、ありがとうございます。"; then
		not_ok "plain ${honorific} viewer address was accepted"
	else
		pass "plain ${honorific} viewer address is rejected when unresolved"
	fi
done
if _is_valid_comment_talk 'みなさん、ありがとうございます。'; then
	pass 'generic audience phrase is not mistaken for individual address'
else
	not_ok 'generic audience phrase was rejected'
fi

# Known viewer addresses are repaired deterministically before validation, so a
# local honorific mistake does not spend another LLM generation attempt.
comment_batch_file="$TMP/comment_batch.txt"
cat >"$comment_batch_file" <<'EOF'
alice: こんにちは
takaさん: こんばんは
dociai: carol が【カードA】赤いカードを獲得しました
EOF
repair_input=$'aliceさん、ありがとう。\n\ntakaさん、こんばんは。\n\ncarol様、カードおめでとう。'
repair_output=$(printf '%s' "$repair_input" | _comment_replace_country_references)
if printf '%s' "$repair_output" | grep -qF '同志alice、ありがとう。'; then
	pass 'known viewer -san address is repaired without regeneration'
else
	not_ok 'known viewer -san address was not repaired'
fi
if printf '%s' "$repair_output" | grep -qF '同志takaさん、こんばんは。'; then
	pass 'viewer name that already ends in さん is preserved exactly'
else
	not_ok 'viewer name ending in さん was truncated or changed'
fi
if printf '%s' "$repair_output" | grep -qF '同志carol、カードおめでとう。'; then
	pass 'card recipient address is repaired instead of bot poster name'
else
	not_ok 'card recipient address was not repaired'
fi
if _is_valid_comment_talk "$repair_output"; then
	pass 'locally repaired reply passes final validator'
else
	not_ok 'locally repaired reply still triggers full regeneration'
fi

# Exact known names and @mentions at paragraph start also need 同志; repair them
# locally even when the model omitted an honorific entirely.
plain_output=$(printf '%s' $'alice、了解です。\n\n@alice：ありがとう。' | _comment_replace_country_references)
[ "$(printf '%s' "$plain_output" | grep -c '同志alice')" -eq 2 ] && pass 'plain known-name addresses are locally prefixed with 同志' || not_ok 'plain known-name repair failed'

# Unknown honorific addresses are not guessed. They remain unchanged and the
# validator preserves the existing full-regeneration fallback.
unknown_output=$(printf '%s' 'bob様、こんにちは。' | _comment_replace_country_references)
if [ "$unknown_output" = 'bob様、こんにちは。' ] && ! _is_valid_comment_talk "$unknown_output"; then
	pass 'unknown honorific address stays fail-closed for regeneration'
else
	not_ok 'unknown honorific address was guessed or accepted'
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
: >"$YOUTUBE_CHAT_OUTFILE"
: >"$TMP/generated"
generate_comment_response youtube
[ "$(cat "$TMP/generated")" = 'base:youtube' ] && pass 'debounced wrapper calls base generator once' || not_ok 'generator wrapper did not preserve base call'

# Card detection must accept Twica's live ASCII "[tier]" format and the
# "N連ガチャ" summary, not just the legacy fullwidth "【tier】" form.
_gacha_ascii="$TMP/gacha_ascii.txt"
printf '%s\n' '@もやしちゃん が [コモン] 一ルーブル札 を獲得しました. Lv.2 - 83 種類中 20 種類所持 素材: X / Public Domain 出典・権利確認:  シリーズ: 同志の心得' >"$_gacha_ascii"
_comment_debounce_is_card_batch "$_gacha_ascii" && pass 'ascii-bracket card notification is detected' || not_ok 'ascii-bracket card notification was missed'
_gacha_multi="$TMP/gacha_multi.txt"
printf '%s\n' '@ふぉくし_ が12連ガチャで エピックx1、レアx1、コモンx10 を獲得しました!一ドル札、クレジットカード' >"$_gacha_multi"
_comment_debounce_is_card_batch "$_gacha_multi" && pass 'multi-draw summary is detected as a card batch' || not_ok 'multi-draw summary was missed'

# Consolidation key: card-only same viewer yields a key; mixed content or
# multiple viewers must not consolidate.
_gacha_batch="$TMP/gacha_batch.txt"
printf '%s\n' '@alice が [コモン] カードA を獲得しました. Lv.1' '@alice が [レア] カードB を獲得しました. Lv.2' >"$_gacha_batch"
[ "$(_comment_debounce_card_consolidation_key "$_gacha_batch")" = "alice" ] && pass 'same-viewer card-only batch yields a consolidation key' || not_ok 'same-viewer card batch key missing'
printf '%s\n' '@alice が [コモン] カードA を獲得しました. Lv.1' 'bob: こんにちは' >"$_gacha_batch"
[ -z "$(_comment_debounce_card_consolidation_key "$_gacha_batch")" ] && pass 'mixed batch yields no consolidation key' || not_ok 'mixed batch wrongly consolidated'
printf '%s\n' '@alice が [コモン] カードA を獲得しました. Lv.1' '@bob が [レア] カードB を獲得しました. Lv.2' >"$_gacha_batch"
[ -z "$(_comment_debounce_card_consolidation_key "$_gacha_batch")" ] && pass 'multi-viewer card batch yields no consolidation key' || not_ok 'multi-viewer batch wrongly consolidated'
printf '%s\n' '@ふぉくし_ が12連ガチャで エピックx1、レアx1、コモンx10 を獲得しました!一ドル札' '@ふぉくし_ が [コモン] 人民食堂 を獲得しました. Lv.1' >"$_gacha_batch"
[ "$(_comment_debounce_card_consolidation_key "$_gacha_batch")" = "ふぉくし_" ] && pass 'multi-draw summary and single draw share the recipient key' || not_ok 'recipient key mismatch for mixed card forms'

# Carry-over gate is non-blocking: it holds across worker ticks while the same
# viewer keeps drawing, then releases once the viewer stops, and it never
# blocks the worker loop (the wrapper returns and the next tick retries).
export COMMENT_CARD_HOLD_STATE_DIR="$TMP/holdstate"
COMMENT_CARD_CONSOLIDATE_QUIET_SEC=20
COMMENT_CARD_CONSOLIDATE_MAX_SEC=180
_gate_file="$TMP/gate_comments.txt"
printf '%s\n' 'dociai: @alice が [コモン] カードA を獲得しました. Lv.1' >"$_gate_file"
_comment_debounce_outfile() { printf '%s' "$_gate_file"; }
_gate_clock="$TMP/gate_clock"
printf '1000\n' >"$_gate_clock"
_comment_debounce_now() { cat "$_gate_clock"; }
if _comment_card_consolidation_gate twitch; then
	not_ok 'first card batch was not held'
else
	pass 'first card batch is held (no immediate reply)'
fi
printf '1050\n' >"$_gate_clock"
printf '%s\n' 'dociai: @alice が [レア] カードB を獲得しました. Lv.2' >>"$_gate_file"
if _comment_card_consolidation_gate twitch; then
	not_ok 'continued draw did not reset the hold'
else
	pass 'a new draw from the same viewer keeps the batch held'
fi
printf '1080\n' >"$_gate_clock"
if _comment_card_consolidation_gate twitch; then
	pass 'batch is released after the viewer stops drawing'
else
	not_ok 'batch was held past the quiet window'
fi
[ ! -f "$COMMENT_CARD_HOLD_STATE_DIR/comment_card_hold_twitch" ] && pass 'hold state is cleared before generation' || not_ok 'hold state was not cleared'
# A hard ceiling still releases even while draws keep arriving.
printf '%s\n' 'dociai: @alice が [コモン] カードA を獲得しました. Lv.1' >"$_gate_file"
printf '2000\n' >"$_gate_clock"
_comment_card_consolidation_gate twitch >/dev/null 2>&1 || true
printf '2300\n' >"$_gate_clock"
printf '%s\n' 'dociai: @alice が [レア] カードB を獲得しました. Lv.2' >>"$_gate_file"
if _comment_card_consolidation_gate twitch; then
	pass 'hard ceiling releases a batch even while draws continue'
else
	not_ok 'hard ceiling did not release the batch'
fi

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
