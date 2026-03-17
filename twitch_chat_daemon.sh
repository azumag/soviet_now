#!/bin/bash
# twitch_chat_daemon.sh - Twitch IRC 常駐プロセス（twitch_chat.sh から起動される）
# 4分ごとに再接続し、PRIVMSGをraw.logに追記する

CHAT_DIR="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
RAW_LOG="$CHAT_DIR/raw.log"
CHANNEL="${1:-azumagbanjo}"
RECENT_MSG_IDS_FILE="$CHAT_DIR/recent_msg_ids.log"
RECENT_LINE_HASHES_FILE="$CHAT_DIR/recent_line_hashes.log"
RECENT_MSG_ID_TTL_SEC="${TWITCH_RECENT_DEDUP_TTL_SEC:-900}"
RECENT_LINE_HASH_TTL_SEC="${TWITCH_RECENT_LINE_HASH_TTL_SEC:-60}"
RECENT_DEDUP_MAX="${TWITCH_RECENT_DEDUP_MAX:-4000}"
CLIP_COOLDOWN_FILE="$CHAT_DIR/clip_cooldown"
CLIP_COOLDOWN_SEC=30

cd "$(dirname "$0")"
mkdir -p "$CHAT_DIR"

_compact_recent_file() {
    local src="$1" ttl="$2" max_keep="${3:-$RECENT_DEDUP_MAX}"
    [ -n "$ttl" ] || ttl="$RECENT_MSG_ID_TTL_SEC"
    [ -f "$src" ] || return 0
    local tmpf now_ts
    now_ts=$(date +%s)
    tmpf=$(mktemp /tmp/twitch_daemon_recent_XXXXXXXX 2>/dev/null) || return 0
    awk -F'|' -v now_ts="$now_ts" -v ttl="$ttl" '
        NF >= 2 && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl && !seen[$2]++ { print $1 "|" $2 }
    ' "$src" | tail -n "$max_keep" > "$tmpf"
    mv "$tmpf" "$src"
}

_recent_key_seen() {
    local src="$1" key="$2" ttl="$3"
    [ -n "$ttl" ] || ttl="$RECENT_MSG_ID_TTL_SEC"
    [ -n "$key" ] || return 1
    [ -f "$src" ] || return 1
    local now_ts
    now_ts=$(date +%s)
    awk -F'|' -v target="$key" -v now_ts="$now_ts" -v ttl="$ttl" '
        $2 == target && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl { found = 1; exit }
        END { exit(found ? 0 : 1) }
    ' "$src"
}

_mark_recent_key() {
    local src="$1" key="$2"
    [ -n "$key" ] || return 0
    mkdir -p "$CHAT_DIR" 2>/dev/null || true
    printf '%s|%s\n' "$(date +%s)" "$key" >> "$src"
}

while true; do
    nick="justinfan$((RANDOM % 90000 + 10000))"
    {
        # display-name などのメタ情報タグを受け取る
        echo "CAP REQ :twitch.tv/tags twitch.tv/commands"
        echo "NICK $nick"
        echo "JOIN #${CHANNEL}"
        sleep 240
    } | nc -w 250 irc.chat.twitch.tv 6667 2>/dev/null | while IFS= read -r line; do
        # IRCv3 タグ付き行: @tag1=v1;tag2=v2 :user!user@... PRIVMSG #ch :message
        tags=""
        payload="$line"
        if [[ "$payload" == @* ]]; then
            tags="${payload%% *}"
            tags="${tags#@}"
            payload="${payload#* }"
        fi

        if [[ "$payload" == *"PRIVMSG"* ]]; then
            login_user=$(echo "$payload" | sed -n 's/^:\([^!]*\)!.*/\1/p')
            display_name=""
            msg_id=""
            if [ -n "$tags" ]; then
                display_name=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^display-name=//p' | head -n1)
                msg_id=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^id=//p' | head -n1)
                # IRCv3の最低限デコード（\s=space, \:=;, \\=\）
                display_name=$(printf '%s' "$display_name" | sed -e 's/\\s/ /g' -e 's/\\:/;/g' -e 's/\\\\/\\/g')
            fi
            user="$display_name"
            [ -z "$user" ] && user="$login_user"

            # 開発用アカウントは読み上げ対象から除外
            if [ "$login_user" = "azumagdev" ] || [ "$user" = "azumagdev" ]; then
                continue
            fi

            msg=$(echo "$payload" | sed 's/^.*PRIVMSG [^ ]* ://')
            # サニタイズ: 制御文字 + シェルメタ文字除去
            msg=$(echo "$msg" | tr -d '\000-\010\013-\037\r' | tr -d '`$\\{}|;<>&')
            user=$(echo "$user" | tr -d '`$\\{}|;<>&')
            clean_line="${user}: ${msg}"

            # !clip コマンド検出（クールダウン付き、TWITCH_CLIP_CMD_ENABLED=1 で有効）
            if [ "${TWITCH_CLIP_CMD_ENABLED:-0}" = "1" ] && [[ "$msg" =~ ^[[:space:]]*!clip([[:space:]]|$) ]]; then
                local now_ts last_clip_ts clip_age
                now_ts=$(date +%s)
                last_clip_ts=$(cat "$CLIP_COOLDOWN_FILE" 2>/dev/null || echo 0)
                clip_age=$((now_ts - last_clip_ts))
                if [ "$clip_age" -ge "$CLIP_COOLDOWN_SEC" ]; then
                    echo "$now_ts" > "$CLIP_COOLDOWN_FILE"
                    ( ./twitch_clip.sh "📎 Clip by ${user}" 2>>"tmp/debug/twitch_clip.log" || true ) &
                fi
            fi

            # !syukusei / 粛清 [ID] — vo_random から特定スタイルIDを除外 + 再生中の読み上げをkill
            if [[ "$msg" == *粛清*[0-9]* ]] || [[ "$msg" == *syukusei*[0-9]* ]]; then
                _syukusei_id=$(echo "$msg" | grep -oE '[0-9]+' | head -1)
                _syukusei_file="tmp/voicevox_exclude_ids.txt"
                echo "[syukusei $(date '+%H:%M:%S')] id=$_syukusei_id msg=[$msg]" >> tmp/debug/syukusei.log 2>&1
                if [ -n "$_syukusei_id" ]; then
                    if ! grep -qx "$_syukusei_id" "$_syukusei_file" 2>/dev/null; then
                        echo "$_syukusei_id" >> "$_syukusei_file"
                    fi
                    # 再生中の読み上げをkill (afplayのみ、say_enqueueはkill_flagでリトライ抑止)
                    echo "1" > tmp/.say_queue/kill_flag
                    pgrep -x 'afplay' 2>/dev/null | xargs kill -9 2>/dev/null || true
                    # チャットに粛清通知
                    ( [ -f .env ] && set -a && . ./.env && set +a; ./twitch_chat.sh send "粛清されました [${_syukusei_id}]" >/dev/null 2>&1 || true ) &
                fi
                continue
            fi

            # !pitch ID VALUE — スピーカーIDごとにピッチ設定 (例: !pitch 86 0.1, !pitch 3 -0.05)
            if [[ "$msg" =~ ^[[:space:]]*!pitch[[:space:]]+([0-9]+)[[:space:]]+([-]?[0-9]*\.?[0-9]+) ]]; then
                _pitch_id="${BASH_REMATCH[1]}"
                _pitch_val="${BASH_REMATCH[2]}"
                _pitch_file="tmp/voicevox_pitch_map.txt"
                # 既存エントリを除去して新しい値を追加
                if [ -f "$_pitch_file" ]; then
                    grep -v "^${_pitch_id}|" "$_pitch_file" > "${_pitch_file}.tmp" 2>/dev/null || true
                    mv "${_pitch_file}.tmp" "$_pitch_file"
                fi
                echo "${_pitch_id}|${_pitch_val}" >> "$_pitch_file"
                continue
            fi

            # !wakana / !moko / !random / !vo / !vo_random / !say — コメント読み上げの声切替
            if [[ "$msg" =~ ^[[:space:]]*!(wakana|moko|random|vo_random|vo|say)([[:space:]]|$) ]]; then
                coe_cmd="${BASH_REMATCH[1]}"
                case "$coe_cmd" in
                    wakana)    echo "8e99d620-87d3-11ed-870a-0242ac1c000c|905192261" > tmp/coeiroink_voice.txt; rm -f tmp/voicevox_voice.txt ;;
                    moko)      echo "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131762" > tmp/coeiroink_voice.txt; rm -f tmp/voicevox_voice.txt ;;
                    random)    echo "random" > tmp/coeiroink_voice.txt; rm -f tmp/voicevox_voice.txt ;;
                    vo)        echo "109" > tmp/voicevox_voice.txt; rm -f tmp/coeiroink_voice.txt ;;
                    vo_random) echo "random" > tmp/voicevox_voice.txt; rm -f tmp/coeiroink_voice.txt ;;
                    say)       rm -f tmp/coeiroink_voice.txt tmp/voicevox_voice.txt ;;
                esac
                continue
            fi

            _compact_recent_file "$RECENT_MSG_IDS_FILE" "$RECENT_MSG_ID_TTL_SEC"
            _compact_recent_file "$RECENT_LINE_HASHES_FILE" "$RECENT_LINE_HASH_TTL_SEC"

            if [ -n "$msg_id" ] && _recent_key_seen "$RECENT_MSG_IDS_FILE" "$msg_id" "$RECENT_MSG_ID_TTL_SEC"; then
                continue
            fi

            line_hash=$(printf '%s' "$clean_line" | md5 -q 2>/dev/null || printf '%s' "$clean_line" | md5sum | awk '{print $1}')
            if [ -n "$line_hash" ] && _recent_key_seen "$RECENT_LINE_HASHES_FILE" "$line_hash" "$RECENT_LINE_HASH_TTL_SEC"; then
                continue
            fi

            # msg-id を先頭に保持しておくと、再接続時の同一コメント重複を抑止しやすい
            if [ -n "$msg_id" ]; then
                echo "id=${msg_id}"$'\t'"${clean_line}" >> "$RAW_LOG"
                _mark_recent_key "$RECENT_MSG_IDS_FILE" "$msg_id"
            else
                echo "${clean_line}" >> "$RAW_LOG"
            fi
            if [ -n "$line_hash" ]; then
                _mark_recent_key "$RECENT_LINE_HASHES_FILE" "$line_hash"
            fi
        fi
    done
    sleep 5
done
