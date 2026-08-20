#!/usr/bin/env bash
# deferred ラジオの時報本文と事前生成音声が同じ世代で再生されることを検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }
assert_eq() {
	local expected="$1" actual="$2" label="$3"
	if [ "$expected" = "$actual" ]; then
		ok "$label"
	else
		not_ok "$label (expected=$expected actual=$actual)"
	fi
}

# 実時刻に依存せず、分境界のスナップショットと時報置換を固定する。
date() {
	if [ "${1:-}" = "+%H %M" ]; then
		printf '05 48\n'
	else
		command date "$@"
	fi
}

log() { :; }
RADIO_STATE_STALE_SEC=600
RADIO_TIME_SYNC_ENABLED=1
RADIO_TIME_ANNOUNCE_MINUTES=0
RADIO_DEFERRED_QUEUE_DIR="$TMP/queue"
mkdir -p "$RADIO_DEFERRED_QUEUE_DIR"

# 実コードの関数をそのまま読み込む。
. "$ROOT/broadcast/radio_persona.sh"
. "$ROOT/broadcast/radio_state.sh"

_radio_time_context
assert_eq '05' "$_rc_hour" 'single date snapshot preserves zero-padded hour'
assert_eq '05:48' "$_rc_time" 'single date snapshot preserves minute'
assert_eq '5時48分' "$_rc_time_spoken" 'prompt time keeps minute precision'
assert_eq '5時' "$_rc_time_announce_spoken" 'deferred announcement defaults to hour precision'

RADIO_TIME_ANNOUNCE_MINUTES=1
_radio_time_context
assert_eq '5時48分' "$_rc_time_announce_spoken" 'minute precision is opt-in'
RADIO_TIME_ANNOUNCE_MINUTES=0

qf="$RADIO_DEFERRED_QUEUE_DIR/radio_1_2_news_3.txt"
ready="$(_radio_ready_wav_path "$qf")"
bundle="$(_radio_ready_bundle_path "$qf")"
printf 'こんばんは、現在時刻は2時です。\n本文です。\n' >"$qf"
printf 'RIFF-old\n' >"$ready"
mkdir -p "$bundle"
printf 'old\n' >"$bundle/playlist.txt"
printf 'old\n' >"$bundle/captions.txt"
printf 'old-hash %s\n' "$(command date +%s)" >"$(_radio_render_meta_path "$qf")"

_radio_sync_deferred_time_before_render "$qf" news
assert_eq 'おはようございます、現在時刻は5時です。' "$(head -n 1 "$qf")" 'stale deferred intro is refreshed before render'
if [ ! -e "$ready" ] && [ ! -e "$bundle" ]; then
	ok 'stale ready WAV and bundle are invalidated together'
else
	not_ok 'stale ready WAV and bundle are invalidated together'
fi
if [ ! -e "$(_radio_render_meta_path "$qf")" ]; then
	ok 'stale render metadata is cleared'
else
	not_ok 'stale render metadata is cleared'
fi

printf 'RIFF-current\n' >"$ready"
mkdir -p "$bundle"
printf 'current\n' >"$bundle/playlist.txt"
printf 'current\n' >"$bundle/captions.txt"
_radio_write_render_meta "$qf" "$(_radio_text_hash "$qf")"
_radio_sync_deferred_time_before_render "$qf" news
if [ -s "$ready" ] && [ -s "$bundle/playlist.txt" ]; then
	ok 'matching render metadata keeps current ready audio'
else
	not_ok 'matching render metadata keeps current ready audio'
fi

rm -f "$(_radio_render_meta_path "$qf")"
printf 'RIFF-legacy\n' >"$ready"
_radio_sync_deferred_time_before_render "$qf" news
if [ ! -e "$ready" ]; then
	ok 'legacy ready audio without metadata is regenerated safely'
else
	not_ok 'legacy ready audio without metadata is regenerated safely'
fi

if grep -q '_refresh_radio_intro_for_playback_file "\$playing_file"' "$ROOT/broadcast/radio_state.sh"; then
	not_ok 'deferred playback does not mutate text after render'
else
	ok 'deferred playback does not mutate text after render'
fi
if grep -q '_radio_write_render_meta' "$ROOT/broadcast/radio_state.sh" &&
	grep -q 'RADIO_TIME_SYNC_ENABLED' "$ROOT/core/config.sh"; then
	ok 'render generation gate is wired to config'
else
	not_ok 'render generation gate is wired to config'
fi

exit "$FAIL"
