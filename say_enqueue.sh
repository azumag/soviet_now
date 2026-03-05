#!/bin/bash
# say_enqueue.sh - mkdirロックベースのsayキュー（最新が勝つ・プリエンプション付き）
#
# 使い方: ./say_enqueue.sh [--no-preempt] <content_file> [rate] [pre_delay_sec]
#
# --no-preempt: プリエンプションチェックをスキップ（コメント読み上げ等、途中で切られたくない場合）
#
# 動作:
#   1. コンテンツコピー + トークン登録
#   2. mkdirロック取得（待機 + プリエンプション確認）
#   3. ロック内: 前のsay PID待ち + プリエンプション確認
#   4. ロック内: nohup say起動 + PID記録
#   5. ロック解放
#   6. say完了待ち + クリーンアップ

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

PID_FILE="$QUEUE_DIR/pid"
TOKEN_FILE="$QUEUE_DIR/token"
LOCK_DIR="$QUEUE_DIR/.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner_pid"
LOCK_HEARTBEAT_FILE="$LOCK_DIR/heartbeat"

if [ ! -s "$CONTENT_FILE" ]; then
    echo "[say_enqueue] content file missing or empty: $CONTENT_FILE" >&2
    exit 1
fi

# ユニークトークン（PID + ランダム + 秒 で衝突回避）
MY_TOKEN="${BASHPID:-$$}_${RANDOM}_$(date +%s)"
MY_CONTENT="$QUEUE_DIR/content_${MY_TOKEN}.txt"

# コンテンツをキュー用にコピー（元ファイルが消されても安全）
cp "$CONTENT_FILE" "$MY_CONTENT"

# 読み上げ修正: "AI" → "エーアイ"
sed -i '' 's/AI/エーアイ/g' "$MY_CONTENT"

# トークン登録（通常リクエストのみ）
# --no-preempt は「途中で切られない」用途なので、
# 他の通常リクエストをプリエンプトしないようトークンを更新しない。
if [ "$NO_PREEMPT" = false ]; then
    echo "$MY_TOKEN" > "$TOKEN_FILE"
fi

_log() { echo "[say_enqueue $(date '+%H:%M:%S')] $*" >&2; echo "[say_enqueue $(date '+%H:%M:%S') PID=$$/${BASHPID:-?}] $* | file=$CONTENT_FILE token=$MY_TOKEN" >> tmp/.say_queue/debug.log; }

_is_preempted() {
    [ "$NO_PREEMPT" = true ] && return 1
    [ "$(cat "$TOKEN_FILE" 2>/dev/null)" != "$MY_TOKEN" ]
}

_touch_lock_heartbeat() {
    [ -d "$LOCK_DIR" ] || return 0
    echo "${BASHPID:-$$}" > "$LOCK_OWNER_FILE" 2>/dev/null || true
    date +%s > "$LOCK_HEARTBEAT_FILE" 2>/dev/null || true
}

# mkdirロック: アトミックな排他制御（macOS互換）
_acquire_lock() {
    # --no-preempt: 必ず再生したいのでタイムアウトなし
    # 通常: 30秒でタイムアウト
    local max_wait=60 waited=0
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        # stale lock検出: 所有PIDが死んでおり、heartbeatも古い場合のみ強制解除
        if [ -d "$LOCK_DIR" ]; then
            local lock_owner lock_hb now lock_age owner_alive=false
            lock_owner=$(cat "$LOCK_OWNER_FILE" 2>/dev/null || true)
            if [ -n "$lock_owner" ] && kill -0 "$lock_owner" 2>/dev/null; then
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
            if [ "$owner_alive" = false ] && [ "$lock_age" -gt 30 ]; then
                _log "stale lock検出 (owner=${lock_owner:-?}, ${lock_age}秒) → 強制解除"
                rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
                rmdir "$LOCK_DIR" 2>/dev/null
                continue
            fi
        fi
        if [ "$NO_PREEMPT" = false ] && [ "$waited" -ge "$max_wait" ]; then
            return 1
        fi
        if _is_preempted; then
            return 2
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    _touch_lock_heartbeat
    return 0
}

_release_lock() {
    rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
    rmdir "$LOCK_DIR" 2>/dev/null
}

# クリーンアップ: 終了時にロック解放 + 自分のコンテンツ削除
_cleanup() {
    _release_lock
    rm -f "$MY_CONTENT"
}
trap '_cleanup' EXIT

_log "queued (token=${MY_TOKEN})"

# --- mkdirロックで排他制御 ---
_acquire_lock
lock_ret=$?
if [ "$lock_ret" -ne 0 ]; then
    if [ "$lock_ret" -eq 2 ]; then
        _log "ロック待ち中にプリエンプト → 諦め"
    else
        _log "ロック取得タイムアウト → 諦め"
    fi
    exit 0
fi

# --- ロック内: 前のsayが残っていたら待つ ---
if [ -f "$PID_FILE" ]; then
    PREV_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PREV_PID" ] && kill -0 "$PREV_PID" 2>/dev/null; then
        _log "前のsay (PID=$PREV_PID) がまだ再生中 → 終了待ち"
        while kill -0 "$PREV_PID" 2>/dev/null; do
            _touch_lock_heartbeat
            if _is_preempted; then
                _log "say待ち中にプリエンプト → 諦め"
                exit 0
            fi
            sleep 1
        done
    fi
    rm -f "$PID_FILE"
fi

# --- ロック内: 最終プリエンプションチェック ---
if _is_preempted; then
    _log "最終チェックでプリエンプト → 諦め"
    exit 0
fi

# --- ロック内: 既存sayプロセス終了待ち（PIDファイル漏れ対策） ---
# --no-preempt は「絶対に重ねない」ことを優先し、全sayが終わるまで待機を継続する。
# （過去の30秒タイムアウト切替は、コメントとラジオの同時再生を引き起こすことがあるため廃止）
_say_pgrep_wait=0
while pgrep -x say >/dev/null 2>&1; do
    _touch_lock_heartbeat
    if _is_preempted; then
        _log "say待機中にプリエンプト → 諦め"
        exit 0
    fi
    [ "${_say_wait_logged:-0}" -eq 0 ] && _log "既存sayプロセス検出 → 終了待ち" && _say_wait_logged=1
    sleep 1
    _say_pgrep_wait=$((_say_pgrep_wait + 1))
done

# --- ロック内: トーク開始前の間（ロック外でやると他がすり抜けるのでロック内で） ---
PRE_DELAY="${3:-60}"
_log "トーク開始まで ${PRE_DELAY}秒 待機..."
waited_pre=0
while [ "$waited_pre" -lt "$PRE_DELAY" ]; do
    _touch_lock_heartbeat
    if _is_preempted; then
        _log "待機中にプリエンプト → 諦め"
        exit 0
    fi
    sleep 1
    waited_pre=$((waited_pre + 1))
done

# --- ロック内: say開始 + PID記録（アトミック） ---
_log "say開始 (rate=${RATE})"
nohup say -r "$RATE" -f "$MY_CONTENT" > /dev/null 2>&1 &
SAY_PID=$!
echo "$SAY_PID" > "$PID_FILE"

# sayの完了をロック内で待つ（他プロセスのsay起動を確実にブロック）
# ロックを保持し続けることで、二重再生を構造的に防止する
while kill -0 "$SAY_PID" 2>/dev/null; do
    _touch_lock_heartbeat
    sleep 2
done

# ロック解放（say完了後）
_release_lock

_log "say終了"
# 自分のPIDの場合のみ削除（他プロセスが上書きした場合は残す）
[ "$(cat "$PID_FILE" 2>/dev/null)" = "$SAY_PID" ] && rm -f "$PID_FILE"
