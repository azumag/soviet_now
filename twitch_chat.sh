#!/bin/bash
# twitch_chat.sh - Twitch チャット常駐デーモン + 差分フェッチ
#
# 使い方:
#   ./twitch_chat.sh start [channel]   - バックグラウンドでIRC常駐開始
#   ./twitch_chat.sh fetch             - 前回fetch以降の新コメントを取得 → tmp/twitch_comments.txt
#   ./twitch_chat.sh stop              - デーモン停止
#   ./twitch_chat.sh status            - 動作状況表示

set -uo pipefail
cd "$(dirname "$0")"

CHAT_DIR="tmp/.twitch_chat"
mkdir -p "$CHAT_DIR"

RAW_LOG="$CHAT_DIR/raw.log"        # デーモンが追記するログ
PID_FILE="$CHAT_DIR/daemon.pid"
OFFSET_FILE="$CHAT_DIR/last_offset" # 前回fetchした行数
OUTFILE="tmp/twitch_comments.txt"

CMD="${1:-fetch}"
CHANNEL="${2:-azumagbanjo}"

_log() { echo "[twitch_chat $(date '+%H:%M:%S')] $*" >&2; }

#--- デーモン: IRC常駐 + PING/PONG + 自動再接続 ---
_daemon() {
    local channel="$1"
    _log "daemon start (channel=#${channel})"

    while true; do
        local nick="justinfan$((RANDOM % 90000 + 10000))"
        _log "IRC接続中... (nick=$nick)"

        # 名前付きパイプで双方向通信
        local fifo="$CHAT_DIR/irc_fifo"
        rm -f "$fifo"
        mkfifo "$fifo"

        # nc をバックグラウンドで起動、fifoから入力を受ける
        nc irc.chat.twitch.tv 6667 < "$fifo" 2>/dev/null | while IFS= read -r line; do
            # PING/PONG処理
            if [[ "$line" == PING* ]]; then
                echo "PONG :tmi.twitch.tv" > "$fifo" 2>/dev/null
                continue
            fi

            # PRIVMSG を抽出して raw.log に追記
            if [[ "$line" == *"PRIVMSG"* ]]; then
                local user msg
                user=$(echo "$line" | sed 's/^:\([^!]*\)!.*/\1/')
                msg=$(echo "$line" | sed 's/^.*PRIVMSG [^ ]* ://')
                # 制御文字除去
                msg=$(echo "$msg" | tr -d '\000-\010\013-\037\r')
                echo "${user}: ${msg}" >> "$RAW_LOG"
            fi
        done &
        local reader_pid=$!

        # IRC認証・JOIN送信
        {
            echo "NICK $nick"
            echo "JOIN #${channel}"
        } > "$fifo"

        # readerが死ぬまで待つ（= 接続切断）
        wait "$reader_pid" 2>/dev/null
        rm -f "$fifo"

        _log "IRC切断 → 10秒後に再接続"
        sleep 10
    done
}

#--- start ---
_start() {
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

    # ログ初期化
    > "$RAW_LOG"
    echo "0" > "$OFFSET_FILE"

    # デーモン起動
    _daemon "$CHANNEL" &
    local dpid=$!
    echo "$dpid" > "$PID_FILE"
    disown "$dpid"
    _log "daemon起動 (PID=$dpid)"
}

#--- fetch: 前回からの差分を取得してサニタイズ ---
_fetch() {
    if [ ! -f "$RAW_LOG" ]; then
        rm -f "$OUTFILE"
        return 0
    fi

    local last_offset
    last_offset=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
    local current_lines
    current_lines=$(wc -l < "$RAW_LOG" | tr -d ' ')

    if [ "$current_lines" -le "$last_offset" ]; then
        # 新コメントなし
        rm -f "$OUTFILE"
        return 0
    fi

    # 差分行を取得
    local new_comments
    new_comments=$(tail -n "+$((last_offset + 1))" "$RAW_LOG")

    # オフセット更新
    echo "$current_lines" > "$OFFSET_FILE"

    # サニタイズ
    local sanitized
    sanitized=$(echo "$new_comments" | \
        # 1行80文字に切り詰め
        cut -c1-80 | \
        # 危険パターンを除去: プロンプトインジェクション対策
        grep -iv 'ignore\|forget\|instruction\|system\|prompt\|override\|pretend\|act as\|you are\|あなたは\|無視\|命令\|指示\|忘れ\|ふりをし\|なりきり\|プロンプト\|システム' | \
        # 最新10件に制限
        tail -10)

    if [ -n "$sanitized" ]; then
        echo "$sanitized" > "$OUTFILE"
        _log "fetch: $((current_lines - last_offset))件中 $(echo "$sanitized" | wc -l | tr -d ' ')件取得"
    else
        rm -f "$OUTFILE"
        _log "fetch: 新コメントなし（フィルタ後）"
    fi
}

#--- stop ---
_stop() {
    if [ -f "$PID_FILE" ]; then
        local dpid
        dpid=$(cat "$PID_FILE")
        if kill -0 "$dpid" 2>/dev/null; then
            # 子プロセス(nc等)ごと終了
            pkill -P "$dpid" 2>/dev/null
            kill "$dpid" 2>/dev/null
            wait "$dpid" 2>/dev/null
            _log "daemon停止 (PID=$dpid)"
        fi
        rm -f "$PID_FILE"
    fi
    rm -f "$CHAT_DIR/irc_fifo"
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
    stop)   _stop ;;
    status) _status ;;
    *)      echo "Usage: $0 {start|fetch|stop|status} [channel]" >&2; exit 1 ;;
esac
