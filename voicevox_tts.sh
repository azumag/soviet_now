#!/bin/bash
# VOICEVOX TTS wrapper
# Usage:
#   ./voicevox_tts.sh --speakers          # 話者一覧表示
#   ./voicevox_tts.sh --test              # テスト音声生成+再生
#   ./voicevox_tts.sh "テキスト"          # テキストを音声合成+再生
#   ./voicevox_tts.sh -o out.wav "テキスト"  # ファイル出力

VOICEVOX_URL="${VOICEVOX_URL:-http://127.0.0.1:50021}"
VOICEVOX_SPEAKER="${VOICEVOX_SPEAKER:-3}"  # デフォルト: ずんだもん ノーマル
VOICEVOX_TIMEOUT="${VOICEVOX_TIMEOUT:-30}"

check_server() {
    if ! curl -s --max-time 2 "$VOICEVOX_URL/speakers" > /dev/null 2>&1; then
        echo "ERROR: VOICEVOX engine is not running at $VOICEVOX_URL" >&2
        return 1
    fi
}

show_speakers() {
    check_server || return 1
    curl -s "$VOICEVOX_URL/speakers" | python3 -c "
import json, sys
speakers = json.load(sys.stdin)
for s in speakers:
    print(f\"{s['name']}\")
    for st in s.get('styles', []):
        print(f\"  [{st['id']}] {st['name']}\")
"
}

synthesize() {
    local text="$1"
    local output="$2"

    check_server || return 1

    # Step 1: audio_query
    local query_json http_code
    query_json=$(curl -s --max-time "$VOICEVOX_TIMEOUT" \
        -X POST "$VOICEVOX_URL/audio_query" \
        --get --data-urlencode "text=$text" \
        --data-urlencode "speaker=$VOICEVOX_SPEAKER" \
        -H "Content-Type: application/json")

    if [ -z "$query_json" ] || echo "$query_json" | grep -q '"detail"'; then
        echo "ERROR: audio_query failed" >&2
        return 1
    fi

    # Step 2: synthesis
    http_code=$(curl -s --max-time "$VOICEVOX_TIMEOUT" \
        -X POST "$VOICEVOX_URL/synthesis?speaker=$VOICEVOX_SPEAKER" \
        -H "Content-Type: application/json" \
        -d "$query_json" \
        --output "$output" \
        -w '%{http_code}')

    if [ "$http_code" != "200" ] || [ ! -s "$output" ]; then
        echo "ERROR: synthesis failed (HTTP $http_code)" >&2
        rm -f "$output" 2>/dev/null
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
        OUTPUT="/tmp/voicevox_test.wav"
        TEXT="テスト音声です。VOICEVOXが正常に動作しています。"
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
        echo "  VOICEVOX_URL      (default: http://127.0.0.1:50021)"
        echo "  VOICEVOX_SPEAKER  (default: 3 = ずんだもん ノーマル)"
        exit 0
        ;;
    *)
        TEXT="$*"
        OUTPUT="/tmp/voicevox_$$.wav"
        ;;
esac

if [ -z "$TEXT" ]; then
    echo "ERROR: no text specified" >&2
    exit 1
fi

synthesize "$TEXT" "$OUTPUT" || exit 1

if [ "$OUTPUT" = "/tmp/voicevox_$$.wav" ]; then
    afplay "$OUTPUT"
    rm -f "$OUTPUT"
else
    echo "Saved: $OUTPUT"
fi
