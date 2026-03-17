#!/bin/bash
# Google Cloud TTS - 日本語音声合成
# Usage: ./google_tts.sh "テキスト" [options]
# Example: ./google_tts.sh "こんにちは"
#          ./google_tts.sh "こんにちは" --voice ja-JP-Chirp3-HD-Kore
#          ./google_tts.sh "こんにちは" --rate 1.5 --pitch 3
#          ./google_tts.sh --list   # 声の一覧表示

PROJECT="gen-lang-client-0367522921"
DEFAULT_VOICE="${GOOGLE_TTS_VOICE:-ja-JP-Standard-B}"
OUT="/tmp/tts.mp3"

if [[ "$1" == "--demo" ]]; then
  TEXT="${2:-こんにちは、私の声を聞いてください}"
  TOKEN=$(gcloud auth print-access-token)
  VOICES=$(curl -s \
    -H "Authorization: Bearer $TOKEN" \
    -H "x-goog-user-project: $PROJECT" \
    "https://texttospeech.googleapis.com/v1/voices?languageCode=ja-JP" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for v in data['voices']:
    print(v['name'], v['ssmlGender'])
")
  TOTAL=$(echo "$VOICES" | wc -l | tr -d ' ')
  I=0
  echo "$VOICES" | while read NAME GENDER; do
    I=$((I + 1))
    echo "[$I/$TOTAL] $NAME ($GENDER)"
    curl -s -X POST \
      -H "Authorization: Bearer $TOKEN" \
      -H "x-goog-user-project: $PROJECT" \
      -H "Content-Type: application/json" \
      -d "{
        \"input\": {\"text\": \"$TEXT\"},
        \"voice\": {\"languageCode\": \"ja-JP\", \"name\": \"$NAME\"},
        \"audioConfig\": {\"audioEncoding\": \"MP3\"}
      }" \
      "https://texttospeech.googleapis.com/v1/text:synthesize" \
      | python3 -c "
import sys, json, base64
data = json.load(sys.stdin)
if 'error' in data:
    print('  Error:', data['error']['message']); sys.exit(1)
audio = base64.b64decode(data['audioContent'])
open('$OUT', 'wb').write(audio)
" && afplay "$OUT"
  done
  exit 0
fi

if [[ "$1" == "--list" ]]; then
  curl -s \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "x-goog-user-project: $PROJECT" \
    "https://texttospeech.googleapis.com/v1/voices?languageCode=ja-JP" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for v in data['voices']:
    print(f\"{v['name']:30s} {v['ssmlGender']:8s}\")
"
  exit 0
fi

# Parse arguments
OUTPUT=""
TEXT=""
VOICE="$DEFAULT_VOICE"
RATE="${GOOGLE_TTS_RATE:-0.7}"
PITCH="${GOOGLE_TTS_PITCH:--2.5}"
VOLUME="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)       OUTPUT="$2"; shift 2 ;;
    -f)       TEXT=$(cat "$2"); shift 2 ;;
    --voice)  VOICE="$2"; shift 2 ;;
    --rate)   RATE="$2"; shift 2 ;;
    --pitch)  PITCH="$2"; shift 2 ;;
    --volume) VOLUME="$2"; shift 2 ;;
    -*)       echo "Unknown option: $1"; exit 1 ;;
    *)        TEXT="$1"; shift ;;
  esac
done

if [[ -z "$TEXT" ]]; then
  echo "Usage: ./google_tts.sh \"テキスト\" [options]"
  echo "       ./google_tts.sh -o out.mp3 \"テキスト\""
  echo "       ./google_tts.sh -o out.mp3 -f content.txt"
  echo "Options:"
  echo "  -o FILE        ファイル出力（再生なし）"
  echo "  -f FILE        テキストをファイルから読み込み"
  echo "  --voice NAME   声の種類 (default: $DEFAULT_VOICE)"
  echo "  --rate  N      速さ 0.25-4.0 (default: 1.0)"
  echo "  --pitch N      高さ -20.0-20.0 (default: 0)"
  echo "  --volume N     音量 -96.0-16.0 dB (default: 0)"
  echo "  --list         声の一覧表示"
  echo "Env: GOOGLE_TTS_VOICE, GOOGLE_TTS_RATE"
  exit 1
fi

# 出力先の決定
PLAY_AFTER=false
if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$OUT"
  PLAY_AFTER=true
fi

# テキストのエスケープ（JSON用）
ESCAPED_TEXT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TEXT")

curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: $PROJECT" \
  -H "Content-Type: application/json" \
  -d "{
    \"input\": {\"text\": $ESCAPED_TEXT},
    \"voice\": {\"languageCode\": \"ja-JP\", \"name\": \"$VOICE\"},
    \"audioConfig\": {
      \"audioEncoding\": \"MP3\",
      \"speakingRate\": $RATE,
      \"pitch\": $PITCH,
      \"volumeGainDb\": $VOLUME
    }
  }" \
  "https://texttospeech.googleapis.com/v1/text:synthesize" \
  | python3 -c "
import sys, json, base64
data = json.load(sys.stdin)
if 'error' in data:
    print('Error:', data['error']['message'], file=sys.stderr); sys.exit(1)
audio = base64.b64decode(data['audioContent'])
open(sys.argv[1], 'wb').write(audio)
" "$OUTPUT" || exit 1

[[ "$PLAY_AFTER" = "true" ]] && afplay "$OUTPUT"
