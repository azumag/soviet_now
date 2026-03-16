#!/bin/bash
# COEIROINK v2 TTS wrapper
# Usage:
#   ./coeiroink_tts.sh --speakers          # 話者一覧表示
#   ./coeiroink_tts.sh --test              # テスト音声生成+再生
#   ./coeiroink_tts.sh "テキスト"          # テキストを音声合成+再生
#   ./coeiroink_tts.sh -o out.wav "テキスト"  # ファイル出力

COEIROINK_URL="${COEIROINK_URL:-http://localhost:50033}"

# デフォルト話者 (つくよみちゃん)
SPEAKER_UUID="${SPEAKER_UUID:-3c37646f-3881-5374-2a83-149267990abc}"
STYLE_ID="${STYLE_ID:-0}"

check_server() {
    if ! curl -s --max-time 2 "$COEIROINK_URL/v1/speakers" > /dev/null 2>&1; then
        echo "ERROR: COEIROINK engine is not running at $COEIROINK_URL" >&2
        echo "Start it with: cd /Volumes/satelite/work_satelite/coeiroink && docker compose up -d" >&2
        return 1
    fi
}

show_speakers() {
    check_server || return 1
    curl -s "$COEIROINK_URL/v1/speakers" | python3 -m json.tool
}

synthesize() {
    local text="$1"
    local output="$2"

    check_server || return 1

    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
    'speakerUuid': '$SPEAKER_UUID',
    'styleId': $STYLE_ID,
    'text': sys.argv[1],
    'speedScale': 1.0,
    'volumeScale': 1.0,
    'pitchScale': 0.0,
    'intonationScale': 1.0,
    'outputSamplingRate': 48000
}))
" "$text")

    curl -s -X POST "$COEIROINK_URL/v1/synthesis" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        --output "$output"

    if [ $? -ne 0 ] || [ ! -s "$output" ]; then
        echo "ERROR: synthesis failed" >&2
        return 1
    fi
}

# --- main ---
OUTPUT=""
TEXT=""

case "${1:-}" in
    --speakers)
        show_speakers
        exit $?
        ;;
    --test)
        OUTPUT="/tmp/coeiroink_test.wav"
        TEXT="テスト音声です。COEIROINKが正常に動作しています。"
        synthesize "$TEXT" "$OUTPUT" || exit 1
        echo "Playing: $OUTPUT"
        afplay "$OUTPUT"
        exit 0
        ;;
    -o)
        OUTPUT="$2"
        shift 2
        TEXT="$*"
        ;;
    ""|--help|-h)
        echo "Usage:"
        echo "  $0 --speakers           話者一覧表示"
        echo "  $0 --test               テスト音声生成+再生"
        echo "  $0 \"テキスト\"            音声合成+再生"
        echo "  $0 -o out.wav \"テキスト\"  ファイル出力"
        echo ""
        echo "Environment variables:"
        echo "  COEIROINK_URL   (default: http://localhost:50033)"
        echo "  SPEAKER_UUID    (default: つくよみちゃん)"
        echo "  STYLE_ID        (default: 0)"
        exit 0
        ;;
    *)
        TEXT="$*"
        OUTPUT="/tmp/coeiroink_$$.wav"
        ;;
esac

if [ -z "$TEXT" ]; then
    echo "ERROR: no text specified" >&2
    exit 1
fi

synthesize "$TEXT" "$OUTPUT" || exit 1

if [ "$OUTPUT" = "/tmp/coeiroink_$$.wav" ]; then
    afplay "$OUTPUT"
    rm -f "$OUTPUT"
else
    echo "Saved: $OUTPUT"
fi
