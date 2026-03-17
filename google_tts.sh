#!/bin/bash
# Google Cloud TTS - 日本語音声合成
# Usage: ./tts.sh "テキスト" [options]
# Example: ./tts.sh "こんにちは"
#          ./tts.sh "こんにちは" --voice ja-JP-Chirp3-HD-Kore
#          ./tts.sh "こんにちは" --rate 1.5 --pitch 3
#          ./tts.sh --list   # 声の一覧表示

PROJECT="gen-lang-client-0367522921"
DEFAULT_VOICE="ja-JP-Neural2-B"
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
TEXT=""
VOICE="$DEFAULT_VOICE"
RATE="1.0"
PITCH="0"
VOLUME="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --voice)  VOICE="$2"; shift 2 ;;
    --rate)   RATE="$2"; shift 2 ;;
    --pitch)  PITCH="$2"; shift 2 ;;
    --volume) VOLUME="$2"; shift 2 ;;
    -*)       echo "Unknown option: $1"; exit 1 ;;
    *)        TEXT="$1"; shift ;;
  esac
done

if [[ -z "$TEXT" ]]; then
  echo "Usage: ./tts.sh \"テキスト\" [options]"
  echo "Options:"
  echo "  --voice NAME   声の種類 (default: $DEFAULT_VOICE)"
  echo "  --rate  N      速さ 0.25-4.0 (default: 1.0)"
  echo "  --pitch N      高さ -20.0-20.0 (default: 0)"
  echo "  --volume N     音量 -96.0-16.0 dB (default: 0)"
  echo "  --list         声の一覧表示"
  exit 1
fi

curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "x-goog-user-project: $PROJECT" \
  -H "Content-Type: application/json" \
  -d "{
    \"input\": {\"text\": \"$TEXT\"},
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
    print('Error:', data['error']['message']); sys.exit(1)
audio = base64.b64decode(data['audioContent'])
open('$OUT', 'wb').write(audio)
" && afplay "$OUT"
