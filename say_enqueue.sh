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
#   4. ロック内: 文単位で say 再生（異常終了時はリトライ）
#   5. ロック解放
#   6. クリーンアップ

set -uo pipefail
cd "$(dirname "$0")"

# --no-preempt フラグ処理
NO_PREEMPT=false
if [ "${1:-}" = "--no-preempt" ]; then
    NO_PREEMPT=true
    shift
fi

QUEUE_DIR="tmp/.say_queue"
mkdir -p "$QUEUE_DIR"

CONTENT_FILE="${1:?Usage: say_enqueue.sh [--no-preempt] <content_file> [rate]}"
RATE="${2:-120}"
SAY_AUDIO_DEVICE="${SAY_AUDIO_DEVICE:-}"
SAY_VOICE="${SAY_VOICE:-}"
SAY_RETRY_MAX="${SAY_RETRY_MAX:-6}"
SAY_RETRY_SLEEP_SEC="${SAY_RETRY_SLEEP_SEC:-2}"
SAY_RETRY_MAX_SLEEP_SEC="${SAY_RETRY_MAX_SLEEP_SEC:-20}"

PID_FILE="$QUEUE_DIR/pid"
LOCK_DIR="$QUEUE_DIR/.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner_pid"
LOCK_HEARTBEAT_FILE="$LOCK_DIR/heartbeat"
LOCK_STALE_SEC=180

if [ ! -s "$CONTENT_FILE" ]; then
    echo "[say_enqueue] content file missing or empty: $CONTENT_FILE" >&2
    exit 1
fi

# ユニークトークン（PID + ランダム + 秒 で衝突回避）
MY_TOKEN="${BASHPID:-$$}_${RANDOM}_$(date +%s)"
MY_OWNER="${BASHPID:-$$}:${MY_TOKEN}"
MY_CONTENT="$QUEUE_DIR/content_${MY_TOKEN}.txt"
MY_CHUNK_DIR="$QUEUE_DIR/chunks_${MY_TOKEN}"
MY_CHUNK_LIST="$MY_CHUNK_DIR/chunks.txt"
LOCK_HELD=0
LAUNCHED_SAY_PID=""

# コンテンツをキュー用にコピー（元ファイルが消されても安全）
cp "$CONTENT_FILE" "$MY_CONTENT"
mkdir -p "$MY_CHUNK_DIR"

# 読み上げ修正: "AI" → "エーアイ"
sed -i '' 's/AI/エーアイ/g' "$MY_CONTENT"

_log() { echo "[say_enqueue $(date '+%H:%M:%S')] $*" >&2; echo "[say_enqueue $(date '+%H:%M:%S') PID=$$/${BASHPID:-?}] $* | file=$CONTENT_FILE token=$MY_TOKEN" >> tmp/.say_queue/debug.log; }

_is_lock_owner() {
    [ -d "$LOCK_DIR" ] || return 1
    [ "$(cat "$LOCK_OWNER_FILE" 2>/dev/null || true)" = "$MY_OWNER" ]
}

_touch_lock_heartbeat() {
    _is_lock_owner || return 0
    echo "$MY_OWNER" > "$LOCK_OWNER_FILE" 2>/dev/null || true
    date +%s > "$LOCK_HEARTBEAT_FILE" 2>/dev/null || true
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
    rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
    rmdir "$LOCK_DIR" 2>/dev/null
    LOCK_HELD=0
}

# クリーンアップ: 終了時にロック解放 + 自分のコンテンツ削除
_cleanup() {
    _release_lock
    rm -f "$MY_CONTENT"
    rm -rf "$MY_CHUNK_DIR"
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

_prepare_chunks() {
    # 文末記号でざっくり分割し、落ちた際の再開単位を小さくする
    perl -CSDA -pe 's/\r//g; s/\n/ /g; s/\s+/ /g; s/([。！？.!?])\s*/$1\n/g' "$MY_CONTENT" |
        sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' |
        sed '/^[[:space:]]*$/d' >"$MY_CHUNK_LIST"
    if [ ! -s "$MY_CHUNK_LIST" ]; then
        sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' "$MY_CONTENT" |
            sed '/^[[:space:]]*$/d' >"$MY_CHUNK_LIST"
    fi
}

_launch_say() {
    local chunk_file="$1"
    local -a cmd=(say -r "$RATE")
    [ -n "$SAY_VOICE" ] && cmd+=(-v "$SAY_VOICE")
    [ -n "$SAY_AUDIO_DEVICE" ] && cmd+=(-a "$SAY_AUDIO_DEVICE")
    cmd+=(-f "$chunk_file")
    nohup bash -c 'trap "" INT TERM; "$@"' _ "${cmd[@]}" >/dev/null 2>&1 &
    LAUNCHED_SAY_PID="$!"
}

_play_chunk_with_retry() {
    local chunk_file="$1" chunk_idx="$2" chunk_total="$3"
    local retry=0 backoff="$SAY_RETRY_SLEEP_SEC"
    while true; do
        local attempt=$((retry + 1))
        _log "say開始 (chunk=${chunk_idx}/${chunk_total}, attempt=${attempt}, rate=${RATE})"
        local say_pid
        LAUNCHED_SAY_PID=""
        _launch_say "$chunk_file"
        say_pid="${LAUNCHED_SAY_PID:-}"
        if [ -z "$say_pid" ]; then
            _log "say起動失敗 (chunk=${chunk_idx}/${chunk_total})"
            return 1
        fi
        echo "$say_pid" > "$PID_FILE"
        local start_ts now_ts elapsed say_rc
        start_ts=$(date +%s)
        while kill -0 "$say_pid" 2>/dev/null; do
            _touch_lock_heartbeat
            sleep 1
        done
        wait "$say_pid"
        say_rc=$?
        now_ts=$(date +%s)
        elapsed=$((now_ts - start_ts))
        if [ "$say_rc" -eq 0 ]; then
            return 0
        fi
        if [ "$retry" -ge "$SAY_RETRY_MAX" ]; then
            _log "say異常終了 (chunk=${chunk_idx}/${chunk_total}, rc=$say_rc, elapsed=${elapsed}s) → 再試行上限"
            return "$say_rc"
        fi
        retry=$((retry + 1))
        _log "say異常終了 (chunk=${chunk_idx}/${chunk_total}, rc=$say_rc, elapsed=${elapsed}s) → ${backoff}s後に再試行 ${retry}/${SAY_RETRY_MAX}"
        _sleep_with_heartbeat "$backoff"
        if [ "$backoff" -lt "$SAY_RETRY_MAX_SLEEP_SEC" ]; then
            backoff=$((backoff * 2))
            [ "$backoff" -gt "$SAY_RETRY_MAX_SLEEP_SEC" ] && backoff="$SAY_RETRY_MAX_SLEEP_SEC"
        fi
    done
}

# --- mkdirロックで排他制御 ---
_acquire_lock
lock_ret=$?
if [ "$lock_ret" -ne 0 ]; then
    _log "ロック取得失敗 → 諦め"
    exit 0
fi

# --- ロック内: 前のsayが残っていたら待つ ---
if [ -f "$PID_FILE" ]; then
    PREV_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PREV_PID" ] && kill -0 "$PREV_PID" 2>/dev/null; then
        _log "前のsay (PID=$PREV_PID) がまだ再生中 → 終了待ち"
        while kill -0 "$PREV_PID" 2>/dev/null; do
            _touch_lock_heartbeat
            sleep 1
        done
    fi
    rm -f "$PID_FILE"
fi

# --- ロック内: 既存sayプロセス終了待ち（PIDファイル漏れ対策） ---
# 絶対に重ねないことを優先し、全sayが終わるまで待機する
while pgrep -x say >/dev/null 2>&1; do
    _touch_lock_heartbeat
    [ "${_say_wait_logged:-0}" -eq 0 ] && _log "既存sayプロセス検出 → 終了待ち" && _say_wait_logged=1
    sleep 1
done

# --- ロック内: トーク開始前の間（ロック外でやると他がすり抜けるのでロック内で） ---
PRE_DELAY="${3:-60}"
_log "トーク開始まで ${PRE_DELAY}秒 待機..."
waited_pre=0
while [ "$waited_pre" -lt "$PRE_DELAY" ]; do
    _touch_lock_heartbeat
    sleep 1
    waited_pre=$((waited_pre + 1))
done

# --- ロック内: say再生（文単位 + 自動リトライ） ---
_prepare_chunks
if [ ! -s "$MY_CHUNK_LIST" ]; then
    _log "再生スキップ: チャンク生成結果が空"
    _release_lock
    exit 1
fi

CHUNK_TOTAL=$(awk 'END { print NR + 0 }' "$MY_CHUNK_LIST")
CHUNK_IDX=0
PLAYBACK_FAILED=0
LAST_SAY_PID=""
while IFS= read -r chunk_line || [ -n "$chunk_line" ]; do
    [ -n "${chunk_line//[[:space:]]/}" ] || continue
    CHUNK_IDX=$((CHUNK_IDX + 1))
    CHUNK_FILE="$MY_CHUNK_DIR/chunk_${CHUNK_IDX}.txt"
    printf '%s\n' "$chunk_line" > "$CHUNK_FILE"
    if ! _play_chunk_with_retry "$CHUNK_FILE" "$CHUNK_IDX" "$CHUNK_TOTAL"; then
        PLAYBACK_FAILED=1
        break
    fi
    LAST_SAY_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
done < "$MY_CHUNK_LIST"

# ロック解放（say完了後）
_release_lock

if [ "$PLAYBACK_FAILED" -eq 1 ]; then
    _log "say終了 (一部失敗あり)"
else
    _log "say終了"
fi
# 自分のPIDの場合のみ削除（他プロセスが上書きした場合は残す）
[ -n "$LAST_SAY_PID" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$LAST_SAY_PID" ] && rm -f "$PID_FILE"
exit "$PLAYBACK_FAILED"
