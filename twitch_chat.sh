#!/bin/bash
# twitch_chat.sh - Twitch チャット常駐デーモン + 差分フェッチ
#
# 使い方:
#   ./twitch_chat.sh start [channel]   - バックグラウンドでIRC常駐開始
#   ./twitch_chat.sh fetch             - 前回fetch以降の新コメントを取得 → tmp/twitch_comments.txt
#   ./twitch_chat.sh ack               - 読み上げ完了後に呼ぶ。pending.logをクリア
#   ./twitch_chat.sh stop              - デーモン停止
#   ./twitch_chat.sh status            - 動作状況表示

cd "$(dirname "$0")"

CHAT_DIR="tmp/.twitch_chat"
mkdir -p "$CHAT_DIR"

RAW_LOG="$CHAT_DIR/raw.log"        # デーモンが追記するログ
PID_FILE="$CHAT_DIR/daemon.pid"
OFFSET_FILE="$CHAT_DIR/last_offset" # 前回fetchした行数
PENDING_LOG="$CHAT_DIR/pending.log"  # 未読み上げキュー
OUTFILE="tmp/twitch_comments.txt"
LOCK_DIR="$CHAT_DIR/.op_lock"
LOCK_TIMEOUT_SEC=8
LOCK_STALE_SEC=120

CMD="${1:-fetch}"
CHANNEL="${2:-azumagbanjo}"

_log() { echo "[twitch_chat $(date '+%H:%M:%S')] $*" >&2; }

_release_lock() {
    [ -d "$LOCK_DIR" ] || return 0
    local lock_pid
    lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
    if [ -z "$lock_pid" ] || [ "$lock_pid" = "$$" ]; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
}

_acquire_lock() {
    local op_name="${1:-op}"
    local start_ts now_ts lock_age lock_pid
    start_ts=$(date +%s)
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        # 孤立ロック回収: PID死亡 or ロックが古すぎる
        if [ -f "$LOCK_DIR/pid" ]; then
            lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
            if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
                rm -rf "$LOCK_DIR" 2>/dev/null || true
                continue
            fi
        fi
        now_ts=$(date +%s)
        lock_age=$((now_ts - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "$now_ts")))
        if [ "$lock_age" -ge "$LOCK_STALE_SEC" ]; then
            rm -rf "$LOCK_DIR" 2>/dev/null || true
            continue
        fi
        if [ $((now_ts - start_ts)) -ge "$LOCK_TIMEOUT_SEC" ]; then
            _log "${op_name}: lock timeout"
            return 1
        fi
        sleep 0.1
    done
    echo "$$" > "$LOCK_DIR/pid" 2>/dev/null || true
    return 0
}

_with_chat_lock() {
    local op_name="$1"
    shift
    _acquire_lock "$op_name" || return 1
    "$@"
    local rc=$?
    _release_lock
    return $rc
}

# 同一チャンネルの daemon PID を列挙
_daemon_pids() {
    ps -Ao pid=,command= 2>/dev/null | awk -v ch="$CHANNEL" '
        $0 ~ /[b]ash[[:space:]]+\.\/twitch_chat_daemon\.sh/ && $0 ~ ("[[:space:]]" ch "([[:space:]]|$)") {print $1}
    '
}

#--- start ---
_start() {
    local running_pids
    running_pids=$(_daemon_pids)
    if [ -n "$running_pids" ]; then
        local pid_count
        pid_count=$(printf '%s\n' "$running_pids" | sed '/^$/d' | wc -l | tr -d ' ')
        if [ "$pid_count" -gt 1 ]; then
            _log "daemon多重起動を検出 (${pid_count}件) → 既存を停止して再起動"
            local pid
            for pid in $running_pids; do
                pkill -P "$pid" 2>/dev/null
                kill "$pid" 2>/dev/null
                wait "$pid" 2>/dev/null
            done
            running_pids=""
        fi
    fi

    if [ -n "$running_pids" ]; then
        local keep_pid
        keep_pid=$(printf '%s\n' "$running_pids" | head -n1)
        echo "$keep_pid" > "$PID_FILE"
        _log "既存daemonを継続利用 (PID=$keep_pid)"
        return 0
    fi

    # 既に動いていたら何もしない
    if [ -f "$PID_FILE" ]; then
        local old_pid
        old_pid=$(cat "$PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            _log "既に起動中 (PID=$old_pid)"
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    # ログファイルが無い場合のみ作成（再起動時は既存ログを引き継ぐ）
    [ -f "$RAW_LOG" ] || touch "$RAW_LOG"
    [ -f "$OFFSET_FILE" ] || echo "0" > "$OFFSET_FILE"

    # デーモンを別スクリプトとしてバックグラウンド起動
    nohup bash ./twitch_chat_daemon.sh "$CHANNEL" > /dev/null 2>&1 &
    local dpid=$!
    echo "$dpid" > "$PID_FILE"
    _log "daemon起動 (PID=$dpid)"
}

#--- fetch: 前回からの差分を取得してサニタイズ → pending.logに蓄積 ---
_fetch_nolock() {
    local new_sanitized=""

    # raw.logから新規コメントを取得
    if [ -f "$RAW_LOG" ]; then
        local last_offset
        last_offset=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
        local current_lines
        current_lines=$(wc -l < "$RAW_LOG" | tr -d ' ')

        if [ "$current_lines" -gt "$last_offset" ]; then
            # 差分行を取得
            local new_comments
            new_comments=$(tail -n "+$((last_offset + 1))" "$RAW_LOG")

            # 既読分をログから削除してオフセットリセット
            local remaining
            remaining=$(tail -n "+$((current_lines + 1))" "$RAW_LOG" 2>/dev/null)
            if [ -n "$remaining" ]; then
                echo "$remaining" > "$RAW_LOG"
                echo "$(echo "$remaining" | wc -l | tr -d ' ')" > "$OFFSET_FILE"
            else
                > "$RAW_LOG"
                echo "0" > "$OFFSET_FILE"
            fi

            # サニタイズ
            new_sanitized=$(echo "$new_comments" | \
                # シェルメタ文字の二重除去（デーモン側で漏れた場合の保険）
                tr -d '`$\\{}|;<>&' | \
                # 危険パターンを除去: プロンプトインジェクション対策
                grep -iv 'ignore.*instruction\|forget.*instruction\|override.*prompt\|pretend.*you\|act as ai\|無視.*指示\|指示.*無視\|命令.*無視\|忘れ.*指示\|ふりをし\|なりきり\|プロンプトインジェクション' | \
                # AI操作系の危険パターンを除去
                grep -iv 'sudo\|chmod\|rm -rf\|eval(\|exec(\|ファイル.*削除\|コマンド.*実行\|スクリプト.*実行\|上書き.*ファイル' | \
                # 最新10件に制限
                tail -10)

            # 同一行の重複を除去（多重接続/再送対策）
            if [ -n "$new_sanitized" ]; then
                new_sanitized=$(printf '%s\n' "$new_sanitized" | awk 'NF && !seen[$0]++')
            fi

            # 新規サニタイズ済みコメントをpending.logに追記
            if [ -n "$new_sanitized" ]; then
                echo "$new_sanitized" >> "$PENDING_LOG"
                _log "fetch: $((current_lines - last_offset))件中 $(echo "$new_sanitized" | wc -l | tr -d ' ')件を新規追加"
            fi
        fi
    fi

    # pending.log全体（前回未ack分 + 今回新規分）をOUTFILEに出力
    if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
        # pending全体の同一行も正規化
        local before_count after_count pending_tmp
        before_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
        pending_tmp=$(mktemp /tmp/twitch_pending_dedup_XXXXXXXX)
        awk 'NF && !seen[$0]++' "$PENDING_LOG" > "$pending_tmp"
        mv "$pending_tmp" "$PENDING_LOG"
        after_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
        if [ "${after_count:-0}" -lt "${before_count:-0}" ]; then
            _log "fetch: pending重複を$((before_count - after_count))件除去"
        fi

        # pending.logも最新10件に制限
        tail -10 "$PENDING_LOG" > "$OUTFILE"
        local pending_count
        pending_count=$(wc -l < "$OUTFILE" | tr -d ' ')
        _log "fetch: pending ${pending_count}件を出力"
    else
        rm -f "$OUTFILE"
        _log "fetch: 未読コメントなし"
    fi
}

_fetch() {
    _with_chat_lock "fetch" _fetch_nolock
}

#--- ack: 読み上げ完了後にpending.logをクリア ---
_ack_nolock() {
    if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
        local count
        count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
        > "$PENDING_LOG"
        _log "ack: ${count}件の読み上げ完了を確認、pending.logクリア"
    else
        _log "ack: pending.logは空"
    fi
}

_ack() {
    _with_chat_lock "ack" _ack_nolock
}

#--- claim: fetch + pending snapshot + ack をロック下で一括処理 ---
_claim_nolock() {
    _fetch_nolock
    _ack_nolock
}

_claim() {
    _with_chat_lock "claim" _claim_nolock
}

#--- stop ---
_stop() {
    local stopped=false

    if [ -f "$PID_FILE" ]; then
        local dpid
        dpid=$(cat "$PID_FILE")
        if kill -0 "$dpid" 2>/dev/null; then
            # 子プロセス(nc等)ごと終了
            pkill -P "$dpid" 2>/dev/null
            kill "$dpid" 2>/dev/null
            wait "$dpid" 2>/dev/null
            _log "daemon停止 (PID=$dpid)"
            stopped=true
        fi
        rm -f "$PID_FILE"
    fi

    # PIDファイル管理外の孤児daemonも掃除
    local pid
    for pid in $(_daemon_pids); do
        pkill -P "$pid" 2>/dev/null
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        _log "daemon孤児を停止 (PID=$pid)"
        stopped=true
    done

    if [ "$stopped" = false ]; then
        _log "stop: 対象daemonなし"
    fi
}

#--- status ---
_status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        local lines
        lines=$(wc -l < "$RAW_LOG" 2>/dev/null | tr -d ' ')
        local offset
        offset=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
        echo "running (PID=$(cat "$PID_FILE"), total=${lines:-0}件, unread=$((${lines:-0} - offset))件)"
    else
        echo "stopped"
    fi
}

case "$CMD" in
    start)  _start ;;
    fetch)  _fetch ;;
    ack)    _ack ;;
    claim)  _claim ;;
    stop)   _stop ;;
    status) _status ;;
    *)      echo "Usage: $0 {start|fetch|ack|claim|stop|status} [channel]" >&2; exit 1 ;;
esac
