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

CHAT_DIR="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
mkdir -p "$CHAT_DIR"

RAW_LOG="$CHAT_DIR/raw.log"        # デーモンが追記するログ
PID_FILE="$CHAT_DIR/daemon.pid"
OFFSET_FILE="$CHAT_DIR/last_offset" # 前回fetchした行数
PENDING_LOG="$CHAT_DIR/pending.log"  # 未読み上げキュー
OUTFILE="${TWITCH_CHAT_OUTFILE:-tmp/twitch_comments.txt}"
SEEN_ID_FILE="$CHAT_DIR/seen_msg_ids.log" # 直近に処理済みのTwitch msg-id
SEEN_ID_MAX=4000
TAB=$'\t'
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

_sanitize_comment_line() {
    local line="$1"
    [ -n "$line" ] || return 1
    # シェルメタ文字の除去（デーモン側で漏れた場合の保険）
    line=$(printf '%s' "$line" | tr -d '`$\\{}|;<>&')
    line=$(printf '%s' "$line" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
    [ -n "$line" ] || return 1

    # 危険パターンを除去: プロンプトインジェクション対策
    if printf '%s\n' "$line" | grep -Eiq 'ignore.*instruction|forget.*instruction|override.*prompt|pretend.*you|act as ai|無視.*指示|指示.*無視|命令.*無視|忘れ.*指示|ふりをし|なりきり|プロンプトインジェクション'; then
        return 1
    fi
    # AI操作系の危険パターンを除去
    if printf '%s\n' "$line" | grep -Eiq 'sudo|chmod|rm -rf|eval\(|exec\(|ファイル.*削除|コマンド.*実行|スクリプト.*実行|上書き.*ファイル'; then
        return 1
    fi

    printf '%s' "$line"
    return 0
}

_compact_seen_ids() {
    [ -f "$SEEN_ID_FILE" ] || return 0
    local tmpf
    tmpf=$(mktemp /tmp/twitch_seen_ids_XXXXXXXX)
    awk 'NF && !seen[$0]++' "$SEEN_ID_FILE" | tail -n "$SEEN_ID_MAX" > "$tmpf"
    mv "$tmpf" "$SEEN_ID_FILE"
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

            # 最新10件に制限したうえで、msg-id重複と危険入力を除外
            local scan_tmp seen_batch_tmp dedup_tmp
            local skipped_by_id=0 skipped_by_sanitize=0 skipped_by_line=0 added_count=0
            scan_tmp=$(mktemp /tmp/twitch_new_scan_XXXXXXXX)
            seen_batch_tmp=$(mktemp /tmp/twitch_seen_batch_XXXXXXXX)
            dedup_tmp=$(mktemp /tmp/twitch_new_dedup_XXXXXXXX)
            : > "$scan_tmp"
            : > "$seen_batch_tmp"
            [ -f "$SEEN_ID_FILE" ] || : > "$SEEN_ID_FILE"

            while IFS= read -r raw_line; do
                [ -n "$raw_line" ] || continue

                local msg_id="" comment_line="$raw_line"
                # 新形式: id=<twitch-msg-id>\t<display>: <message>
                if [[ "$raw_line" == id=*"$TAB"* ]]; then
                    msg_id="${raw_line%%"$TAB"*}"
                    msg_id="${msg_id#id=}"
                    comment_line="${raw_line#*"$TAB"}"
                    case "$msg_id" in
                    ''|*[!0-9A-Za-z-]*)
                        msg_id=""
                        ;;
                    esac
                fi

                local clean_line=""
                clean_line=$(_sanitize_comment_line "$comment_line")
                if [ -z "$clean_line" ]; then
                    skipped_by_sanitize=$((skipped_by_sanitize + 1))
                    continue
                fi

                if [ -n "$msg_id" ]; then
                    if grep -qxF "$msg_id" "$seen_batch_tmp" 2>/dev/null || \
                        grep -qxF "$msg_id" "$SEEN_ID_FILE" 2>/dev/null; then
                        skipped_by_id=$((skipped_by_id + 1))
                        continue
                    fi
                    echo "$msg_id" >> "$seen_batch_tmp"
                fi

                echo "$clean_line" >> "$scan_tmp"
            done <<<"$(printf '%s\n' "$new_comments" | tail -10)"

            # 同一行の重複を除去（多重接続/再送対策）
            if [ -s "$scan_tmp" ]; then
                local before_count after_count
                before_count=$(wc -l < "$scan_tmp" | tr -d ' ')
                awk 'NF && !seen[$0]++' "$scan_tmp" > "$dedup_tmp"
                after_count=$(wc -l < "$dedup_tmp" | tr -d ' ')
                skipped_by_line=$((before_count - after_count))
                if [ "$after_count" -gt 0 ]; then
                    cat "$dedup_tmp" >> "$PENDING_LOG"
                    added_count="$after_count"
                fi
            fi

            # 直近に処理したmsg-idを永続化（再接続再送対策）
            if [ -s "$seen_batch_tmp" ]; then
                cat "$seen_batch_tmp" >> "$SEEN_ID_FILE"
                _compact_seen_ids
            fi

            rm -f "$scan_tmp" "$seen_batch_tmp" "$dedup_tmp"

            if [ "${added_count:-0}" -gt 0 ] || [ "$skipped_by_id" -gt 0 ]; then
                _log "fetch: $((current_lines - last_offset))件中 ${added_count}件追加 (id重複:${skipped_by_id}, 内容重複:${skipped_by_line}, sanitize除外:${skipped_by_sanitize})"
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
