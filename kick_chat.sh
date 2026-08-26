#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では Kick チャット連携を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# kick_chat.sh - Kick チャット常駐デーモン + 差分フェッチ
#
# 使い方:
#   ./kick_chat.sh start [slug]        - バックグラウンドで Kick チャット常駐開始
#   ./kick_chat.sh fetch               - 前回fetch以降の新コメントを取得 → tmp/kick_comments.txt
#   ./kick_chat.sh ack                 - 読み上げ完了後に呼ぶ。pending.logをクリア
#   ./kick_chat.sh ack-batch <file>    - 処理済みコメント行のみ pending.log から削除
#   ./kick_chat.sh stop                - デーモン停止
#   ./kick_chat.sh status              - 動作状況表示
#
# 受信は読み取り専用 (Kick web クライアントと同じ公開 Pusher チャンネル)。
# 送信は Kick 側 API の認証が別途必要なため、このスクリプトでは扱わない。

cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a

CHAT_DIR="${KICK_CHAT_DIR:-tmp/.kick_chat}"
mkdir -p "$CHAT_DIR"

RAW_LOG="$CHAT_DIR/raw.log"          # デーモンが追記するログ
PID_FILE="$CHAT_DIR/daemon.pid"
OFFSET_FILE="$CHAT_DIR/last_offset"  # 前回fetchした行数
PENDING_LOG="$CHAT_DIR/pending.log"  # 未読み上げキュー
OUTFILE="${KICK_CHAT_OUTFILE:-tmp/kick_comments.txt}"
SEEN_ID_FILE="$CHAT_DIR/seen_msg_ids.log"
SEEN_ID_MAX=4000
SEEN_LINE_HASH_FILE="$CHAT_DIR/seen_line_hashes.log"
SEEN_LINE_MAX=4000
SEEN_LINE_TTL_SEC="${KICK_FETCH_LINE_HASH_TTL_SEC:-900}"
DAEMON_SCRIPT="./kick_chat_daemon.mjs"
TAB=$'\t'
LOCK_DIR="$CHAT_DIR/.op_lock"
LOCK_TIMEOUT_SEC=8
LOCK_STALE_SEC=120

CMD="${1:-fetch}"
CHANNEL="${2:-${KICK_CHANNEL:-dociai}}"

_log() { echo "[kick_chat $(date '+%H:%M:%S')] $*" >&2; }

_pid_alive() {
    local pid="${1:-}" err=""
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
    case "$err" in
        *"operation not permitted"*|*"Operation not permitted"*) return 0 ;;
    esac
    return 1
}

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
    local start_ts now_ts lock_age lock_mtime lock_pid
    start_ts=$(date +%s)
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        if [ -f "$LOCK_DIR/pid" ]; then
            lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
            if [ -n "$lock_pid" ] && ! _pid_alive "$lock_pid"; then
                rm -rf "$LOCK_DIR" 2>/dev/null || true
                continue
            fi
        fi
        now_ts=$(date +%s)
        lock_mtime=$(stat -f %m "$LOCK_DIR" 2>/dev/null) \
            || lock_mtime=$(stat -c %Y "$LOCK_DIR" 2>/dev/null) \
            || lock_mtime="$now_ts"
        lock_age=$((now_ts - lock_mtime))
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

_is_ignored_comment_author_line() {
    local line="$1"
    # dociai = 配信チャンネル本体 (outbound bot の投稿)。視聴者コメントではない。
    local ignored="${KICK_IGNORE_AUTHORS:-dociai DoCiAI}"
    local item
    for item in $ignored; do
        printf '%s\n' "$line" | grep -Fqi -- "${item}: " && return 0
    done
    return 1
}

# twitch_chat.sh と同じ規則。デーモン側でも落としているが、raw.log を直接
# 書き換えられた場合の保険として fetch 側でも同じ検査を通す。
_sanitize_comment_line() {
    local line="$1"
    [ -n "$line" ] || return 1
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

    if _is_ignored_comment_author_line "$line"; then
        return 1
    fi

    printf '%s' "$line"
    return 0
}

_compact_seen_ids() {
    [ -f "$SEEN_ID_FILE" ] || return 0
    local tmpf
    tmpf=$(mktemp "$CHAT_DIR/.seen_ids.XXXXXXXX")
    awk 'NF && !seen[$0]++' "$SEEN_ID_FILE" | tail -n "$SEEN_ID_MAX" > "$tmpf"
    cat "$tmpf" > "$SEEN_ID_FILE"
    rm -f "$tmpf"
}

_compact_seen_line_hashes() {
    [ -f "$SEEN_LINE_HASH_FILE" ] || return 0
    local tmpf now_ts
    now_ts=$(date +%s)
    tmpf=$(mktemp "$CHAT_DIR/.seen_lines.XXXXXXXX")
    awk -F'|' -v now_ts="$now_ts" -v ttl="$SEEN_LINE_TTL_SEC" '
        NF >= 2 && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl && !seen[$2]++ { print $1 "|" $2 }
    ' "$SEEN_LINE_HASH_FILE" | tail -n "$SEEN_LINE_MAX" > "$tmpf"
    cat "$tmpf" > "$SEEN_LINE_HASH_FILE"
    rm -f "$tmpf"
}

_line_hash_recently_seen() {
    local line_hash="$1"
    [ -n "$line_hash" ] || return 1
    [ -f "$SEEN_LINE_HASH_FILE" ] || return 1
    local now_ts
    now_ts=$(date +%s)
    awk -F'|' -v target="$line_hash" -v now_ts="$now_ts" -v ttl="$SEEN_LINE_TTL_SEC" '
        $2 == target && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl { found = 1; exit }
        END { exit(found ? 0 : 1) }
    ' "$SEEN_LINE_HASH_FILE"
}

_daemon_pids() {
    ps -Ao pid=,command= 2>/dev/null | awk '
        $0 ~ /[k]ick_chat_daemon\.mjs/ {print $1}
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

    if [ -f "$PID_FILE" ]; then
        local old_pid
        old_pid=$(cat "$PID_FILE")
        if _pid_alive "$old_pid"; then
            _log "既に起動中 (PID=$old_pid)"
            return 0
        fi
        rm -f "$PID_FILE"
    fi

    if ! command -v node >/dev/null 2>&1; then
        _log "node が見つからないため daemon を起動できない"
        return 1
    fi

    [ -f "$RAW_LOG" ] || touch "$RAW_LOG"
    [ -f "$OFFSET_FILE" ] || echo "0" > "$OFFSET_FILE"

    nohup node "$DAEMON_SCRIPT" "$CHANNEL" >> "$CHAT_DIR/daemon.out" 2>&1 &
    local dpid=$!
    echo "$dpid" > "$PID_FILE"
    _log "daemon起動 (PID=$dpid, slug=$CHANNEL)"
}

#--- fetch: 前回からの差分を取得してサニタイズ → pending.logに蓄積 ---
_fetch_nolock() {
    if [ -f "$RAW_LOG" ]; then
        local last_offset
        last_offset=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
        case "$last_offset" in ''|*[!0-9]*) last_offset=0 ;; esac
        local current_lines
        current_lines=$(wc -l < "$RAW_LOG" | tr -d ' ')

        # デーモンが raw.log を切り詰めた直後はオフセットが行数を追い越すので戻す。
        if [ "$last_offset" -gt "$current_lines" ]; then
            last_offset=0
            echo "0" > "$OFFSET_FILE"
        fi

        if [ "$current_lines" -gt "$last_offset" ]; then
            local new_comments
            new_comments=$(tail -n "+$((last_offset + 1))" "$RAW_LOG")

            local remaining
            remaining=$(tail -n "+$((current_lines + 1))" "$RAW_LOG" 2>/dev/null)
            if [ -n "$remaining" ]; then
                echo "$remaining" > "$RAW_LOG"
                echo "$(echo "$remaining" | wc -l | tr -d ' ')" > "$OFFSET_FILE"
            else
                > "$RAW_LOG"
                echo "0" > "$OFFSET_FILE"
            fi

            local scan_tmp seen_batch_tmp seen_line_batch_tmp dedup_tmp
            local skipped_by_id=0 skipped_by_sanitize=0 skipped_by_line=0 skipped_by_recent_line=0 added_count=0
            scan_tmp=$(mktemp "$CHAT_DIR/.new_scan.XXXXXXXX")
            seen_batch_tmp=$(mktemp "$CHAT_DIR/.seen_batch.XXXXXXXX")
            seen_line_batch_tmp=$(mktemp "$CHAT_DIR/.seen_line_batch.XXXXXXXX")
            dedup_tmp=$(mktemp "$CHAT_DIR/.new_dedup.XXXXXXXX")
            : > "$scan_tmp"
            : > "$seen_batch_tmp"
            : > "$seen_line_batch_tmp"
            [ -f "$SEEN_ID_FILE" ] || : > "$SEEN_ID_FILE"
            [ -f "$SEEN_LINE_HASH_FILE" ] || : > "$SEEN_LINE_HASH_FILE"
            _compact_seen_line_hashes

            while IFS= read -r raw_line; do
                [ -n "$raw_line" ] || continue

                local msg_id="" comment_line="$raw_line"
                # 行形式: id=<kick-msg-id>\t<user>: <message>
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

                local line_hash=""
                # md5sum(Linux) が無い環境では md5(BSD) にフォールバックする。
                # パイプの終端が awk になるため `||` では拾えず、空判定で分岐する。
                line_hash=$(printf '%s' "$clean_line" | md5sum 2>/dev/null | awk '{print $1}')
                [ -n "$line_hash" ] || line_hash=$(printf '%s' "$clean_line" | md5 -q 2>/dev/null)
                if [ -n "$line_hash" ]; then
                    if grep -qxF "$line_hash" "$seen_line_batch_tmp" 2>/dev/null || _line_hash_recently_seen "$line_hash"; then
                        skipped_by_recent_line=$((skipped_by_recent_line + 1))
                        continue
                    fi
                    echo "$line_hash" >> "$seen_line_batch_tmp"
                fi

                echo "$clean_line" >> "$scan_tmp"
            done <<<"$(printf '%s\n' "$new_comments")"

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

            if [ -s "$seen_batch_tmp" ]; then
                cat "$seen_batch_tmp" >> "$SEEN_ID_FILE"
                _compact_seen_ids
            fi
            if [ -s "$seen_line_batch_tmp" ]; then
                local seen_line_ts
                seen_line_ts=$(date +%s)
                while IFS= read -r line_hash; do
                    [ -n "$line_hash" ] || continue
                    printf '%s|%s\n' "$seen_line_ts" "$line_hash" >> "$SEEN_LINE_HASH_FILE"
                done < "$seen_line_batch_tmp"
                _compact_seen_line_hashes
            fi

            rm -f "$scan_tmp" "$seen_batch_tmp" "$seen_line_batch_tmp" "$dedup_tmp"

            if [ "${added_count:-0}" -gt 0 ] || [ "$skipped_by_id" -gt 0 ] || [ "$skipped_by_recent_line" -gt 0 ]; then
                _log "fetch: $((current_lines - last_offset))件中 ${added_count}件追加 (id重複:${skipped_by_id}, 内容重複:${skipped_by_line}, 履歴重複:${skipped_by_recent_line}, sanitize除外:${skipped_by_sanitize})"
            fi
        fi
    fi

    if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
        local before_count after_count pending_tmp
        before_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
        pending_tmp=$(mktemp "$CHAT_DIR/.pending_dedup.XXXXXXXX")
        awk 'NF && !seen[$0]++' "$PENDING_LOG" > "$pending_tmp"
        cat "$pending_tmp" > "$PENDING_LOG"
        rm -f "$pending_tmp"
        after_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
        if [ "${after_count:-0}" -lt "${before_count:-0}" ]; then
            _log "fetch: pending重複を$((before_count - after_count))件除去"
        fi

        head -10 "$PENDING_LOG" > "$OUTFILE"
        local pending_count
        pending_count=$(wc -l < "$OUTFILE" | tr -d ' ')
        _log "fetch: pending ${pending_count}件を出力"
    else
        rm -f "$OUTFILE"
        :
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
    rm -f "$OUTFILE"
}

_ack() {
    _with_chat_lock "ack" _ack_nolock
}

#--- ack-batch: 指定ファイルの行だけpending.logから削除 ---
_ack_batch_nolock() {
    local batch_file="$1"
    if [ -z "$batch_file" ] || [ ! -f "$batch_file" ]; then
        _log "ack_batch: バッチファイルが無い ($batch_file)"
        return 1
    fi
    if [ ! -f "$PENDING_LOG" ] || [ ! -s "$PENDING_LOG" ]; then
        _log "ack_batch: pending.logは空"
        return 0
    fi

    local batch_tmp out_tmp before_count after_count
    batch_tmp=$(mktemp "$CHAT_DIR/.ack_batch.XXXXXXXX")
    out_tmp=$(mktemp "$CHAT_DIR/.pending_after_ack.XXXXXXXX")
    awk 'NF' "$batch_file" > "$batch_tmp"
    before_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
    grep -vxF -f "$batch_tmp" "$PENDING_LOG" > "$out_tmp" || true
    cat "$out_tmp" > "$PENDING_LOG"
    after_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
    rm -f "$batch_tmp" "$out_tmp"
    _log "ack_batch: $((before_count - after_count))件を消化 (残り${after_count}件)"
    rm -f "$OUTFILE"
    return 0
}

_ack_batch() {
    _with_chat_lock "ack-batch" _ack_batch_nolock "$1"
}

#--- stop ---
_stop() {
    local stopped=false

    if [ -f "$PID_FILE" ]; then
        local dpid
        dpid=$(cat "$PID_FILE")
        if _pid_alive "$dpid"; then
            kill "$dpid" 2>/dev/null
            wait "$dpid" 2>/dev/null
            _log "daemon停止 (PID=$dpid)"
            stopped=true
        fi
        rm -f "$PID_FILE"
    fi

    local pid
    for pid in $(_daemon_pids); do
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
    if [ -f "$PID_FILE" ] && _pid_alive "$(cat "$PID_FILE")"; then
        local lines offset pending
        lines=$(wc -l < "$RAW_LOG" 2>/dev/null | tr -d ' ')
        offset=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
        pending=0
        [ -f "$PENDING_LOG" ] && pending=$(wc -l < "$PENDING_LOG" 2>/dev/null | tr -d ' ')
        echo "running (PID=$(cat "$PID_FILE"), total=${lines:-0}件, unread=$((${lines:-0} - offset))件, pending=${pending:-0}件)"
    else
        echo "stopped"
    fi
}

case "$CMD" in
    start)  _start ;;
    fetch)  _fetch ;;
    ack)    _ack ;;
    ack-batch) _ack_batch "$2" ;;
    stop)   _stop ;;
    status) _status ;;
    *)      echo "Usage: $0 {start|fetch|ack|ack-batch|stop|status} [slug|batch_file]" >&2; exit 1 ;;
esac
