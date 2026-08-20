#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では Twitch チャット連携を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# twitch_chat.sh - Twitch チャット常駐デーモン + 差分フェッチ
#
# 使い方:
#   ./twitch_chat.sh start [channel]   - バックグラウンドでIRC常駐開始
#   ./twitch_chat.sh fetch             - 前回fetch以降の新コメントを取得 → tmp/twitch_comments.txt
#   ./twitch_chat.sh ack               - 読み上げ完了後に呼ぶ。pending.logをクリア
#   ./twitch_chat.sh ack-batch <file>  - 処理済みコメント行のみ pending.log から削除
#   ./twitch_chat.sh send <message>    - 認証済みIRC接続で単発チャット投稿
#   ./twitch_chat.sh stop              - デーモン停止
#   ./twitch_chat.sh status            - 動作状況表示

cd "$(dirname "$0")"

# Long-running workers may already export these, but direct send/announce/fetch
# commands should also see the current Twitch credentials.
[ -f .env ] && set -a && . ./.env && set +a

CHAT_DIR="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
mkdir -p "$CHAT_DIR"

RAW_LOG="$CHAT_DIR/raw.log"        # デーモンが追記するログ
PID_FILE="$CHAT_DIR/daemon.pid"
OFFSET_FILE="$CHAT_DIR/last_offset" # 前回fetchした行数
PENDING_LOG="$CHAT_DIR/pending.log"  # 未読み上げキュー
OUTFILE="${TWITCH_CHAT_OUTFILE:-tmp/twitch_comments.txt}"
SEEN_ID_FILE="$CHAT_DIR/seen_msg_ids.log" # 直近に処理済みのTwitch msg-id
SEEN_ID_MAX=4000
SEEN_LINE_HASH_FILE="$CHAT_DIR/seen_line_hashes.log" # 直近に処理済みのコメント行ハッシュ
SEEN_LINE_MAX=4000
SEEN_LINE_TTL_SEC="${TWITCH_FETCH_LINE_HASH_TTL_SEC:-900}"
TAB=$'\t'
LOCK_DIR="$CHAT_DIR/.op_lock"
LOCK_TIMEOUT_SEC=8
LOCK_STALE_SEC=120

CMD="${1:-fetch}"
CHANNEL="${2:-azumagbanjo}"

_log() { echo "[twitch_chat $(date '+%H:%M:%S')] $*" >&2; }

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
        # 孤立ロック回収: PID死亡 or ロックが古すぎる
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

_json_string() {
    python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

_resolve_twitch_sender_id() {
    local token="$1"
    local client_id="$2"
    if [ -n "${TWITCH_BOT_USER_ID:-}" ]; then
        printf '%s' "$TWITCH_BOT_USER_ID"
        return 0
    fi
    local resp sender_id err_msg
    resp=$(curl -s -H "Authorization: Bearer ${token}" -H "Client-Id: ${client_id}" \
        https://api.twitch.tv/helix/users 2>/dev/null) || return 1
    sender_id=$(printf '%s' "$resp" | python3 -c "import json,sys; print((json.load(sys.stdin).get('data') or [{}])[0].get('id',''))" 2>/dev/null || true)
    if [ -n "$sender_id" ]; then
        printf '%s' "$sender_id"
        return 0
    fi
    err_msg=$(printf '%s' "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('message') or d.get('error') or '')" 2>/dev/null || true)
    [ -n "$err_msg" ] && echo "WARNING: Twitch users lookup failed: ${err_msg}" >&2
    return 1
}

_send_api() {
    local msg="$1"
    local token="${TWITCH_BOT_TOKEN:-}"
    local client_id="${TWITCH_CLIENT_ID:-}"
    local broadcaster_id="${TWITCH_BROADCASTER_ID:-}"
    [ -n "$token" ] && [ -n "$client_id" ] && [ -n "$broadcaster_id" ] || return 2
    token="${token#oauth:}"

    local sender_id
    sender_id=$(_resolve_twitch_sender_id "$token" "$client_id")
    if [ -z "$sender_id" ]; then
        echo "WARNING: could not resolve Twitch sender_id from token" >&2
        return 1
    fi

    local payload resp http_code body sent_status drop_reason
    payload=$(printf '{"broadcaster_id":%s,"sender_id":%s,"message":%s}' \
        "$(_json_string "$broadcaster_id")" \
        "$(_json_string "$sender_id")" \
        "$(_json_string "$msg")")
    resp=$(curl -s -w "\n%{http_code}" -X POST \
        "https://api.twitch.tv/helix/chat/messages" \
        -H "Authorization: Bearer ${token}" \
        -H "Client-Id: ${client_id}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        2>/dev/null)
    http_code=$(printf '%s' "$resp" | tail -1)
    body=$(printf '%s' "$resp" | sed '$d')

    if [ "$http_code" != "200" ]; then
        echo "WARNING: Twitch chat/messages failed (HTTP=$http_code): $(printf '%s' "$body" | head -1)" >&2
        return 1
    fi

    sent_status=$(python3 -c 'import json,sys; data=json.load(sys.stdin).get("data") or []; print("1" if data and data[0].get("is_sent") else "0")' <<<"$body" 2>/dev/null || echo "0")
    if [ "$sent_status" = "1" ]; then
        _log "send: chat/messages accepted"
        return 0
    fi
    drop_reason=$(python3 -c 'import json,sys; data=json.load(sys.stdin).get("data") or [{}]; print(data[0].get("drop_reason",{}).get("message") or data[0].get("drop_reason",{}).get("code") or "not sent")' <<<"$body" 2>/dev/null || echo "not sent")
    echo "WARNING: Twitch chat/messages returned is_sent=false: ${drop_reason}" >&2
    return 1
}

_is_card_gacha_result_line() {
    local line="$1"
    local message="$line"
    [ -n "$message" ] || return 1
    case "$message" in
        *": "*) message="${message#*: }" ;;
    esac
    printf '%s\n' "$message" | grep -Eq '[^[:space:]]+[[:space:]]*が[[:space:]]*.+[[:space:]]*を獲得しました'
}

_is_ignored_comment_author_line() {
    local line="$1"
    local ignored="${TWITCH_IGNORE_AUTHORS:-azumagdev azumagbanjo あずまぐ}"
    local item
    for item in $ignored; do
        printf '%s\n' "$line" | grep -Fqi -- "${item}: " && return 0
    done
    return 1
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

    # Bot / broadcaster self-posts are outbound echoes, not viewer comments.
    if _is_ignored_comment_author_line "$line"; then
        if ! _is_card_gacha_result_line "$line"; then
            return 1
        fi
        line="${line#*: }"
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
        if _pid_alive "$old_pid"; then
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

                local line_hash=""
                line_hash=$(printf '%s' "$clean_line" | md5 -q 2>/dev/null || printf '%s' "$clean_line" | md5sum | awk '{print $1}')
                if [ -n "$line_hash" ]; then
                    if grep -qxF "$line_hash" "$seen_line_batch_tmp" 2>/dev/null || _line_hash_recently_seen "$line_hash"; then
                        skipped_by_recent_line=$((skipped_by_recent_line + 1))
                        continue
                    fi
                    echo "$line_hash" >> "$seen_line_batch_tmp"
                fi

                echo "$clean_line" >> "$scan_tmp"
            done <<<"$(printf '%s\n' "$new_comments")"

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

    # pending.log全体（前回未ack分 + 今回新規分）をOUTFILEに出力
    if [ -f "$PENDING_LOG" ] && [ -s "$PENDING_LOG" ]; then
        # pending全体の同一行も正規化
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

        # pending は FIFO で先頭から処理する
        head -10 "$PENDING_LOG" > "$OUTFILE"
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

#--- ack-batch: 指定ファイルの行だけpending.logから削除 ---
_ack_batch_nolock() {
    local batch_file="$1"
    if [ -z "$batch_file" ] || [ ! -f "$batch_file" ]; then
        _log "ack_batch: batch fileが見つかりません"
        return 0
    fi
    if [ ! -f "$PENDING_LOG" ] || [ ! -s "$PENDING_LOG" ]; then
        _log "ack_batch: pending.logは空"
        return 0
    fi

    local batch_tmp out_tmp before_count after_count removed_count
    batch_tmp=$(mktemp "$CHAT_DIR/.ack_batch.XXXXXXXX")
    out_tmp=$(mktemp "$CHAT_DIR/.ack_out.XXXXXXXX")
    awk 'NF && !seen[$0]++' "$batch_file" > "$batch_tmp"
    if [ ! -s "$batch_tmp" ]; then
        rm -f "$batch_tmp" "$out_tmp"
        _log "ack_batch: 対象行なし"
        return 0
    fi

    before_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
    grep -vxF -f "$batch_tmp" "$PENDING_LOG" > "$out_tmp" || true
    cat "$out_tmp" > "$PENDING_LOG"
    after_count=$(wc -l < "$PENDING_LOG" | tr -d ' ')
    removed_count=$((before_count - after_count))
    _log "ack_batch: ${removed_count}件をpendingから削除"
    rm -f "$batch_tmp" "$out_tmp"
}

_ack_batch() {
    local batch_file="$1"
    _with_chat_lock "ack_batch" _ack_batch_nolock "$batch_file"
}

#--- claim: fetch + pending snapshot + ack をロック下で一括処理 ---
_claim_nolock() {
    _fetch_nolock
    _ack_nolock
}

_claim() {
    _with_chat_lock "claim" _claim_nolock
}

_send() {
    local msg="$1"
    [ -n "$msg" ] || {
        echo "Usage: $0 send <message>" >&2
        return 1
    }

    local channel token nick tmp_irc rc
    channel="${TWITCH_CHANNEL:-azumagbanjo}"
    token="${TWITCH_BOT_TOKEN:-}"
    nick="${TWITCH_BOT_NICK:-${TWITCH_BOT_LOGIN:-azumagdev}}"
    channel="${channel#\#}"
    nick=$(printf '%s' "$nick" | tr '\r\n' ' ' | tr -d '\000')
    if [ -z "$token" ]; then
        echo "TWITCH_BOT_TOKEN not set" >&2
        return 1
    fi

    token="${token#oauth:}"
    msg=$(printf '%s' "$msg" | tr '\r\n' ' ' | tr -d '\000')
    [ -n "$msg" ] || return 1
    msg=$(python3 - <<'PY' "$msg"
import sys
msg = sys.argv[1]
limit = 430
data = msg.encode("utf-8")
if len(data) <= limit:
    print(msg, end="")
    raise SystemExit
trim = data[:limit - 3]
while True:
    try:
        out = trim.decode("utf-8")
        break
    except UnicodeDecodeError:
        trim = trim[:-1]
print(out.rstrip() + "...", end="")
PY
)

    if _send_api "$msg"; then
        return 0
    fi
    local api_rc=$?
    if [ "$api_rc" -ne 2 ]; then
        echo "WARNING: falling back to IRC after Twitch API send failure" >&2
    fi

    tmp_irc=$(mktemp /tmp/twitch_send_XXXXXXXX)
    {
        printf 'PASS oauth:%s\r\n' "$token"
        printf 'NICK %s\r\n' "$nick"
        printf 'JOIN #%s\r\n' "$channel"
        sleep 1.5
        printf 'PRIVMSG #%s :%s\r\n' "$channel" "$msg"
        sleep 0.5
        printf 'QUIT\r\n'
    } | nc -w 5 irc.chat.twitch.tv 6667 >"$tmp_irc" 2>&1
    rc=$?

    if [ "$rc" -ne 0 ] || ! grep -Eiq " 001 |Welcome, GLHF|JOIN #${channel}" "$tmp_irc" 2>/dev/null || grep -Eiq "Login authentication failed|NOTICE.*Error|Improperly formatted auth|Error logging in" "$tmp_irc" 2>/dev/null; then
        echo "WARNING: Twitch send may have failed (rc=$rc)" >&2
        if [ -s "$tmp_irc" ]; then
            head -5 "$tmp_irc" >&2
        fi
        rm -f "$tmp_irc"
        return 1
    fi
    rm -f "$tmp_irc"
    return 0
}

#--- announce (ピン留めアナウンス) ---
_announce() {
    local msg="$1" color="${2:-primary}"
    [ -n "$msg" ] || {
        echo "Usage: $0 announce <message> [color]" >&2
        return 1
    }
    local token="${TWITCH_BOT_TOKEN:-}"
    local client_id="${TWITCH_CLIENT_ID:-}"
    local broadcaster_id="${TWITCH_BROADCASTER_ID:-}"
    if [ -z "$token" ] || [ -z "$client_id" ] || [ -z "$broadcaster_id" ]; then
        echo "WARNING: TWITCH_BOT_TOKEN, TWITCH_CLIENT_ID, or TWITCH_BROADCASTER_ID not set" >&2
        return 1
    fi
    token="${token#oauth:}"
    # moderator_id はトークン所有者(bot)のIDを使う
    local moderator_id
    moderator_id=$(curl -s -H "Authorization: Bearer ${token}" -H "Client-Id: ${client_id}" \
        https://api.twitch.tv/helix/users 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
    if [ -z "$moderator_id" ]; then
        echo "WARNING: could not resolve moderator_id from token" >&2
        return 1
    fi
    local resp http_code
    resp=$(curl -s -w "\n%{http_code}" -X POST \
        "https://api.twitch.tv/helix/chat/announcements?broadcaster_id=${broadcaster_id}&moderator_id=${moderator_id}" \
        -H "Authorization: Bearer ${token}" \
        -H "Client-Id: ${client_id}" \
        -H "Content-Type: application/json" \
        -d "{\"message\":$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"color\":\"${color}\"}" \
        2>/dev/null)
    http_code=$(printf '%s' "$resp" | tail -1)
    if [ "$http_code" = "204" ] || [ "$http_code" = "200" ]; then
        _log "announce送信成功"
        return 0
    else
        _log "announce送信失敗 (HTTP=$http_code): $(printf '%s' "$resp" | head -1)"
        return 1
    fi
}

#--- stop ---
_stop() {
    local stopped=false

    if [ -f "$PID_FILE" ]; then
        local dpid
        dpid=$(cat "$PID_FILE")
        if _pid_alive "$dpid"; then
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
    if [ -f "$PID_FILE" ] && _pid_alive "$(cat "$PID_FILE")"; then
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
    ack-batch) _ack_batch "$2" ;;
    claim)  _claim ;;
    send)   _send "$2" ;;
    announce) _announce "$2" "${3:-primary}" ;;
    stop)   _stop ;;
    status) _status ;;
    *)      echo "Usage: $0 {start|fetch|ack|ack-batch|claim|send|announce|stop|status} [channel|batch_file|message]" >&2; exit 1 ;;
esac
