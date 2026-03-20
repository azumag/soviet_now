#!/bin/bash
# VOICEVOX 歌声合成ラッパー
# Usage:
#   ./voicevox_sing.sh --singers              # シンガー一覧
#   ./voicevox_sing.sh --test                 # テスト歌唱（デフォルト楽譜）
#   ./voicevox_sing.sh -o out.wav score.json  # 楽譜ファイルから合成
#   ./voicevox_sing.sh -o out.wav -           # stdin から楽譜JSON

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -z "${VOICEVOX_URL:-}" ] && [ -f "$SCRIPT_DIR/.env" ] && . "$SCRIPT_DIR/.env"
VOICEVOX_URL="${VOICEVOX_URL:-http://127.0.0.1:50021}"
VOICEVOX_SING_SPEAKER="${VOICEVOX_SING_SPEAKER:-6000}"  # 波音リツ (sing用)
VOICEVOX_SING_TIMEOUT="${VOICEVOX_SING_TIMEOUT:-120}"
DEFAULT_SCORE="$SCRIPT_DIR/data/songs/default.json"

check_server() {
    if ! curl -s --max-time 3 "$VOICEVOX_URL/singers" > /dev/null 2>&1; then
        echo "ERROR: VOICEVOX engine is not running or singing API unavailable at $VOICEVOX_URL" >&2
        return 1
    fi
}

show_singers() {
    check_server || return 1
    echo "=== Singers (sing_frame_audio_query 用) ==="
    curl -s "$VOICEVOX_URL/singers" | python3 -c "
import json, sys
singers = json.load(sys.stdin)
for s in singers:
    print(f\"{s['name']}\")
    for st in s.get('styles', []):
        t = st.get('type', 'unknown')
        print(f\"  [{st['id']}] {st['name']} (type={t})\")
"
    echo ""
    echo "=== Singers with frame_decode support (frame_synthesis 用) ==="
    curl -s "$VOICEVOX_URL/singers" | python3 -c "
import json, sys
singers = json.load(sys.stdin)
for s in singers:
    fd_styles = [st for st in s.get('styles', []) if st.get('type') == 'frame_decode']
    if fd_styles:
        print(f\"{s['name']}\")
        for st in fd_styles:
            print(f\"  [{st['id']}] {st['name']} (frame_decode)\")
"
}

# /singers から frame_decode 対応のスピーカーIDをランダム選択
pick_synth_speaker() {
    curl -s --max-time 5 "$VOICEVOX_URL/singers" | python3 -c "
import json, sys, random
singers = json.load(sys.stdin)
fd_ids = []
for s in singers:
    for st in s.get('styles', []):
        if st.get('type') == 'frame_decode':
            fd_ids.append(st['id'])
if not fd_ids:
    print('', end='')
    sys.exit(1)
print(random.choice(fd_ids), end='')
" 2>/dev/null
}

synthesize_song() {
    local score_file="$1"
    local output="$2"

    check_server || return 1

    # 楽譜JSONを読み込み
    local score_json
    if [ "$score_file" = "-" ]; then
        score_json=$(cat)
    else
        score_json=$(cat "$score_file")
    fi

    # バリデーション
    if ! echo "$score_json" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'notes' in d" 2>/dev/null; then
        echo "ERROR: invalid score JSON (missing 'notes' key)" >&2
        return 1
    fi

    # Step 1: sing_frame_audio_query
    echo "Generating frame audio query (speaker=$VOICEVOX_SING_SPEAKER)..." >&2
    local frame_query
    frame_query=$(curl -s --max-time "$VOICEVOX_SING_TIMEOUT" \
        -X POST "$VOICEVOX_URL/sing_frame_audio_query?speaker=$VOICEVOX_SING_SPEAKER" \
        -H "Content-Type: application/json" \
        -d "$score_json")

    if [ -z "$frame_query" ]; then
        echo "ERROR: sing_frame_audio_query returned empty response" >&2
        return 1
    fi
    if ! echo "$frame_query" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'f0' in d" 2>/dev/null; then
        echo "ERROR: sing_frame_audio_query failed (no 'f0' in response)" >&2
        echo "$frame_query" | head -3 >&2
        return 1
    fi

    # Step 2: frame_synthesis — frame_decode スピーカーでランダム合成
    local synth_speaker
    synth_speaker=$(pick_synth_speaker)
    if [ -z "$synth_speaker" ]; then
        echo "ERROR: no frame_decode speaker found" >&2
        return 1
    fi
    echo "Synthesizing with frame_decode speaker=$synth_speaker..." >&2

    local http_code
    http_code=$(curl -s --max-time "$VOICEVOX_SING_TIMEOUT" \
        -X POST "$VOICEVOX_URL/frame_synthesis?speaker=$synth_speaker" \
        -H "Content-Type: application/json" \
        -d "$frame_query" \
        --output "$output" \
        -w '%{http_code}')

    if [ "$http_code" != "200" ] || [ ! -s "$output" ]; then
        echo "ERROR: frame_synthesis failed (HTTP $http_code)" >&2
        rm -f "$output" 2>/dev/null
        return 1
    fi

    echo "OK: $output" >&2
}

# --- main ---
OUTPUT=""
SCORE_FILE=""

case "${1:-}" in
    --singers)
        show_singers
        exit $?
        ;;
    --test)
        OUTPUT="/tmp/voicevox_sing_test.wav"
        SCORE_FILE="$DEFAULT_SCORE"
        if [ ! -f "$SCORE_FILE" ]; then
            echo "ERROR: default score not found: $SCORE_FILE" >&2
            exit 1
        fi
        synthesize_song "$SCORE_FILE" "$OUTPUT" || exit 1
        echo "Playing: $OUTPUT"
        afplay "$OUTPUT"
        exit 0
        ;;
    -o)
        OUTPUT="$2"
        SCORE_FILE="$3"
        shift 3
        ;;
    ""|--help|-h)
        echo "Usage:"
        echo "  $0 --singers              シンガー一覧表示"
        echo "  $0 --test                 テスト歌唱（デフォルト楽譜）"
        echo "  $0 -o out.wav score.json  楽譜ファイルから合成"
        echo "  $0 -o out.wav -           stdin から楽譜JSON"
        echo ""
        echo "Environment variables:"
        echo "  VOICEVOX_URL            (default: http://127.0.0.1:50021)"
        echo "  VOICEVOX_SING_SPEAKER   (default: 6000 = 波音リツ)"
        echo "  VOICEVOX_SING_TIMEOUT   (default: 120)"
        exit 0
        ;;
    *)
        echo "ERROR: unknown option: $1" >&2
        echo "Run '$0 --help' for usage" >&2
        exit 1
        ;;
esac

if [ -z "$SCORE_FILE" ]; then
    echo "ERROR: no score file specified" >&2
    exit 1
fi

synthesize_song "$SCORE_FILE" "$OUTPUT" || exit 1

echo "Saved: $OUTPUT"
