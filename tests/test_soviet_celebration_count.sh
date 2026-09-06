#!/usr/bin/env bash
# tests/test_soviet_celebration_count.sh - ソ連建国祝賀の回数加味
#
# - _soviet_founding_past_events が archive + runtime の日付distinct数を返す
#   (同一日の重複行=凍結盤面の再検出スパムは1件に畳む)
# - _soviet_founding_ordinal が 0→初めて / 1→2度目 / 2→3度目を返す
# - prompts/celebration.md が序数プレースホルダと一人称規定を持つ

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

# shellcheck source=/dev/null
source "$ROOT/broadcast/radio_celebration.sh"

# --- fixture: archive 1件 (06-03) + runtime 同日8件スパム (09-06) ---
FIX_ARCHIVE="$TMP/archive.tsv"
FIX_RUNTIME="$TMP/runtime.tsv"
printf '2026-06-03T06:26:52+09:00\t2026-06-03 06:26 JST\t29557\t6527\t\n' >"$FIX_ARCHIVE"
: >"$FIX_RUNTIME"
g=49106
while [ "$g" -le 49113 ]; do
	printf '2026-09-06T08:07:16+09:00\t2026-09-06 08:07 JST\t%s\t5312\t1\n' "$g" >>"$FIX_RUNTIME"
	g=$((g + 1))
done

info=$(SOVIET_CREATION_ARCHIVE_FILE="$FIX_ARCHIVE" SOVIET_CREATION_HISTORY_FILE="$FIX_RUNTIME" _soviet_founding_past_events)
past_count=$(printf '%s' "$info" | sed -n '1p')
past_days=$(printf '%s' "$info" | sed -n '2p')
check "[ \"$past_count\" = \"2\" ]" "同日8件スパムは1件に畳み、過去件数=2 (実測: $past_count)"
check "[ \"$past_days\" = \"2026-06-03,2026-09-06\" ]" "過去日付列が古い順 (実測: $past_days)"

# --- runtime欠落時はarchiveのみ ---
info2=$(SOVIET_CREATION_ARCHIVE_FILE="$FIX_ARCHIVE" SOVIET_CREATION_HISTORY_FILE="$TMP/nonexistent.tsv" _soviet_founding_past_events)
check "[ \"\$(printf '%s' \"\$info2\" | sed -n '1p')\" = \"1\" ]" "runtime欠落時はarchiveのみ数える"

# --- 両方欠落時は0 ---
info0=$(SOVIET_CREATION_ARCHIVE_FILE="$TMP/no1.tsv" SOVIET_CREATION_HISTORY_FILE="$TMP/no2.tsv" _soviet_founding_past_events)
check "[ \"\$(printf '%s' \"\$info0\" | sed -n '1p')\" = \"0\" ]" "履歴なしは0件"

# --- 序数 ---
check "[ \"\$(_soviet_founding_ordinal 0)\" = \"初めて\" ]" "0件→初めて"
check "[ \"\$(_soviet_founding_ordinal 1)\" = \"2度目\" ]" "1件→2度目"
check "[ \"\$(_soviet_founding_ordinal 2)\" = \"3度目\" ]" "2件→3度目 (次回建国)"
check "[ \"\$(_soviet_founding_ordinal 5)\" = \"6度目\" ]" "5件→6度目"

# --- prompts/celebration.md の同期 ---
check 'grep -q "founding_ordinal" "$ROOT/prompts/celebration.md"' 'prompts/celebration.mdに序数プレースホルダがある'
check 'grep -q "一人称は「私」を使うこと。「僕」「俺」「自分」は使わない" "$ROOT/prompts/celebration.md"' 'prompts/celebration.mdの一人称規定を維持'

# --- 実archiveは現時点で2件 (06-03 + 09-06) → 次回は3度目 ---
real_info=$(SOVIET_CREATION_ARCHIVE_FILE="$ROOT/data/soviet_creation_history.tsv" SOVIET_CREATION_HISTORY_FILE="$TMP/nonexistent.tsv" _soviet_founding_past_events)
real_count=$(printf '%s' "$real_info" | sed -n '1p')
check "[ \"$real_count\" = \"2\" ]" "実archiveは2件 (実測: $real_count)"
check "[ \"\$(_soviet_founding_ordinal \"$real_count\")\" = \"3度目\" ]" "次回建国は3度目になる"

printf '1..%s\n' "$((ok + fail))"
printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
