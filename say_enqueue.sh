#!/bin/bash
# say_enqueue.sh - mkdirロックベースのsayキュー（FIFO順次再生）
#
# 使い方: ./say_enqueue.sh [--no-preempt] <content_file> [rate] [pre_delay_sec]
#
# --no-preempt: 後方互換のため受け付ける（現在は常に順次再生）
#
# 動作:
#   1. コンテンツコピー
#   2. mkdirロック取得（取得できるまで待機）
#   3. ロック内: 前のsay PID待ち
#   4. ロック内: say 再生（異常終了時はリトライ）
#   5. ロック解放
#   6. クリーンアップ

set -uo pipefail
cd "$(dirname "$0")"

# フラグ処理
NO_PREEMPT=false
WAV_MODE=false
while true; do
    case "${1:-}" in
        --no-preempt) NO_PREEMPT=true; shift ;;
        --wav) WAV_MODE=true; shift ;;
        *) break ;;
    esac
done

QUEUE_DIR="tmp/.say_queue"
mkdir -p "$QUEUE_DIR"

CONTENT_FILE="${1:?Usage: say_enqueue.sh [--no-preempt] <content_file> [rate]}"
RATE="${2:-120}"
SAY_RETRY_MAX="${SAY_RETRY_MAX:-6}"
SAY_RETRY_SLEEP_SEC="${SAY_RETRY_SLEEP_SEC:-2}"
SAY_RETRY_MAX_SLEEP_SEC="${SAY_RETRY_MAX_SLEEP_SEC:-20}"
SAY_TRUNCATE_RATIO="${SAY_TRUNCATE_RATIO:-0.8}"
SAY_TRUNCATE_GRACE_SEC="${SAY_TRUNCATE_GRACE_SEC:-3}"
SAY_TRUNCATE_MIN_EXPECTED_SEC="${SAY_TRUNCATE_MIN_EXPECTED_SEC:-15}"
SAY_HANG_EXTRA_SEC="${SAY_HANG_EXTRA_SEC:-120}"

# --- VOICEVOX ASMR (ささやき系) 話者選択 ---
# _pick_asmr_voicevox_speaker: ささやき系から "ID|名前/スタイル" を stdout に返す（粛清フィルタ適用）
_pick_asmr_voicevox_speaker() {
    local vo_url="${VOICEVOX_URL:-http://127.0.0.1:50021}"
    curl -s --max-time 3 "$vo_url/speakers" 2>/dev/null | python3 -c "
import json, sys, random
try:
    with open('tmp/voicevox_exclude_ids.txt') as f:
        exclude_ids = {int(l.strip()) for l in f if l.strip().isdigit()}
except FileNotFoundError:
    exclude_ids = set()
speakers = json.load(sys.stdin)
pool = [(s['name'], st['id'], st['name']) for s in speakers for st in s.get('styles', []) if st.get('type', 'talk') == 'talk' and 'ささやき' in st['name'] and st['id'] not in exclude_ids]
if pool:
    name, sid, style = random.choice(pool)
    print(f'{sid}|{name}/{style}', end='')
else:
    print('36|四国めたん/ささやき', end='')
" 2>/dev/null
}

# --- VOICEVOX ランダム話者選択 ---
# _pick_random_voicevox_speaker: "ID|名前/スタイル" を stdout に返す
_pick_random_voicevox_speaker() {
    local vo_url="${VOICEVOX_URL:-http://127.0.0.1:50021}"
    curl -s --max-time 3 "$vo_url/speakers" 2>/dev/null | python3 -c "
import json, sys, random
exclude = {'玄野武宏','白上虎太郎','後鬼','ちび式じい','†聖騎士 紅桜†','栗田まろん','Voidoll'}
try:
    with open('tmp/voicevox_exclude_ids.txt') as f:
        exclude_ids = {int(l.strip()) for l in f if l.strip().isdigit()}
except FileNotFoundError:
    exclude_ids = set()
speakers = json.load(sys.stdin)
exclude_styles = {'ささやき', 'セクシー'}
pool = [(s['name'], st['id'], st['name']) for s in speakers if s['name'] not in exclude for st in s.get('styles', []) if st.get('type', 'talk') == 'talk' and st['id'] not in exclude_ids and not any(k in st['name'] for k in exclude_styles)]
if pool:
    name, sid, style = random.choice(pool)
    print(f'{sid}|{name}/{style}', end='')
else:
    print('3|ずんだもん/ノーマル', end='')
" 2>/dev/null
}

# --- COEIROINK TTS切替 ---
# tmp/coeiroink_voice.txt の内容で動作を決定:
#   "random"        → 毎回ランダムに話者選択
#   "uuid|styleId"  → 固定話者
#   ファイルなし     → macOS say
# !wakana/!moko で固定、!random でランダム、!say で無効化
_COE_VOICES=(
    "3c37646f-3881-5374-2a83-149267990abc|0"          # つくよみちゃん れいせい
    "d41bcbd9-f4a9-4e10-b000-7a431568dd01|100"        # AI声優-金苗 のーまる
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131762"  # モコちゃん よろこび
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131765"  # モコちゃん ろぼろぼ
    "8e99d620-87d3-11ed-870a-0242ac1c000c|905192261"  # ワカナ normal
    "9bf2ab50-c756-11ec-9374-0242ac1c0002|1403759395" # ナースロボ 通常
    "6e0539ea-a6a7-11f0-8d2f-0242ac1c000c|172697038"  # AⅡowa β
    "f0d1a286-64dd-11ee-babd-0242ac1c000c|1486940343" # 芽々子 のーまる
)
if [ -f "tmp/coeiroink_voice.txt" ]; then
    _coe_line=$(cat "tmp/coeiroink_voice.txt" 2>/dev/null)
    if [ "$_coe_line" = "random" ]; then
        _coe_pick="${_COE_VOICES[$((RANDOM % ${#_COE_VOICES[@]}))]}"
        COEIROINK_SPEAKER_UUID="${_coe_pick%%|*}"
        COEIROINK_STYLE_ID="${_coe_pick##*|}"
    else
        COEIROINK_SPEAKER_UUID="${_coe_line%%|*}"
        COEIROINK_STYLE_ID="${_coe_line##*|}"
    fi
    USE_COEIROINK="${USE_COEIROINK:-1}"
else
    USE_COEIROINK="${USE_COEIROINK:-0}"
    COEIROINK_SPEAKER_UUID="${COEIROINK_SPEAKER_UUID:-8e99d620-87d3-11ed-870a-0242ac1c000c}"
    COEIROINK_STYLE_ID="${COEIROINK_STYLE_ID:-905192261}"
fi

# --- VOICEVOX TTS切替 ---
# tmp/voicevox_voice.txt があれば VOICEVOX を使用 (COEIROINK より優先)
# ファイル内容: speaker ID (例: 109) or "random"
USE_VOICEVOX=0
VOICEVOX_SPEAKER="${VOICEVOX_SPEAKER:-109}"
if [ -f "tmp/voicevox_voice.txt" ]; then
    _vo_line=$(cat "tmp/voicevox_voice.txt" 2>/dev/null)
    if [ "$_vo_line" = "random" ]; then
        _vo_result=$(_pick_random_voicevox_speaker)
        VOICEVOX_SPEAKER="${_vo_result%%|*}"
        VOICEVOX_RANDOM_VOICE_NAME="${_vo_result#*|}"
        VOICEVOX_RANDOM_MODE=1
    else
        VOICEVOX_SPEAKER="$_vo_line"
        VOICEVOX_RANDOM_MODE=0
    fi
    USE_VOICEVOX=1
    USE_COEIROINK=0
fi

PID_FILE="$QUEUE_DIR/pid"
LOCK_DIR="$QUEUE_DIR/.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner_pid"
LOCK_HEARTBEAT_FILE="$LOCK_DIR/heartbeat"
CURRENT_SOURCE_FILE="$QUEUE_DIR/current_source"
PLAYED_LOG_FILE="$QUEUE_DIR/played.log"
LAST_RADIO_PLAYED_FILE="tmp/state/radio_talk_played"
LOCK_STALE_SEC=180

if [ ! -s "$CONTENT_FILE" ]; then
    echo "[say_enqueue] content file missing or empty: $CONTENT_FILE" >&2
    exit 1
fi

# ユニークトークン（PID + ランダム + 秒 で衝突回避）
MY_TOKEN="${BASHPID:-$$}_${RANDOM}_$(date +%s)"
MY_OWNER="${BASHPID:-$$}:${MY_TOKEN}"
MY_CONTENT="$QUEUE_DIR/content_${MY_TOKEN}.txt"
LOCK_HELD=0
LAUNCHED_SAY_PID=""
LAUNCHED_EXPECTED_SEC=0

# コンテンツをキュー用にコピー（元ファイルが消されても安全）
cp "$CONTENT_FILE" "$MY_CONTENT"

# 読み上げ修正: よくある誤読を事前に置換（WAVモード時はスキップ）
if [ "$WAV_MODE" = "false" ]; then
    sed -i '' \
        -e 's/AI/エーアイ/g' \
        -e 's/静寂/せいじゃく/g' \
        -e 's/地政学的/ちせいがくてき/g' \
        -e 's/地政学/ちせいがく/g' \
        "$MY_CONTENT"
fi

_infer_source_label() {
    local path="$1" base corner
    base=$(basename "$path")
    case "$path" in
    *"tmp/.comment_queue/comment_"*.playing|*"tmp/.comment_queue/comment_"*.txt)
        echo "comment"
        return 0
        ;;
    *"tmp/.radio_deferred_queue/radio_"*.playing|*"tmp/.radio_deferred_queue/radio_"*.txt)
        corner=$(printf '%s' "$base" | sed -E 's/^radio_[0-9]+_[0-9]+_([^_]+)_.*/\1/')
        [ -n "$corner" ] && [ "$corner" != "$base" ] && {
            echo "radio:${corner}"
            return 0
        }
        ;;
    *"/tmp/eloop_radio_talk_"*)
        echo "radio"
        return 0
        ;;
    *"tmp/radio_celebration.txt")
        echo "radio:celebration"
        return 0
        ;;
    *"radio_russia_celebration.txt")
        echo "radio:russia"
        return 0
        ;;
    esac
    return 1
}

SOURCE_LABEL="${SAY_CONTEXT_LABEL:-}"
if [ -z "$SOURCE_LABEL" ]; then
    SOURCE_LABEL=$(_infer_source_label "$CONTENT_FILE" 2>/dev/null || true)
fi

_log() { echo "[say_enqueue $(date '+%H:%M:%S')] $*" >&2; echo "[say_enqueue $(date '+%H:%M:%S') PID=$$/${BASHPID:-?}] $* | file=$CONTENT_FILE token=$MY_TOKEN label=${SOURCE_LABEL:-unknown}" >> tmp/.say_queue/debug.log; }

_append_played_log() {
    local status="$1" now_h now_ts
    now_h=$(date '+%H:%M:%S')
    now_ts=$(date +%s)
    printf '[%s] %s [%s] %s\n' "$now_h" "$status" "${SOURCE_LABEL:-unknown}" "$CONTENT_FILE" >> "$PLAYED_LOG_FILE"
    if [ -f "$PLAYED_LOG_FILE" ] && [ "$(wc -l < "$PLAYED_LOG_FILE")" -gt 500 ]; then
        tail -200 "$PLAYED_LOG_FILE" > "${PLAYED_LOG_FILE}.tmp" && mv "${PLAYED_LOG_FILE}.tmp" "$PLAYED_LOG_FILE"
    fi
    case "${SOURCE_LABEL:-}" in
    radio:*)
        if [ "$status" = "played" ]; then
            printf '%s|%s|%s\n' "$now_ts" "${SOURCE_LABEL#radio:}" "$CONTENT_FILE" > "$LAST_RADIO_PLAYED_FILE"
        fi
        ;;
    esac
}

_is_lock_owner() {
    [ -d "$LOCK_DIR" ] || return 1
    [ "$(cat "$LOCK_OWNER_FILE" 2>/dev/null || true)" = "$MY_OWNER" ]
}

_touch_lock_heartbeat() {
    _is_lock_owner || return 0
    echo "$MY_OWNER" > "$LOCK_OWNER_FILE" 2>/dev/null || true
    date +%s > "$LOCK_HEARTBEAT_FILE" 2>/dev/null || true
}

_set_current_source() {
    local phase="${1:-waiting}"
    _is_lock_owner || return 0
    printf '%s|%s|%s|%s|%s\n' "$MY_OWNER" "$phase" "$CONTENT_FILE" "$(date +%s)" "${SOURCE_LABEL:-}" > "$CURRENT_SOURCE_FILE" 2>/dev/null || true
}

_clear_current_source_if_owner() {
    local owner
    owner=$(awk -F'|' 'NR==1{print $1}' "$CURRENT_SOURCE_FILE" 2>/dev/null || true)
    [ -n "$owner" ] || return 0
    [ "$owner" = "$MY_OWNER" ] || return 0
    rm -f "$CURRENT_SOURCE_FILE" 2>/dev/null || true
}

_has_pending_comment_queue() {
    ls tmp/.comment_queue/comment_*.txt >/dev/null 2>&1
}

_radio_should_yield_to_comment() {
    # deferred radio is launched from the comment player itself.
    # If it keeps yielding to newly queued comments, the comment player blocks
    # on this process and can never drain that backlog.
    case "${SAY_DISABLE_COMMENT_YIELD:-0}" in
    1|true|yes)
        return 1
        ;;
    esac

    case "${SOURCE_LABEL:-}" in
    radio|radio:*)
        ;;
    *)
        return 1
        ;;
    esac

    _has_pending_comment_queue
}

_yield_turn_to_pending_comment() {
    _is_lock_owner || return 1
    _log "pending comment を優先するため ${SOURCE_LABEL:-unknown} が順番を譲る"
    _release_lock
    sleep 1
    return 0
}

# mkdirロック: アトミックな排他制御（macOS互換）
_acquire_lock() {
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        # stale lock検出: 所有PIDが死んでおり、heartbeatも古い場合のみ強制解除
        if [ -d "$LOCK_DIR" ]; then
            local lock_owner_raw lock_owner_pid lock_hb now lock_age owner_alive=false
            lock_owner_raw=$(cat "$LOCK_OWNER_FILE" 2>/dev/null || true)
            lock_owner_pid="${lock_owner_raw%%:*}"
            case "$lock_owner_pid" in
            ''|*[!0-9]*) lock_owner_pid="" ;;
            esac
            if [ -n "$lock_owner_pid" ] && kill -0 "$lock_owner_pid" 2>/dev/null; then
                owner_alive=true
            fi
            lock_hb=$(cat "$LOCK_HEARTBEAT_FILE" 2>/dev/null || true)
            case "$lock_hb" in
            ''|*[!0-9]*)
                lock_hb=$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0)
                ;;
            esac
            now=$(date +%s)
            lock_age=$((now - lock_hb))
            if [ "$owner_alive" = false ] && [ "$lock_age" -gt "$LOCK_STALE_SEC" ]; then
                _log "stale lock検出 (owner=${lock_owner_pid:-?}, ${lock_age}秒) → 強制解除"
                rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
                rmdir "$LOCK_DIR" 2>/dev/null
                continue
            fi
        fi
        sleep 0.5
    done
    echo "$MY_OWNER" > "$LOCK_OWNER_FILE" 2>/dev/null || {
        rmdir "$LOCK_DIR" 2>/dev/null
        return 1
    }
    date +%s > "$LOCK_HEARTBEAT_FILE" 2>/dev/null || true
    LOCK_HELD=1
    return 0
}

_release_lock() {
    [ "$LOCK_HELD" -eq 1 ] || return 0
    if ! _is_lock_owner; then
        _log "ロック解放スキップ: 所有者不一致"
        LOCK_HELD=0
        return 0
    fi
    _clear_current_source_if_owner
    rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
    rmdir "$LOCK_DIR" 2>/dev/null
    LOCK_HELD=0
}

# クリーンアップ: 終了時にロック解放 + 自分のコンテンツ削除
_cleanup() {
    _clear_current_source_if_owner
    _release_lock
    rm -f "$MY_CONTENT"
}
trap '_cleanup' EXIT

_log "queued (token=${MY_TOKEN})"

_sleep_with_heartbeat() {
    local sec="${1:-1}" waited=0
    while [ "$waited" -lt "$sec" ]; do
        _touch_lock_heartbeat
        sleep 1
        waited=$((waited + 1))
    done
}

_resolve_audio_device_index() {
    local name="$1"
    # 数値ならそのまま返す
    case "$name" in
    *[!0-9]*) ;;  # 非数値→名前解決へ
    '') return 1 ;;
    *) echo "$name"; return 0 ;;
    esac
    local devices line idx alt_name
    devices=$(ffmpeg -y -f lavfi -i sine=frequency=1:duration=0.001 -f audiotoolbox -list_devices true "" 2>&1)

    # まずは完全一致
    line=$(printf '%s\n' "$devices" | grep -F "$name" | head -1)
    if [ -z "$line" ]; then
        # CoreAudio 側で表記揺れした場合に備えて緩めに解決
        alt_name=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')
        line=$(printf '%s\n' "$devices" | awk -v needle="$alt_name" '
            BEGIN { IGNORECASE = 1 }
            {
                hay = tolower($0)
                if (index(hay, needle) > 0) {
                    print
                    exit
                }
            }')
    fi
    if [ -z "$line" ] && printf '%s' "$name" | grep -qi 'blackhole'; then
        line=$(printf '%s\n' "$devices" | awk '
            BEGIN { IGNORECASE = 1 }
            /blackhole/ {
                print
                exit
            }')
    fi
    if [ -n "$line" ]; then
        idx=$(printf '%s\n' "$line" | sed -n 's/.*\[\([0-9][0-9]*\)\].*/\1/p')
        if [ -n "$idx" ]; then
            echo "$idx"
            return 0
        fi
    fi
    echo "[say_enqueue] audio device not found: $name" >&2
    return 1
}

_estimate_audio_duration_sec() {
    local file="$1" d
    d=""
    if command -v ffprobe >/dev/null 2>&1; then
        d=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$file" 2>/dev/null | head -1)
        case "$d" in
        ''|N/A|*[!0-9.]*) d="" ;;
        esac
    fi
    if [ -z "$d" ] && command -v afinfo >/dev/null 2>&1; then
        d=$(afinfo "$file" 2>/dev/null | sed -n 's/.*estimated duration: \([0-9.][0-9.]*\) sec.*/\1/p' | head -1)
        case "$d" in
        ''|*[!0-9.]*) d="" ;;
        esac
    fi
    if [ -z "$d" ]; then
        echo 0
        return 0
    fi
    awk -v v="$d" 'BEGIN { if (v < 0) v = 0; printf "%d\n", (v + 0.5) }'
}

_estimate_text_duration_sec() {
    local file="$1" rate="${2:-150}" chars chars_per_sec
    chars=$(wc -m < "$file" 2>/dev/null | tr -d '[:space:]')
    case "$chars" in
    ''|*[!0-9]*) chars=0 ;;
    esac
    if [ "$chars" -le 0 ]; then
        echo 0
        return 0
    fi
    chars_per_sec=$(awk -v r="$rate" '
BEGIN {
    cps = 5.0
    if (r > 0) cps = cps * (r / 150.0)
    if (cps < 2.5) cps = 2.5
    printf "%.3f\n", cps
}')
    awk -v c="$chars" -v cps="$chars_per_sec" '
BEGIN {
    sec = int((c / cps) + 0.999)
    if (sec < 8) sec = 8
    print sec
}'
}

_is_truncated_playback() {
    local elapsed="${1:-0}" expected="${2:-0}"
    awk -v e="$elapsed" -v x="$expected" -v min="$SAY_TRUNCATE_MIN_EXPECTED_SEC" -v ratio="$SAY_TRUNCATE_RATIO" -v grace="$SAY_TRUNCATE_GRACE_SEC" '
BEGIN {
    if (x < min) exit 1
    if ((e + grace) < (x * ratio)) exit 0
    exit 1
}'
}

_launch_say() {
    LAUNCHED_EXPECTED_SEC=0
    LAUNCH_MODE="say"

    # --- Pre-synthesized WAV (--wav mode) ---
    if [ "$WAV_MODE" = "true" ] && [ -s "$MY_CONTENT" ]; then
        LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$MY_CONTENT")
        nohup bash -c 'trap "" INT TERM; afplay "$1"' _ "$MY_CONTENT" >/dev/null 2>&1 &
        LAUNCH_MODE="wav"
        LAUNCHED_SAY_PID="$!"
        return
    fi

    # --- 同志モード: コメント再生時に macOS say へ一時切替 ---
    if [ -f "tmp/voicevox_dousi.txt" ]; then
        case "${SOURCE_LABEL:-}" in
        comment|comment:*)
            rm -f "tmp/voicevox_dousi.txt"
            USE_VOICEVOX=0
            USE_COEIROINK=0
            _log "同志mode: macOS say"
            ;;
        esac
    fi

    # --- ASMR モード: コメント再生時にささやき系ボイスへ一時切替 ---
    if [ "${USE_VOICEVOX:-0}" = "1" ] && [ -f "tmp/voicevox_asmr.txt" ]; then
        case "${SOURCE_LABEL:-}" in
        comment|comment:*)
            local _asmr_result
            _asmr_result=$(_pick_asmr_voicevox_speaker)
            VOICEVOX_SPEAKER="${_asmr_result%%|*}"
            VOICEVOX_RANDOM_VOICE_NAME="${_asmr_result#*|}"
            VOICEVOX_RANDOM_MODE=1
            rm -f "tmp/voicevox_asmr.txt"
            _log "ASMR mode: speaker=$VOICEVOX_SPEAKER ($VOICEVOX_RANDOM_VOICE_NAME)"
            ;;
        esac
    fi

    # --- VOICEVOX TTS ---
    if [ "${USE_VOICEVOX:-0}" = "1" ]; then
        # 合成直前に粛清リストを再チェック — 粛清済みなら別のスピーカーに差し替え
        if [ -f "tmp/voicevox_exclude_ids.txt" ] && grep -qx "$VOICEVOX_SPEAKER" "tmp/voicevox_exclude_ids.txt" 2>/dev/null; then
            _log "speaker=$VOICEVOX_SPEAKER は粛清済み → 再選択"
            local _reroll
            _reroll=$(_pick_random_voicevox_speaker)
            VOICEVOX_SPEAKER="${_reroll%%|*}"
            VOICEVOX_RANDOM_VOICE_NAME="${_reroll#*|}"
        fi
        local vo_voice_name="${VOICEVOX_RANDOM_VOICE_NAME:-}"
        # IDごとのピッチ・テンポ設定をルックアップ
        local vo_pitch="" vo_tempo=""
        if [ -f "tmp/voicevox_pitch_map.txt" ]; then
            vo_pitch=$(grep "^${VOICEVOX_SPEAKER}|" "tmp/voicevox_pitch_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
        fi
        if [ -f "tmp/voicevox_tempo_map.txt" ]; then
            vo_tempo=$(grep "^${VOICEVOX_SPEAKER}|" "tmp/voicevox_tempo_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
        fi
        _log "VOICEVOX speaker=$VOICEVOX_SPEAKER${vo_voice_name:+ ($vo_voice_name)}${vo_pitch:+ pitch=$vo_pitch}${vo_tempo:+ tempo=$vo_tempo}"
        local vo_wav
        vo_wav="${MY_CONTENT%.txt}.wav"
        # 合成中もheartbeatを更新（stale判定回避）
        ( while true; do _touch_lock_heartbeat; sleep 2; done ) &
        local _hb_pid=$!
        local _vo_ok=0
        if VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" VOICEVOX_PITCH="$vo_pitch" VOICEVOX_TEMPO="$vo_tempo" VOICEVOX_TIMEOUT=60 \
           ./voicevox_tts.sh -o "$vo_wav" -f "$MY_CONTENT" 2>/dev/null && [ -s "$vo_wav" ]; then
            _vo_ok=1
        fi
        kill "$_hb_pid" 2>/dev/null; wait "$_hb_pid" 2>/dev/null
        if [ "$_vo_ok" -eq 1 ]; then
            # vo_random 時はチャットに話者名を投稿
            if [ -n "$vo_voice_name" ] && [ "${VOICEVOX_RANDOM_MODE:-0}" = "1" ]; then
                ./twitch_chat.sh send "VOICEVOX: [$VOICEVOX_SPEAKER] $vo_voice_name" >/dev/null 2>&1 &
            fi
            LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$vo_wav")
            nohup bash -c 'trap "" INT TERM; afplay "$1"; rc=$?; rm -f "$1"; exit $rc' _ "$vo_wav" >/dev/null 2>&1 &
            LAUNCH_MODE="voicevox"
            LAUNCHED_SAY_PID="$!"
            return
        else
            _log "VOICEVOX合成失敗 → macOS say にフォールバック"
        fi
    fi

    # --- COEIROINK TTS ---
    if [ "${USE_COEIROINK:-0}" = "1" ]; then
        local coe_text coe_wav
        coe_text=$(cat "$MY_CONTENT" 2>/dev/null)
        coe_wav="${MY_CONTENT%.txt}.wav"
        if SPEAKER_UUID="$COEIROINK_SPEAKER_UUID" STYLE_ID="$COEIROINK_STYLE_ID" \
           ./coeiroink_tts.sh -o "$coe_wav" "$coe_text" >/dev/null 2>&1 && [ -s "$coe_wav" ]; then
            LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$coe_wav")
            nohup bash -c 'trap "" INT TERM; afplay "$1"; rc=$?; rm -f "$1"; exit $rc' _ "$coe_wav" >/dev/null 2>&1 &
            LAUNCH_MODE="coeiroink"
            LAUNCHED_SAY_PID="$!"
            return
        else
            _log "COEIROINK合成失敗 → macOS say にフォールバック"
        fi
    fi
    # --- /COEIROINK ---

    if [ -n "${SAY_AUDIO_DEVICE:-}" ] && [ "${SAY_FORCE_DIRECT:-0}" != "1" ]; then
        local device_index
        device_index=$(_resolve_audio_device_index "$SAY_AUDIO_DEVICE") || {
            _log "audio device解決失敗 (${SAY_AUDIO_DEVICE}) → デフォルト出力にフォールバック"
            nohup bash -c 'trap "" INT TERM; say -r "$1" -f "$2"' _ "$RATE" "$MY_CONTENT" >/dev/null 2>&1 &
            LAUNCH_MODE="say"
            LAUNCHED_SAY_PID="$!"
            return
        }
        local aiff_file="${MY_CONTENT%.txt}.aiff"
        if ! say -r "$RATE" -o "$aiff_file" -f "$MY_CONTENT" >/dev/null 2>&1; then
            _log "say音声生成失敗 (rc!=0)"
            LAUNCHED_SAY_PID=""
            return
        fi
        LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$aiff_file")
        if [ "${LAUNCHED_EXPECTED_SEC:-0}" -le 0 ]; then
            _log "say音声生成失敗 (duration=${LAUNCHED_EXPECTED_SEC:-0}s)"
            rm -f "$aiff_file" 2>/dev/null || true
            LAUNCHED_SAY_PID=""
            return
        fi
        nohup bash -c 'trap "" INT TERM; ffmpeg -y -loglevel error -i "$1" -f audiotoolbox -audio_device_index "$2" ""; rc=$?; [ "$rc" -eq 0 ] && rm -f "$1"; exit "$rc"' \
            _ "$aiff_file" "$device_index" >/dev/null 2>&1 &
        LAUNCH_MODE="ffmpeg"
    else
        nohup bash -c 'trap "" INT TERM; say -r "$1" -f "$2"' _ "$RATE" "$MY_CONTENT" >/dev/null 2>&1 &
        LAUNCH_MODE="say"
        LAUNCHED_EXPECTED_SEC=$(_estimate_text_duration_sec "$MY_CONTENT" "$RATE")
    fi
    LAUNCHED_SAY_PID="$!"
}

_play_with_retry() {
    local retry=0 backoff="$SAY_RETRY_SLEEP_SEC"
    LAST_SAY_PID=""
    SAY_FORCE_DIRECT=0
    while true; do
        local attempt=$((retry + 1))
        _set_current_source "playing"
        _log "say開始 (attempt=${attempt}, rate=${RATE})"
        local say_pid
        LAUNCHED_SAY_PID=""
        _launch_say
        say_pid="${LAUNCHED_SAY_PID:-}"
        if [ -z "$say_pid" ]; then
            _log "say起動失敗"
        else
            LAST_SAY_PID="$say_pid"
            echo "$say_pid" > "$PID_FILE"
        fi
        local start_ts now_ts elapsed say_rc expected_sec max_wait_sec timed_out
        start_ts=$(date +%s)
        expected_sec="${LAUNCHED_EXPECTED_SEC:-0}"
        max_wait_sec=0
        timed_out=0
        # 期待尺が取れる経路（SAY_AUDIO_DEVICE経由）では、ハング監視を有効化
        if [ "${expected_sec:-0}" -gt 0 ]; then
            max_wait_sec=$((expected_sec + SAY_TRUNCATE_GRACE_SEC + SAY_HANG_EXTRA_SEC))
        fi
        if [ -z "$say_pid" ]; then
            say_rc=97
            now_ts=$(date +%s)
            elapsed=$((now_ts - start_ts))
        else
            while kill -0 "$say_pid" 2>/dev/null; do
                _touch_lock_heartbeat
                sleep 1
                if [ "$max_wait_sec" -gt 0 ]; then
                    now_ts=$(date +%s)
                    elapsed=$((now_ts - start_ts))
                    if [ "$elapsed" -gt "$max_wait_sec" ]; then
                        timed_out=1
                        _log "say再生ハング疑い (elapsed=${elapsed}s, expected=${expected_sec}s, max=${max_wait_sec}s) → 強制終了して再試行"
                        kill "$say_pid" 2>/dev/null || true
                        sleep 1
                        kill -9 "$say_pid" 2>/dev/null || true
                        break
                    fi
                fi
            done
            wait "$say_pid" 2>/dev/null
            say_rc=$?
            if [ "$timed_out" -eq 1 ]; then
                say_rc=99
            fi
            now_ts=$(date +%s)
            elapsed=$((now_ts - start_ts))
        fi
        if [ "$say_rc" -eq 0 ] && _is_truncated_playback "$elapsed" "$expected_sec"; then
            say_rc=98
            _log "say途中切断の疑い (elapsed=${elapsed}s, expected=${expected_sec}s)"
        fi
        if [ "$say_rc" -eq 0 ]; then
            return 0
        fi
        # 粛清等による外部killフラグがあればリトライしない
        if [ -f "$QUEUE_DIR/kill_flag" ]; then
            rm -f "$QUEUE_DIR/kill_flag"
            _log "外部killフラグ検出 → リトライ中止"
            return "$say_rc"
        fi
        if [ "${LAUNCH_MODE:-say}" = "ffmpeg" ] && [ "$SAY_FORCE_DIRECT" -eq 0 ]; then
            SAY_FORCE_DIRECT=1
            _log "ffmpeg再生失敗 (rc=$say_rc) → 次回は say 直再生へフォールバック"
        fi
        if [ "$retry" -ge "$SAY_RETRY_MAX" ]; then
            _log "say異常終了 (rc=$say_rc, elapsed=${elapsed}s, expected=${expected_sec}s) → 再試行上限"
            return "$say_rc"
        fi
        retry=$((retry + 1))
        _set_current_source "retry_wait"
        _log "say異常終了 (rc=$say_rc, elapsed=${elapsed}s, expected=${expected_sec}s) → ${backoff}s後に再試行 ${retry}/${SAY_RETRY_MAX}"
        _sleep_with_heartbeat "$backoff"
        if [ "$backoff" -lt "$SAY_RETRY_MAX_SLEEP_SEC" ]; then
            backoff=$((backoff * 2))
            [ "$backoff" -gt "$SAY_RETRY_MAX_SLEEP_SEC" ] && backoff="$SAY_RETRY_MAX_SLEEP_SEC"
        fi
    done
}

_wait_for_turn() {
    local yield_count=0
    while true; do
        _acquire_lock
        lock_ret=$?
        if [ "$lock_ret" -ne 0 ]; then
            _log "ロック取得失敗 → 諦め"
            exit 0
        fi
        _set_current_source "waiting"
        if _radio_should_yield_to_comment; then
            yield_count=$((yield_count + 1))
            _log "comment backlog を優先するため radio が順番を譲る (${yield_count})"
            _release_lock
            sleep 1
            continue
        fi
        break
    done
}

_prepare_playback_turn() {
    local pre_delay="${1:-0}" waited_pre prev_pid=""
    while true; do
        _wait_for_turn

        if [ -f "$PID_FILE" ]; then
            prev_pid=$(cat "$PID_FILE" 2>/dev/null)
            if [ -n "$prev_pid" ] && kill -0 "$prev_pid" 2>/dev/null; then
                _log "前のsay (PID=$prev_pid) がまだ再生中 → 終了待ち"
                while kill -0 "$prev_pid" 2>/dev/null; do
                    if _radio_should_yield_to_comment; then
                        _yield_turn_to_pending_comment
                        continue 2
                    fi
                    _touch_lock_heartbeat
                    sleep 1
                done
            fi
            rm -f "$PID_FILE"
        fi

        # 孤児say/afplayプロセス検出: ロック取得済み＝前の所有者は死んでいるので、残留プロセスはkillして進む
        local _orphan_pids _orphan_wait=0
        while true; do
            _orphan_pids=$(pgrep -x 'say|afplay' 2>/dev/null || true)
            if [ -z "$_orphan_pids" ]; then
                _orphan_pids=$(pgrep -xf "ffmpeg.*audiotoolbox" 2>/dev/null || true)
                [ -z "$_orphan_pids" ] && break
            fi
            if [ "$_orphan_wait" -ge 3 ]; then
                _log "残留say/ffmpegプロセス検出 → kill: $_orphan_pids"
                echo "$_orphan_pids" | xargs kill 2>/dev/null || true
                sleep 1
                echo "$_orphan_pids" | xargs kill -9 2>/dev/null || true
                break
            fi
            [ "$_orphan_wait" -eq 0 ] && _log "既存sayプロセス検出 → 短時間待機後にkill"
            _touch_lock_heartbeat
            sleep 1
            _orphan_wait=$((_orphan_wait + 1))
        done

        _set_current_source "waiting"
        _log "トーク開始まで ${pre_delay}秒 待機..."
        waited_pre=0
        while [ "$waited_pre" -lt "$pre_delay" ]; do
            if _radio_should_yield_to_comment; then
                _yield_turn_to_pending_comment
                continue 2
            fi
            _touch_lock_heartbeat
            sleep 1
            waited_pre=$((waited_pre + 1))
        done

        if _radio_should_yield_to_comment; then
            _yield_turn_to_pending_comment
            continue
        fi
        return 0
    done
}

# --- mkdirロックで排他制御 ---
PRE_DELAY="${3:-60}"
_prepare_playback_turn "$PRE_DELAY"

# --- ロック内: say再生（単発 + 自動リトライ） ---
PLAYBACK_FAILED=0
LAST_SAY_PID=""
if ! _play_with_retry; then
    PLAYBACK_FAILED=1
fi

# ロック解放（say完了後）
_release_lock

if [ "$PLAYBACK_FAILED" -eq 1 ]; then
    _log "say終了 (一部失敗あり)"
    _append_played_log "failed"
else
    _log "say終了"
    _append_played_log "played"
fi
# 自分のPIDの場合のみ削除（他プロセスが上書きした場合は残す）
[ -n "$LAST_SAY_PID" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$LAST_SAY_PID" ] && rm -f "$PID_FILE"
exit "$PLAYBACK_FAILED"
