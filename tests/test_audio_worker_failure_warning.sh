#!/usr/bin/env bash
# audio_worker が途中リトライではなく、最終結果の連続失敗だけを警告対象にすることを検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/workers/audio_worker.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

sed -n '/^_count_consecutive_terminal_say_failures()/,/^}/p' "$SRC" >"$TMP/function.sh"
. "$TMP/function.sh"

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }
log="$TMP/played.log"

cat >"$log" <<'EOF'
[10:00:00] failed [radio:news] one
[10:01:00] failed [radio:news] two
[10:02:00] played [radio:news] recovered
EOF
count=$(_count_consecutive_terminal_say_failures "$log")
[ "$count" -eq 0 ] \
	&& ok "later success clears historical failures" \
	|| not_ok "historical failures should be cleared by success (got $count)"

cat >>"$log" <<'EOF'
[10:03:00] failed [comment] three
[10:04:00] failed [comment] four
[10:05:00] failed [comment] five
EOF
count=$(_count_consecutive_terminal_say_failures "$log")
[ "$count" -eq 3 ] \
	&& ok "consecutive terminal failures are counted" \
	|| not_ok "expected 3 consecutive terminal failures (got $count)"

printf '[10:06:00] skipped_meta_failure [comment] invalid\n' >>"$log"
count=$(_count_consecutive_terminal_say_failures "$log")
[ "$count" -eq 0 ] \
	&& ok "non-failure terminal result resets warning streak" \
	|| not_ok "skipped terminal result should reset streak (got $count)"

exit "$FAIL"
