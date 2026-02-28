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

CMD="${1:-fetch}"
CHANNEL="${2:-azumagbanjo}"

_log() { echo "[twitch_chat $(date '+%H:%M:%S')] $*" >&2; }

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
_fetch() {
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
                # 1行80文字に切り詰め
                cut -c1-80 | \
                # シェルメタ文字の二重除去（デーモン側で漏れた場合の保険）
                tr -d '`$\\{}|;<>&' | \
                # 危険パターンを除去: プロンプトインジェクション対策
                grep -iv 'ignore\|forget\|instruction\|system\|prompt\|override\|pretend\|act as\|you are\|あなたは\|無視\|命令\|指示\|忘れ\|ふりをし\|なりきり\|プロンプト\|システム' | \
                # AI操作系の危険パターンを除去
                grep -iv 'delete\|remove\|modify\|write\|create\|execute\|run\|sudo\|chmod\|kill\|削除\|消し\|書き換\|変更\|実行\|作成\|上書\|破壊\|ファイル\|コマンド\|スクリプト' | \
                # 最新10件に制限
                tail -10)

            # 新規サニタイズ済みコメントをpending.logに追記
            if [ -n "$new_sanitized" ]; then
                echo "$new_sanitized" >> "$PENDING_LOG"
                _log "fetch: $((current_lines - last_offset))件中 $(echo "$new_sanitized" | wc -l | tr -d ' ')件を新規追加"
            fi
        fi
    fi

    # pending.log全体（前回未ack分 + 今回新規分）をOUTFILEに出力
    if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
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

#--- ack: 読み上げ完了後にpending.logをクリア ---
_ack() {
    if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
        local count
        count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
        > "$PENDING_LOG"
        _log "ack: ${count}件の読み上げ完了を確認、pending.logクリア"
    else
        _log "ack: pending.logは空"
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
    stop)   _stop ;;
    status) _status ;;
    *)      echo "Usage: $0 {start|fetch|ack|stop|status} [channel]" >&2; exit 1 ;;
esac
