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
VOICEVOX_MAX_CHARS="${VOICEVOX_MAX_CHARS:-200}"

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

# 単一チャンクを合成
_synthesize_one() {
    local text="$1"
    local output="$2"

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

    # ピッチ・テンポ適用 (VOICEVOX_PITCH / VOICEVOX_TEMPO 環境変数)
    if [ -n "${VOICEVOX_PITCH:-}" ] || [ -n "${VOICEVOX_TEMPO:-}" ]; then
        query_json=$(echo "$query_json" | python3 -c "
import json,sys,os
d=json.load(sys.stdin)
p=os.environ.get('VOICEVOX_PITCH','')
t=os.environ.get('VOICEVOX_TEMPO','')
if p: d['pitchScale']=d.get('pitchScale',0)+float(p)
if t: d['speedScale']=float(t)
json.dump(d,sys.stdout)
" 2>/dev/null) || true
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

# テキストを句点・改行で分割（Python に委譲）
_split_text() {
    local text="$1"
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
            # 読点で再分割
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
" "$text" "$VOICEVOX_MAX_CHARS"
}

# wav ファイルを Python wave モジュールで結合
_concat_wavs() {
    local output="$1"
    shift
    python3 -c "
import wave, sys
output = sys.argv[1]
files = sys.argv[2:]
with wave.open(output, 'wb') as out:
    params_set = False
    for f in files:
        with wave.open(f, 'rb') as inp:
            if not params_set:
                out.setparams(inp.getparams())
                params_set = True
            out.writeframes(inp.readframes(inp.getnframes()))
" "$output" "$@"
}

# チャンク一時ファイルを削除
_cleanup_chunks() {
    rm -f /tmp/voicevox_chunk_${$}_*.wav 2>/dev/null
}

synthesize() {
    local text="$1"
    local output="$2"

    check_server || return 1

    # テキストを分割
    local chunks=()
    while IFS= read -r line; do
        [ -n "$line" ] && chunks+=("$line")
    done < <(_split_text "$text")

    if [ ${#chunks[@]} -le 1 ]; then
        # 短いテキスト: 従来どおり1回で合成
        _synthesize_one "$text" "$output"
        return $?
    fi

    echo "Splitting into ${#chunks[@]} chunks..." >&2

    # チャンクごとに合成
    local chunk_files=() i=0
    for chunk in "${chunks[@]}"; do
        local chunk_wav="/tmp/voicevox_chunk_${$}_${i}.wav"
        _synthesize_one "$chunk" "$chunk_wav" || { _cleanup_chunks; return 1; }
        chunk_files+=("$chunk_wav")
        i=$((i + 1))
    done

    # Python wave モジュールで結合
    _concat_wavs "$output" "${chunk_files[@]}"
    local rc=$?
    _cleanup_chunks
    return $rc
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
        if [ "$1" = "-f" ]; then
            TEXT=$(cat "$2")
            shift 2
        else
            TEXT="$*"
        fi
        ;;
    -f)
        TEXT=$(cat "$2")
        shift 2
        OUTPUT="/tmp/voicevox_$$.wav"
        ;;
    ""|--help|-h)
        echo "Usage:"
        echo "  $0 --speakers           話者一覧表示"
        echo "  $0 --test               テスト音声生成+再生"
        echo "  $0 \"テキスト\"            音声合成+再生"
        echo "  $0 -o out.wav \"テキスト\"  ファイル出力"
        echo "  $0 -o out.wav -f file    ファイルから読み込み"
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
