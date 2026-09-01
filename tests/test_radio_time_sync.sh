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

qf_duplicate="$RADIO_DEFERRED_QUEUE_DIR/radio_2_3_news_4.txt"
cat >"$qf_duplicate" <<'EOF'
こんばんは、現在時刻は21時です。
本日のニュースです。
21時を回りました、まだ夜の入り口という時間帯です。
列車は午前3時に駅を出発しました。
EOF
_refresh_radio_intro_for_playback_file "$qf_duplicate" news hour
assert_eq 'おはようございます、現在時刻は5時です。' "$(sed -n '1p' "$qf_duplicate")" 'canonical intro is refreshed'
assert_eq '本日のニュースです。' "$(sed -n '2p' "$qf_duplicate")" 'corner announcement is preserved'
assert_eq '列車は午前3時に駅を出発しました。' "$(sed -n '3p' "$qf_duplicate")" 'stale generated clock claim is removed but factual event time is preserved'

qf_duplicate_jiji="$RADIO_DEFERRED_QUEUE_DIR/radio_3_4_jiji_5.txt"
cat >"$qf_duplicate_jiji" <<'EOF'
こんばんは、現在時刻は1時です。
時事ニュースコーナーです。
時事ニュースコーナーのお時間です。
深夜1時を回りましたが、こういう時間のニュースは生々しく感じます。
本文です。
EOF
_refresh_radio_intro_for_playback_file "$qf_duplicate_jiji" jiji hour
assert_eq '本文です。' "$(sed -n '4p' "$qf_duplicate_jiji")" 'duplicate clock after repeated corner announcements is removed'

qf_multiline="$RADIO_DEFERRED_QUEUE_DIR/radio_4_5_theme_6.txt"
cat >"$qf_multiline" <<'EOF'
こんばんは、現在時刻は21時です。
少し外の空気が重たく感じられます。
21時になりました。
21時を回りました。本日のニュースです。
午後3時台に地震が発生しました。
午前3時になって列車が駅を出発しました。
午後3時、街で大規模な停電が起きました。
21時、配信サービスで障害が起きました。
EOF
_refresh_radio_intro_for_playback_file "$qf_multiline" theme hour
if grep -q '21時になりました\|21時を回りました' "$qf_multiline"; then
	not_ok 'all generated clock claims in multiline opening are removed'
else
	ok 'all generated clock claims in multiline opening are removed'
fi
if grep -qF '本日のニュースです。' "$qf_multiline" &&
	grep -qF '午後3時台に地震が発生しました。' "$qf_multiline" &&
	grep -qF '午前3時になって列車が駅を出発しました。' "$qf_multiline" &&
	grep -qF '午後3時、街で大規模な停電が起きました。' "$qf_multiline" &&
	grep -qF '21時、配信サービスで障害が起きました。' "$qf_multiline"; then
	ok 'boilerplate and factual event times in opening lines are preserved'
else
	not_ok 'boilerplate and factual event times in opening lines are preserved'
fi

qf_factual="$RADIO_DEFERRED_QUEUE_DIR/radio_5_6_news_7.txt"
cat >"$qf_factual" <<'EOF'
こんばんは、現在時刻は21時です。
午後3時、街で大規模な停電が起きました。
21時、配信サービスで障害が起きました。
EOF
_refresh_radio_intro_for_playback_file "$qf_factual" news hour
assert_eq '午後3時、街で大規模な停電が起きました。' "$(sed -n '2p' "$qf_factual")" 'factual city event time is preserved as first body line'
assert_eq '21時、配信サービスで障害が起きました。' "$(sed -n '3p' "$qf_factual")" 'factual service event time is preserved in opening scan range'

qf_factual_words="$RADIO_DEFERRED_QUEUE_DIR/radio_6_7_news_8.txt"
cat >"$qf_factual_words" <<'EOF'
こんばんは、現在時刻は21時です。
正午に政府が記者会見を開きました。
真夜中に地震が発生しました。
日付が変わる直前に停電が起きました。
午後9時ですべての列車が運休しました。
EOF
_refresh_radio_intro_for_playback_file "$qf_factual_words" news hour
assert_eq '正午に政府が記者会見を開きました。' "$(sed -n '2p' "$qf_factual_words")" 'factual noon event is preserved'
assert_eq '真夜中に地震が発生しました。' "$(sed -n '3p' "$qf_factual_words")" 'factual midnight event is preserved'
assert_eq '日付が変わる直前に停電が起きました。' "$(sed -n '4p' "$qf_factual_words")" 'factual date-boundary event is preserved'
assert_eq '午後9時ですべての列車が運休しました。' "$(sed -n '5p' "$qf_factual_words")" 'clock-like prefix without predicate boundary is preserved'

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

qf_boundary="$RADIO_DEFERRED_QUEUE_DIR/radio_7_8_soviet_9.txt"
ready_boundary="$(_radio_ready_wav_path "$qf_boundary")"
bundle_boundary="$(_radio_ready_bundle_path "$qf_boundary")"
printf 'こんばんは、現在時刻は4時です。\n長い本文です。\n' >"$qf_boundary"
printf 'RIFF-boundary\n' >"$ready_boundary"
mkdir -p "$bundle_boundary"
printf 'boundary\n' >"$bundle_boundary/playlist.txt"
printf 'boundary\n' >"$bundle_boundary/captions.txt"
_radio_write_render_meta "$qf_boundary" "$(_radio_text_hash "$qf_boundary")"
_radio_ready_wav_crosses_hour_boundary() { return 0; }
_radio_sync_deferred_time_before_render "$qf_boundary" soviet
assert_eq 'おはようございます。' "$(head -n 1 "$qf_boundary")" 'hour-crossing audio omits clock while preserving greeting'
if [ ! -e "$ready_boundary" ] && [ ! -e "$bundle_boundary" ]; then
	ok 'hour-crossing rendered audio is invalidated for clock-free rerender'
else
	not_ok 'hour-crossing rendered audio is invalidated for clock-free rerender'
fi
unset -f _radio_ready_wav_crosses_hour_boundary
. "$ROOT/broadcast/radio_state.sh"

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
