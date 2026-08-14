#!/usr/bin/env bash
# Deferred radio の音声生成失敗が短周期ループにならず、指数バックオフされることを検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/radio_state.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

for fn in \
	_radio_audio_base_path \
	_radio_render_retry_path \
	_radio_render_retry_waiting \
	_radio_schedule_deferred_render_retry \
	_radio_clear_deferred_render_retry; do
	sed -n "/^${fn}()/,/^}/p" "$SRC" >>"$TMP/functions.sh"
done
. "$TMP/functions.sh"

export RADIO_RENDER_RETRY_BASE_SEC=2
export RADIO_RENDER_RETRY_MAX_SEC=5
qf="$TMP/radio_100_1_news_1.txt"
printf 'radio\n' >"$qf"

read -r count delay retry_at <<<"$(_radio_schedule_deferred_render_retry "$qf")"
if [ "$count" -eq 1 ] && [ "$delay" -eq 2 ]; then
	ok "first render retry waits for base delay"
else
	not_ok "first retry should be count=1 delay=2 (got count=$count delay=$delay)"
fi
if _radio_render_retry_waiting "$qf"; then
	ok "retry gate blocks immediate relaunch"
else
	not_ok "retry gate should block immediate relaunch"
fi

printf '1 %s\n' "$(($(date +%s) - 1))" >"$(_radio_render_retry_path "$qf")"
if _radio_render_retry_waiting "$qf"; then
	not_ok "expired retry gate should allow relaunch"
else
	ok "expired retry gate allows relaunch"
fi

read -r count delay retry_at <<<"$(_radio_schedule_deferred_render_retry "$qf")"
[ "$count" -eq 2 ] && [ "$delay" -eq 4 ] \
	&& ok "second retry doubles delay" \
	|| not_ok "second retry should be count=2 delay=4 (got count=$count delay=$delay)"
read -r count delay retry_at <<<"$(_radio_schedule_deferred_render_retry "$qf")"
[ "$count" -eq 3 ] && [ "$delay" -eq 5 ] \
	&& ok "retry delay is capped" \
	|| not_ok "third retry should be capped at 5s (got count=$count delay=$delay)"

_radio_clear_deferred_render_retry "$qf"
if [ ! -e "$(_radio_render_retry_path "$qf")" ]; then
	ok "successful render can clear retry state"
else
	not_ok "retry state should be cleared"
fi

exit "$FAIL"
