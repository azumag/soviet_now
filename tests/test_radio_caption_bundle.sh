#!/usr/bin/env bash
# Deferred radio keeps the exact synthesized chunk boundaries for native CC.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SAY_SRC="$ROOT/say_enqueue.sh"
RADIO_SRC="$ROOT/broadcast/radio_state.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

sed -n '/^_export_prerendered_voicevox_bundle()/,/^}/p' "$SAY_SRC" >"$TMP/fn_bundle.sh"
. "$TMP/fn_bundle.sh"

MY_TOKEN="bundle_test"
mkdir -p "$TMP/source"
printf 'RIFF-one' >"$TMP/source/one.wav"
printf 'RIFF-two' >"$TMP/source/two.wav"
printf '%s\n' 'one.wav' 'two.wav' >"$TMP/source/playlist.txt"
printf '%s\n' '一つ目。' '二つ目。' >"$TMP/captions.txt"

if _export_prerendered_voicevox_bundle \
	"$TMP/source/playlist.txt" "$TMP/captions.txt" "$TMP/ready.bundle"; then
	ok "two aligned chunks export as one bundle"
else
	not_ok "aligned bundle export failed"
fi

if [ "$(sed -n '$=' "$TMP/ready.bundle/playlist.txt" 2>/dev/null)" = "2" ] \
	&& [ "$(sed -n '$=' "$TMP/ready.bundle/captions.txt" 2>/dev/null)" = "2" ] \
	&& [ -s "$TMP/ready.bundle/chunk_000.wav" ] \
	&& [ -s "$TMP/ready.bundle/chunk_001.wav" ]; then
	ok "bundle preserves audio-caption cardinality and relative WAV names"
else
	not_ok "bundle contents are incomplete"
fi

printf '%s\n' '一つ目だけ。' >"$TMP/mismatch.txt"
if _export_prerendered_voicevox_bundle \
	"$TMP/source/playlist.txt" "$TMP/mismatch.txt" "$TMP/bad.bundle"; then
	not_ok "mismatched audio-caption bundle should be rejected"
else
	ok "mismatched audio-caption bundle is rejected"
fi

if grep -q -- '--wav-playlist "$ready_bundle/playlist.txt" --caption-chunks "$ready_bundle/captions.txt"' "$RADIO_SRC"; then
	ok "deferred radio selects synchronized playlist playback"
else
	not_ok "deferred radio does not select synchronized playlist playback"
fi

if grep -q -- '--no-preempt --wav "$ready_wav"' "$RADIO_SRC"; then
	ok "pre-update ready WAV keeps a backward-compatible playback path"
else
	not_ok "legacy ready WAV fallback is missing"
fi

if grep -q '\[ "$RENDER_ONLY" != "true" \]' "$SAY_SRC"; then
	ok "render-only is excluded from the radio chunk cap"
else
	not_ok "render-only could still publish a truncated radio WAV"
fi

if grep -Fq '_pre_chunk_wav="$(pwd)/$_stream_dir/chunk_${_pc_i}.wav"' "$SAY_SRC"; then
	ok "locally synthesized playlists store absolute WAV paths"
else
	not_ok "local playlist paths can be resolved twice and break playback"
fi

if grep -Fq 'printf '"'"'%s\n'"'"' "$(pwd)/$PRE_SYNTH_WAV"' "$SAY_SRC"; then
	ok "single-chunk render playlists store absolute WAV paths"
else
	not_ok "single-chunk render playlists can double the relative path"
fi

exit "$FAIL"
