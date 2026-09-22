#!/usr/bin/env bash
# #829 PR-0b: 助言targetの決定的な誤配を防ぐ。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export ELOOP_LIB_DIR="$TMP"
export MAIN_STRATEGY_ADVICE_FILE="$TMP/main_advice.txt"
export SOREN91_STRATEGY_ADVICE_FILE="$TMP/soren91_advice.txt"
export COMMENT_ADVICE_FILE="$TMP/comment_advice.txt"
export CODEX_ADVICE_FILE="$TMP/codex_advice.txt"
mkdir -p "$TMP"

source "$ROOT/broadcast/comment.sh"
log() { :; }

ok=0
fail=0
check_equal() {
	local actual="$1" expected="$2" message="$3"
	if [ "$actual" = "$expected" ]; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s (actual=%s expected=%s)\n' "$message" "$actual" "$expected"
		fail=$((fail + 1))
	fi
}

check_contains() {
	local haystack="$1" needle="$2" message="$3"
	if [[ "$haystack" == *"$needle"* ]]; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

# Prefixは本文を整形する前に解決し、bareなnext/hold/順位/相手は
# fallbackの受付時modeを上書きしない。
check_equal "$(_detect_strategy_advice_target_mode "[soren91] nextを見て" main)" "soren91" \
	'明示[soren91] prefixはSoren91へ解決する'
check_equal "$(_detect_strategy_advice_target_mode "[main] nextを見て" soren91)" "main" \
	'明示[main] prefixはmainへ解決する'
check_equal "$(_detect_strategy_advice_target_mode "[main] Soren91ではなく本編" soren91)" "main" \
	'明示prefixは本文中のSoren91語より先に解決する'
check_equal "$(_detect_strategy_advice_target_mode "nextをふさがないで" main)" "main" \
	'bare nextはmain fallbackを上書きしない'
check_equal "$(_detect_strategy_advice_target_mode "nextnextを確認して" main)" "main" \
	'bare nextnextはmain fallbackを上書きしない'
check_equal "$(_detect_strategy_advice_target_mode "holdを使う" main)" "main" \
	'bare holdはmain fallbackを上書きしない'
check_equal "$(_detect_strategy_advice_target_mode "順位を確認して" main)" "main" \
	'bare 順位はmain fallbackを上書きしない'
check_equal "$(_detect_strategy_advice_target_mode "相手を見て" main)" "main" \
	'bare 相手はmain fallbackを上書きしない'

# 互換wrapperでもprefix解決後にstripされるため、[main] nextがSoren91へ漏れない。
_append_strategy_advice_item '[main] nextをふさがないで' main test_prefix 1
check_equal "$(grep -c -- '- nextをふさがないで' "$MAIN_STRATEGY_ADVICE_FILE" 2>/dev/null || true)" "1" \
	'prefix付きmain助言がmainファイルへ保存される'
check_equal "$(grep -c -- '- nextをふさがないで' "$SOREN91_STRATEGY_ADVICE_FILE" 2>/dev/null || printf '0')" "0" \
	'prefix付きmain助言がSoren91へ保存されない'

# 受付時に確定したtargetはappend側で再推定しない。

_append_strategy_advice_item_at_target 'holdを温存して' main test_resolved 2
check_equal "$(grep -c -- '- holdを温存して' "$MAIN_STRATEGY_ADVICE_FILE" 2>/dev/null || printf '0')" "1" \
	'確定済みmain targetはbare holdでもmainへ保存される'
check_equal "$(grep -c -- '- holdを温存して' "$SOREN91_STRATEGY_ADVICE_FILE" 2>/dev/null || printf '0')" "0" \
	'確定済みmain targetはSoren91へ再推定されない'

# structured intakeのmodeもbare語では変えず、明示prefixだけを尊重する。
batch="$TMP/comments.txt"
printf '%s\n' \
	'viewer: nextを確認して' \
	'viewer: [main] holdを温存して' \
	'viewer: [soren91] nextを確認して' >"$batch"
structured=$(_extract_structured_advice_from_comments "$batch" main)
check_contains "$structured" $'strategy\tmain\tviewer: nextを確認して' \
	'bare nextのstructured intakeはmain fallbackを維持する'
check_contains "$structured" $'strategy\tmain\tviewer: [main] holdを温存して' \
	'[main] prefixのstructured intakeはmainへ解決する'
check_contains "$structured" $'strategy\tsoren91\tviewer: [soren91] nextを確認して' \
	'[soren91] prefixのstructured intakeはSoren91へ解決する'

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
