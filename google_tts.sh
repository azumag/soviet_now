#!/bin/bash
# Google Cloud TTS - 日本語音声合成
# Usage: ./google_tts.sh "テキスト" [options]
# Example: ./google_tts.sh "こんにちは"
#          ./google_tts.sh "こんにちは" --voice ja-JP-Chirp3-HD-Kore
#          ./google_tts.sh "こんにちは" --rate 1.5 --pitch 3
#          ./google_tts.sh --list   # 声の一覧表示

PROJECT="gen-lang-client-0367522921"
DEFAULT_VOICE="${GOOGLE_TTS_VOICE:-ja-JP-Standard-B}"
GOOGLE_TTS_MAX_CHARS="${GOOGLE_TTS_MAX_CHARS:-500}"
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

# gcloud トークン取得（全チャンクで共有）
TOKEN=$(gcloud auth print-access-token) || { echo "Error: gcloud auth failed" >&2; exit 1; }

# --- テキスト分割（句点・改行で区切り、max_chars 以内にまとめる） ---
_split_text() {
  python3 -c "
import sys
text = sys.argv[1]
max_len = int(sys.argv[2])
chunks = []
for line in text.split('\n'):
    for sent in line.split('\u3002'):
        sent = sent.strip()
        if not sent:
            continue
        sent += '\u3002'
        if chunks and len(chunks[-1]) + len(sent) <= max_len:
            chunks[-1] += sent
        else:
            if len(sent) > max_len:
                parts = sent.split('\u3001')
                buf = ''
                for p in parts:
                    candidate = buf + ('\u3001' if buf else '') + p
                    if len(candidate) > max_len and buf:
                        chunks.append(buf)
                        buf = p
                    else:
                        buf = candidate
                if buf:
                    chunks.append(buf)
            else:
                chunks.append(sent)
for c in chunks:
    print(c)
" "$1" "$2"
}

# --- 1チャンク合成 ---
_synthesize_one() {
  local text="$1" output="$2"
  local escaped
  escaped=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$text")

  curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "x-goog-user-project: $PROJECT" \
    -H "Content-Type: application/json" \
    -d "{
      \"input\": {\"text\": $escaped},
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
" "$output"
}

# --- MP3 結合 ---
_concat_mp3s() {
  local output="$1"; shift
  if [[ $# -eq 1 ]]; then
    mv "$1" "$output"
    return
  fi
  # ffmpeg concat demuxer
  local listfile="/tmp/gtts_concat_$$.txt"
  for f in "$@"; do
    echo "file '$f'"
  done > "$listfile"
  ffmpeg -y -loglevel error -f concat -safe 0 -i "$listfile" -c copy "$output" 2>/dev/null
  local rc=$?
  rm -f "$listfile"
  return $rc
}

_cleanup_chunks() {
  rm -f /tmp/gtts_chunk_${$}_*.mp3 2>/dev/null
}

# --- メイン合成処理 ---
chunks=()
while IFS= read -r line; do
  [[ -n "$line" ]] && chunks+=("$line")
done < <(_split_text "$TEXT" "$GOOGLE_TTS_MAX_CHARS")

if [[ ${#chunks[@]} -le 1 ]]; then
  _synthesize_one "$TEXT" "$OUTPUT" || exit 1
else
  echo "Splitting into ${#chunks[@]} chunks..." >&2
  chunk_files=()
  i=0
  for chunk in "${chunks[@]}"; do
    chunk_mp3="/tmp/gtts_chunk_${$}_${i}.mp3"
    _synthesize_one "$chunk" "$chunk_mp3" || { _cleanup_chunks; exit 1; }
    [[ -s "$chunk_mp3" ]] || { _cleanup_chunks; exit 1; }
    chunk_files+=("$chunk_mp3")
    i=$((i + 1))
  done
  _concat_mp3s "$OUTPUT" "${chunk_files[@]}" || { _cleanup_chunks; exit 1; }
  _cleanup_chunks
fi

[[ "$PLAY_AFTER" = "true" ]] && afplay "$OUTPUT"
