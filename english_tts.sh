#!/bin/bash
# Lightweight offline English TTS using Flite. Produces a normalized mono WAV.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OUTPUT=""
INPUT_FILE=""
VOICE="${ENGLISH_TTS_VOICE:-slt}"
while [ "$#" -gt 0 ]; do
	case "$1" in
	-o)
		OUTPUT="${2:?english_tts.sh: -o requires a WAV path}"
		shift 2
		;;
	-f)
		INPUT_FILE="${2:?english_tts.sh: -f requires a text file}"
		shift 2
		;;
	--voice)
		VOICE="${2:?english_tts.sh: --voice requires a voice name}"
		shift 2
		;;
	*)
		echo "Usage: ./english_tts.sh -o output.wav -f input.txt [--voice slt]" >&2
		exit 2
		;;
	esac
done

if [ -z "$OUTPUT" ] || [ -z "$INPUT_FILE" ]; then
	echo "Usage: ./english_tts.sh -o output.wav -f input.txt [--voice slt]" >&2
	exit 2
fi
[ -s "$INPUT_FILE" ] || {
	echo "[english_tts] input file missing or empty: $INPUT_FILE" >&2
	exit 1
}

FLITE_BIN="${ENGLISH_TTS_FLITE_BIN:-flite}"
FFMPEG_BIN="${ENGLISH_TTS_FFMPEG_BIN:-ffmpeg}"
command -v "$FLITE_BIN" >/dev/null 2>&1 || {
	echo "[english_tts] Flite is not installed (Ubuntu: sudo apt-get install flite)" >&2
	exit 127
}
command -v "$FFMPEG_BIN" >/dev/null 2>&1 || {
	echo "[english_tts] ffmpeg is required to normalize the WAV output" >&2
	exit 127
}

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/soren-english-tts.XXXXXX")
RAW_WAV="$TEMP_DIR/flite.wav"
cleanup() {
	rm -f "$RAW_WAV" 2>/dev/null || true
	rmdir "$TEMP_DIR" 2>/dev/null || true
}
trap cleanup EXIT

flite_voices=$("$FLITE_BIN" -lv 2>/dev/null || true)
if [ -n "$VOICE" ] && printf '%s\n' "$flite_voices" | tr '[:space:]' '\n' | grep -qxF "$VOICE"; then
	"$FLITE_BIN" -voice "$VOICE" -f "$INPUT_FILE" -o "$RAW_WAV"
else
	[ -z "$VOICE" ] || echo "[english_tts] Flite voice '$VOICE' unavailable; using its default US English voice" >&2
	"$FLITE_BIN" -f "$INPUT_FILE" -o "$RAW_WAV"
fi

[ -s "$RAW_WAV" ] || {
	echo "[english_tts] Flite did not produce audio" >&2
	exit 1
}

mkdir -p "$(dirname "$OUTPUT")"
"$FFMPEG_BIN" -hide_banner -loglevel error -y \
	-i "$RAW_WAV" \
	-ar "${ENGLISH_TTS_SAMPLE_RATE:-24000}" -ac 1 -c:a pcm_s16le -f wav "$OUTPUT"
[ -s "$OUTPUT" ] || {
	echo "[english_tts] normalized WAV is empty: $OUTPUT" >&2
	exit 1
}
