#!/bin/bash
# Render ordered English (Flite) and Japanese (VOICEVOX) comment segments to one WAV.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OUTPUT=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	-o)
		OUTPUT="${2:?bilingual_comment_tts.sh: -o requires a WAV path}"
		shift 2
		;;
	*) break ;;
	esac
done

METADATA_FILE="${1:-}"
RATE="${2:-120}"
if [ -z "$OUTPUT" ] || [ ! -s "$METADATA_FILE" ]; then
	echo "Usage: ./bilingual_comment_tts.sh -o output.wav metadata.json [rate]" >&2
	exit 2
fi

PYTHON_BIN="${BILINGUAL_TTS_PYTHON_BIN:-python3}"
FFMPEG_BIN="${BILINGUAL_TTS_FFMPEG_BIN:-ffmpeg}"
ENGLISH_TTS_SCRIPT="${BILINGUAL_TTS_ENGLISH_SCRIPT:-$SCRIPT_DIR/english_tts.sh}"
SAY_ENQUEUE_SCRIPT="${BILINGUAL_TTS_SAY_SCRIPT:-$SCRIPT_DIR/say_enqueue.sh}"
COMMENT_BILINGUAL_SCRIPT="${BILINGUAL_TTS_HELPER:-$SCRIPT_DIR/lib/comment_bilingual.py}"

for required in "$PYTHON_BIN" "$FFMPEG_BIN"; do
	command -v "$required" >/dev/null 2>&1 || {
		echo "[bilingual_tts] missing command: $required" >&2
		exit 127
	}
done
[ -x "$ENGLISH_TTS_SCRIPT" ] || {
	echo "[bilingual_tts] English TTS script is not executable: $ENGLISH_TTS_SCRIPT" >&2
	exit 1
}
[ -x "$SAY_ENQUEUE_SCRIPT" ] || {
	echo "[bilingual_tts] say queue script is not executable: $SAY_ENQUEUE_SCRIPT" >&2
	exit 1
}
[ -f "$COMMENT_BILINGUAL_SCRIPT" ] || {
	echo "[bilingual_tts] bilingual helper is missing: $COMMENT_BILINGUAL_SCRIPT" >&2
	exit 1
}

TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/soren-bilingual-tts.XXXXXX")
MANIFEST="$TEMP_DIR/segments.tsv"
PLAYLIST="$TEMP_DIR/playlist.txt"
cleanup() {
	local cleanup_file
	for cleanup_file in "$TEMP_DIR"/*; do
		[ -e "$cleanup_file" ] || continue
		rm -f "$cleanup_file" 2>/dev/null || true
	done
	rmdir "$TEMP_DIR" 2>/dev/null || true
}
trap cleanup EXIT

"$PYTHON_BIN" "$COMMENT_BILINGUAL_SCRIPT" emit-segments "$METADATA_FILE" "$TEMP_DIR" >"$MANIFEST"
: >"$PLAYLIST"

segment_count=0
while IFS=$'\t' read -r language text_file; do
	if [ -z "$language" ] || [ ! -s "$text_file" ]; then
		continue
	fi
	raw_wav="$TEMP_DIR/raw_${segment_count}.wav"
	normalized_wav="$TEMP_DIR/normalized_${segment_count}.wav"
	case "$language" in
	en)
		"$ENGLISH_TTS_SCRIPT" -o "$raw_wav" -f "$text_file"
		;;
	ja)
		SAY_CONTEXT_LABEL="${SAY_CONTEXT_LABEL:-comment}:ja-render" \
		SAY_DISABLE_COMMENT_YIELD=1 \
		"$SAY_ENQUEUE_SCRIPT" --render-only "$raw_wav" "$text_file" "$RATE" 0
		;;
	*)
		echo "[bilingual_tts] unsupported language: $language" >&2
		exit 1
		;;
	esac
	[ -s "$raw_wav" ] || {
		echo "[bilingual_tts] synthesis produced no audio for segment $segment_count ($language)" >&2
		exit 1
	}
	"$FFMPEG_BIN" -hide_banner -loglevel error -y \
		-i "$raw_wav" -ar 24000 -ac 1 -c:a pcm_s16le -f wav "$normalized_wav"
	[ -s "$normalized_wav" ] || {
		echo "[bilingual_tts] normalization failed for segment $segment_count ($language)" >&2
		exit 1
	}
	printf "file '%s'\n" "$normalized_wav" >>"$PLAYLIST"
	segment_count=$((segment_count + 1))
done <"$MANIFEST"

[ "$segment_count" -gt 0 ] || {
	echo "[bilingual_tts] no speech segments found" >&2
	exit 1
}

mkdir -p "$(dirname "$OUTPUT")"
if [ "$segment_count" -eq 1 ]; then
	first_wav=$(sed -n "s/^file '\(.*\)'$/\1/p" "$PLAYLIST" | head -n1)
	cp "$first_wav" "$OUTPUT"
else
	"$FFMPEG_BIN" -hide_banner -loglevel error -y \
		-f concat -safe 0 -i "$PLAYLIST" -ar 24000 -ac 1 -c:a pcm_s16le -f wav "$OUTPUT"
fi
[ -s "$OUTPUT" ] || {
	echo "[bilingual_tts] final WAV is empty: $OUTPUT" >&2
	exit 1
}
