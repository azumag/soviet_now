#!/bin/bash
# twitch_chat_daemon.sh - Twitch IRC 常駐プロセス（twitch_chat.sh から起動される）
# 常時接続し、PRIVMSGをraw.logに追記する

CHAT_DIR="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
RAW_LOG="$CHAT_DIR/raw.log"
CHANNEL="${1:-azumagbanjo}"
# read がこの秒数、IRC から 1 行も受信できなかったら接続がハング(CLOSE-WAIT等)したと
# みなして再接続する。Twitch は約5分ごとに PING を送るため、通常はそれより長い値にし、
# 正常時には誤再接続しない。0 なら無効。
IRC_READ_TIMEOUT_SEC="${TWITCH_IRC_READ_TIMEOUT_SEC:-600}"
case "$IRC_READ_TIMEOUT_SEC" in
    ''|*[!0-9]*) IRC_READ_TIMEOUT_SEC=600 ;;
esac
RECENT_MSG_IDS_FILE="$CHAT_DIR/recent_msg_ids.log"
RECENT_LINE_HASHES_FILE="$CHAT_DIR/recent_line_hashes.log"
RECENT_MSG_ID_TTL_SEC="${TWITCH_RECENT_DEDUP_TTL_SEC:-900}"
RECENT_LINE_HASH_TTL_SEC="${TWITCH_RECENT_LINE_HASH_TTL_SEC:-900}"
RECENT_DEDUP_MAX="${TWITCH_RECENT_DEDUP_MAX:-4000}"
CLIP_COOLDOWN_FILE="$CHAT_DIR/clip_cooldown"
CLIP_COOLDOWN_SEC=30
WAVE_LINK_REPAIR_COOLDOWN_FILE="$CHAT_DIR/wave_link_repair_cooldown"
WAVE_LINK_REPAIR_COOLDOWN_SEC="${WAVE_LINK_REPAIR_COOLDOWN_SEC:-3600}"
WAVE_LINK_REPAIR_SCRIPT="${WAVE_LINK_REPAIR_SCRIPT:-./repair_wave_link.sh}"
case "$WAVE_LINK_REPAIR_COOLDOWN_SEC" in
    ''|*[!0-9]*) WAVE_LINK_REPAIR_COOLDOWN_SEC=3600 ;;
esac
# 「配信を開始できますか？」系コメントでOBS配信を開始する。既に配信中なら何もしない
# (obs_control.sh stream-start 側で no-op になるため悪用リスクなし)。
STREAM_START_ON_COMMENT_ENABLED="${STREAM_START_ON_COMMENT_ENABLED:-1}"
STREAM_START_COOLDOWN_FILE="$CHAT_DIR/stream_start_cooldown"
STREAM_START_COOLDOWN_SEC="${STREAM_START_COOLDOWN_SEC:-60}"
case "$STREAM_START_COOLDOWN_SEC" in
    ''|*[!0-9]*) STREAM_START_COOLDOWN_SEC=60 ;;
esac

cd "$(dirname "$0")"
mkdir -p "$CHAT_DIR"

_compact_recent_file() {
    local src="$1" ttl="$2" max_keep="${3:-$RECENT_DEDUP_MAX}"
    [ -n "$ttl" ] || ttl="$RECENT_MSG_ID_TTL_SEC"
    [ -f "$src" ] || return 0
    local tmpf now_ts
    now_ts=$(date +%s)
    tmpf=$(mktemp "$CHAT_DIR/.recent_compact.XXXXXXXX" 2>/dev/null) || return 0
    awk -F'|' -v now_ts="$now_ts" -v ttl="$ttl" '
        NF >= 2 && $1 ~ /^[0-9]+$/ && (now_ts - $1) <= ttl && !seen[$2]++ { print $1 "|" $2 }
    ' "$src" | tail -n "$max_keep" > "$tmpf"
    cat "$tmpf" > "$src"
    rm -f "$tmpf"
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

_is_card_gacha_result_message() {
    local text="$1"
    [ -n "$text" ] || return 1
    printf '%s\n' "$text" | grep -Eq '[^[:space:]]+[[:space:]]*が[[:space:]]*.+[[:space:]]*を獲得しました'
}

# 「配信を開始できますか？」「配信がされていない…配信を開始して」等を検出。
# locale非依存にするため正規表現ではなく部分一致(case)で判定する。
# 「配信」+ 開始系の動詞の両方を含む時のみ真。「配信おもしろい」等では発火しない。
_is_stream_start_request() {
    local text="$1"
    [ -n "$text" ] || return 1
    case "$text" in
        *配信*) ;;
        *) return 1 ;;
    esac
    case "$text" in
        *開始*|*始め*|*つけて*|*点けて*|*立ち上げ*|*オンにして*) return 0 ;;
    esac
    return 1
}

_is_ignored_author() {
    local login="${1:-}" display="${2:-}"
    local ignored="${TWITCH_IGNORE_AUTHORS:-azumagdev azumagbanjo あずまぐ}"
    local item
    for item in $ignored; do
        [ "$login" = "$item" ] && return 0
        [ "$display" = "$item" ] && return 0
    done
    return 1
}

_extract_usernotice_message() {
    local payload="${1:-}"
    printf '%s\n' "$payload" \
        | sed -n 's/^.*USERNOTICE [^ ]* ://p' \
        | tr -d '\000-\010\013-\037\r' \
        | tr -d '`$\\{}|;<>&'
}

_notify_chat_overlay() {
    local line="$1"
    [ "${CHAT_INGEST_OVERLAY_NOTIFY:-1}" = "1" ] || return 0
    [ -n "$line" ] || return 0
    [ -x ./overlay_notify.sh ] || return 0
    ./overlay_notify.sh chat "Twitch コメント受信" "$line" "info" >/dev/null 2>&1 || true
}

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

while true; do
    nick="justinfan$((RANDOM % 90000 + 10000))"
    coproc TWITCH_IRC { nc irc.chat.twitch.tv 6667 2>/dev/null; }
    {
        # display-name などのメタ情報タグを受け取る
        printf 'PASS SCHMOOPIIE\r\n'
        printf 'CAP REQ :twitch.tv/tags twitch.tv/commands\r\n'
        printf 'NICK %s\r\n' "$nick"
        printf 'JOIN #%s\r\n' "$CHANNEL"
    } >&"${TWITCH_IRC[1]}" || {
        exec {TWITCH_IRC[0]}>&- 2>/dev/null || true
        exec {TWITCH_IRC[1]}>&- 2>/dev/null || true
        wait "$TWITCH_IRC_PID" 2>/dev/null || true
        sleep 5
        continue
    }
    # セッション開始時に毎回リセットし、前回セッションの値が誤って
    # ストール判定に効かないようにする。
    _irc_read_rc=0
    while :; do
        if [ "${IRC_READ_TIMEOUT_SEC:-0}" -gt 0 ]; then
            if IFS= read -r -t "$IRC_READ_TIMEOUT_SEC" line <&"${TWITCH_IRC[0]}"; then
                :
            else
                _irc_read_rc=$?
                break
            fi
        else
            if IFS= read -r line <&"${TWITCH_IRC[0]}"; then
                :
            else
                _irc_read_rc=$?
                break
            fi
        fi
        # IRCv3 タグ付き行: @tag1=v1;tag2=v2 :user!user@... PRIVMSG #ch :message
        tags=""
        payload="$line"
        if [[ "$payload" == @* ]]; then
            tags="${payload%% *}"
            tags="${tags#@}"
            payload="${payload#* }"
        fi
        if [[ "$payload" == PING* ]]; then
            printf 'PONG %s\r\n' "${payload#PING }" >&"${TWITCH_IRC[1]}" 2>/dev/null || break
            continue
        fi

        # サブスク/ギフトサブ検出 (USERNOTICE)
        if [[ "$payload" == *"USERNOTICE"* ]] && [ -n "$tags" ]; then
            sub_msg_id=""
            sub_msg_id=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^msg-id=//p' | head -n1)
            case "$sub_msg_id" in
            sub|resub|subgift|submysterygift|anonsubgift)
                sub_display=""
                sub_display=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^display-name=//p' | head -n1)
                sub_display=$(printf '%s' "$sub_display" | sed -e 's/\\s/ /g' -e 's/\\:/;/g' -e 's/\\\\/\\/g')
                [ -z "$sub_display" ] && sub_display=$(echo "$payload" | sed -n 's/^:\([^!]*\)!.*/\1/p')
                sub_display=$(echo "$sub_display" | tr -d '`$\\{}|;<>&')
                sub_user_msg=""
                sub_user_msg=$(_extract_usernotice_message "$payload")
                sub_label="サブスクありがとう"
                case "$sub_msg_id" in
                    subgift|anonsubgift) sub_label="サブスクギフトありがとう" ;;
                    resub) sub_label="サブスク継続ありがとう" ;;
                esac
                sub_line="[SUB] ${sub_display}: ${sub_label}"
                [ -n "$sub_user_msg" ] && sub_line="[SUB] ${sub_display}: ${sub_label} / ${sub_user_msg}"
                echo "${sub_line}" >> "$RAW_LOG"
                ;;
            viewermilestone)
                # 連続視聴記録(watch-streak)などの視聴者マイルストーン。
                # 通常のPRIVMSGには出ないので、ここで拾ってコメントとしてraw.logに流す。
                ms_category=""
                ms_category=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^msg-param-category=//p' | head -n1)
                if [ -z "$ms_category" ] || [ "$ms_category" = "watch-streak" ]; then
                    ms_display=""
                    ms_display=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^display-name=//p' | head -n1)
                    ms_display=$(printf '%s' "$ms_display" | sed -e 's/\\s/ /g' -e 's/\\:/;/g' -e 's/\\\\/\\/g')
                    [ -z "$ms_display" ] && ms_display=$(echo "$payload" | sed -n 's/^:\([^!]*\)!.*/\1/p')
                    ms_display=$(echo "$ms_display" | tr -d '`$\\{}|;<>&')
                    ms_value=""
                    ms_value=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^msg-param-value=//p' | head -n1)
                    case "$ms_value" in
                        ''|*[!0-9]*) ms_value="" ;;
                    esac
                    if [ -n "$ms_display" ]; then
                        if [ -n "$ms_value" ]; then
                            ms_line="[視聴記録] ${ms_display}: ${ms_value}連続視聴を達成しました"
                        else
                            ms_line="[視聴記録] ${ms_display}: 連続視聴記録を達成しました"
                        fi
                        echo "${ms_line}" >> "$RAW_LOG"
                    fi
                fi
                ;;
            esac
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

            msg=$(echo "$payload" | sed 's/^.*PRIVMSG [^ ]* ://')
            # サニタイズ: 制御文字 + シェルメタ文字除去
            msg=$(echo "$msg" | tr -d '\000-\010\013-\037\r' | tr -d '`$\\{}|;<>&')

            # Bot / broadcaster self-posts are outbound echoes, not viewer comments.
            if _is_ignored_author "$login_user" "$user"; then
                if ! _is_card_gacha_result_message "$msg"; then
                    continue
                fi
            fi

            user=$(echo "$user" | tr -d '`$\\{}|;<>&')

            # ビッツ(Cheer)検出: tagsに bits= があればタグ付与
            bits_tag=""
            if [ -n "$tags" ]; then
                bits_amount=""
                bits_amount=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^bits=//p' | head -n1)
                if [ -n "$bits_amount" ]; then
                    bits_tag="[BITS] "
                fi
            fi

            # 管理者権限チェック: !pitch/!tempo 等の管理コマンド用
            _is_mod_or_broadcaster=false
            if [ -n "$tags" ]; then
                _badges=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n 's/^badges=//p' | head -n1)
                case ",${_badges}," in
                *,broadcaster/*|*,moderator/*) _is_mod_or_broadcaster=true ;;
                esac
            fi
            clean_line="${bits_tag}${user}: ${msg}"
            if _is_ignored_author "$login_user" "$user" && _is_card_gacha_result_message "$msg"; then
                clean_line="${bits_tag}${msg}"
            fi

            # !clip コマンド検出（クールダウン付き、TWITCH_CLIP_CMD_ENABLED=1 で有効）
            if [ "${TWITCH_CLIP_CMD_ENABLED:-0}" = "1" ] && [[ "$msg" =~ ^[[:space:]]*!clip([[:space:]]|$) ]]; then
                now_ts=0; last_clip_ts=0; clip_age=0
                now_ts=$(date +%s)
                last_clip_ts=$(cat "$CLIP_COOLDOWN_FILE" 2>/dev/null || echo 0)
                clip_age=$((now_ts - last_clip_ts))
                if [ "$clip_age" -ge "$CLIP_COOLDOWN_SEC" ]; then
                    echo "$now_ts" > "$CLIP_COOLDOWN_FILE"
                    ( ./twitch_clip.sh "📎 Clip by ${user}" 2>>"tmp/debug/twitch_clip.log" || true ) &
                fi
            fi

            # 「配信を開始できますか？」系コメント → OBS配信を開始（既に配信中なら no-op）
            if [ "${STREAM_START_ON_COMMENT_ENABLED:-1}" = "1" ] && _is_stream_start_request "$msg"; then
                now_ts=0; last_ss_ts=0; ss_age=0
                now_ts=$(date +%s)
                last_ss_ts=$(cat "$STREAM_START_COOLDOWN_FILE" 2>/dev/null || echo 0)
                case "$last_ss_ts" in ''|*[!0-9]*) last_ss_ts=0 ;; esac
                ss_age=$((now_ts - last_ss_ts))
                if [ "$ss_age" -ge "$STREAM_START_COOLDOWN_SEC" ]; then
                    echo "$now_ts" > "$STREAM_START_COOLDOWN_FILE"
                    (
                        mkdir -p tmp/debug 2>/dev/null || true
                        ss_result=$(./obs_control.sh stream-start 2>>"tmp/debug/stream_start.log" || true)
                        case "$ss_result" in
                            *stream-start:started*)
                                source lib/outbound_queue.sh 2>/dev/null || true
                                enqueue_chat_message "同志、配信を開始しました！" "chat_daemon" 2>/dev/null || true
                                ;;
                        esac
                    ) &
                fi
                # continue しない: コメントとしても通常処理し、AIが反応できるようにする
            fi

            # !音声修復 — Elgato Wave Link を通常終了→再起動（1時間クールダウン）
            if [[ "$msg" =~ ^[[:space:]]*!音声修復([[:space:]]|$) ]]; then
                now_ts=0; last_repair_ts=0; repair_age=0
                now_ts=$(date +%s)
                last_repair_ts=$(cat "$WAVE_LINK_REPAIR_COOLDOWN_FILE" 2>/dev/null || echo 0)
                case "$last_repair_ts" in
                    ''|*[!0-9]*) last_repair_ts=0 ;;
                esac
                repair_age=$((now_ts - last_repair_ts))
                if [ "$repair_age" -ge "$WAVE_LINK_REPAIR_COOLDOWN_SEC" ]; then
                    if [ ! -x "$WAVE_LINK_REPAIR_SCRIPT" ]; then
                        source lib/outbound_queue.sh 2>/dev/null || true
                        enqueue_chat_message "音声修復スクリプトが見つからないため実行できませんでした。" "chat_daemon"
                        continue
                    fi
                    echo "$now_ts" > "$WAVE_LINK_REPAIR_COOLDOWN_FILE"
                    source lib/outbound_queue.sh 2>/dev/null || true
                    enqueue_chat_message "音声修復を開始します。Elgato Wave Link を通常再起動します。" "chat_daemon"
                    (
                        if "$WAVE_LINK_REPAIR_SCRIPT" >>"tmp/debug/wave_link_repair.log" 2>&1; then
                            source lib/outbound_queue.sh 2>/dev/null || true
                            enqueue_chat_message "音声修復が完了しました。" "chat_daemon"
                        else
                            source lib/outbound_queue.sh 2>/dev/null || true
                            enqueue_chat_message "音声修復に失敗しました。配信者側で確認します。" "chat_daemon"
                        fi
                    ) &
                else
                    remaining=$((WAVE_LINK_REPAIR_COOLDOWN_SEC - repair_age))
                    remaining_min=$(((remaining + 59) / 60))
                    source lib/outbound_queue.sh 2>/dev/null || true
                    enqueue_chat_message "音声修復はクールダウン中です。あと約${remaining_min}分待ってください。" "chat_daemon"
                fi
                continue
            fi

            # !ASMR — このコメントへの応答をささやき系ボイスで再生
            if [[ "$msg" =~ ^[[:space:]]*![Aa][Ss][Mm][Rr]([[:space:]]|$) ]]; then
                # !ASMR プレフィックスを除去してコメント本文を残す
                msg=$(echo "$msg" | sed -E 's/^[[:space:]]*![Aa][Ss][Mm][Rr][[:space:]]*//')
                clean_line="${user}: ${msg}"
                # ASMRフラグを立てる（次のコメント再生で使用）
                echo "$(date +%s)" > tmp/voicevox_asmr.txt
                # continue しない — コメントとして通常処理を続行
            fi

            # !NTROB — このコメントへの応答を波音リツ/クイーン(65)で再生
            if [[ "$msg" =~ ^[[:space:]]*![Nn][Tt][Rr][Oo][Bb]([[:space:]]|$) ]]; then
                msg=$(echo "$msg" | sed -E 's/^[[:space:]]*![Nn][Tt][Rr][Oo][Bb][[:space:]]*//')
                clean_line="${user}: ${msg}"
                echo "65" > tmp/voicevox_oneshot_speaker.txt
            fi

            # !doushi — このコメントへの応答を macOS say で再生
            if [[ "$msg" =~ ^[[:space:]]*![Dd][Oo][Uu][Ss][Hh][Ii]([[:space:]]|$) ]]; then
                msg=$(echo "$msg" | sed -E 's/^[[:space:]]*![Dd][Oo][Uu][Ss][Hh][Ii][[:space:]]*//')
                clean_line="${user}: ${msg}"
                echo "$(date +%s)" > tmp/voicevox_dousi.txt
            fi


            # !pitch ID VALUE — スピーカーIDごとにピッチ設定 (mod/broadcaster専用)
            if [[ "$msg" =~ ^[[:space:]]*!pitch[[:space:]]+([0-9]+)[[:space:]]+([-]?[0-9]*\.?[0-9]+) ]]; then
                if [ "$_is_mod_or_broadcaster" != "true" ]; then continue; fi
                _pitch_id="${BASH_REMATCH[1]}"
                _pitch_val="${BASH_REMATCH[2]}"
                _pitch_file="config/voicevox_pitch_map.txt"
                # 既存エントリを除去して新しい値を追加
                if [ -f "$_pitch_file" ]; then
                    grep -v "^${_pitch_id}|" "$_pitch_file" > "${_pitch_file}.tmp" 2>/dev/null || true
                    mv "${_pitch_file}.tmp" "$_pitch_file"
                fi
                echo "${_pitch_id}|${_pitch_val}" >> "$_pitch_file"
                source lib/outbound_queue.sh 2>/dev/null || true; enqueue_chat_message "pitch [${_pitch_id}] → ${_pitch_val}" "chat_daemon"
                continue
            fi

            # !tempo ID VALUE — スピーカーIDごとにテンポ設定 (mod/broadcaster専用)
            if [[ "$msg" =~ ^[[:space:]]*!tempo[[:space:]]+([0-9]+)[[:space:]]+([-]?[0-9]*\.?[0-9]+) ]]; then
                if [ "$_is_mod_or_broadcaster" != "true" ]; then continue; fi
                _tempo_id="${BASH_REMATCH[1]}"
                _tempo_val="${BASH_REMATCH[2]}"
                _tempo_file="config/voicevox_tempo_map.txt"
                if [ -f "$_tempo_file" ]; then
                    grep -v "^${_tempo_id}|" "$_tempo_file" > "${_tempo_file}.tmp" 2>/dev/null || true
                    mv "${_tempo_file}.tmp" "$_tempo_file"
                fi
                echo "${_tempo_id}|${_tempo_val}" >> "$_tempo_file"
                source lib/outbound_queue.sh 2>/dev/null || true; enqueue_chat_message "tempo [${_tempo_id}] → ${_tempo_val}" "chat_daemon"
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

            line_hash=$(printf '%s' "$clean_line" | md5sum 2>/dev/null | awk '{print $1}' || printf '%s' "$clean_line" | md5 -q 2>/dev/null)
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
            _notify_chat_overlay "$clean_line"
            if [ -n "$line_hash" ]; then
                _mark_recent_key "$RECENT_LINE_HASHES_FILE" "$line_hash"
            fi
        fi
    done
    if [ "${IRC_READ_TIMEOUT_SEC:-0}" -gt 0 ] && [ "${_irc_read_rc:-0}" -gt 128 ]; then
        echo "[$(date '+%H:%M:%S')] IRC heartbeat stall: no data for ${IRC_READ_TIMEOUT_SEC}s (rc=${_irc_read_rc}) → reconnecting" >> "$CHAT_DIR/daemon_reconnect.log"
    fi
    exec {TWITCH_IRC[0]}>&- 2>/dev/null || true
    exec {TWITCH_IRC[1]}>&- 2>/dev/null || true
    # read タイムアウト等で nc が CLOSE-WAIT のまま残らないよう、ソケットを確実に破棄する
    case "${TWITCH_IRC_PID:-}" in
        ''|*[!0-9]*) ;;
        *) kill -TERM "$TWITCH_IRC_PID" 2>/dev/null || true ;;
    esac
    wait "$TWITCH_IRC_PID" 2>/dev/null || true
    echo "[$(date '+%H:%M:%S')] IRC session ended; reconnecting in 5s" >> "$CHAT_DIR/daemon_reconnect.log"
    # --- Outbound chat queue consumer ---
    # chat_worker 配下では親 worker が送信を一元管理する。standalone daemon の時だけ消化する。
    _chat_worker_pid=""
    if [ -f "tmp/state/chat_worker.pid" ]; then
        _chat_worker_pid=$(cat "tmp/state/chat_worker.pid" 2>/dev/null || true)
    fi
    case "$_chat_worker_pid" in
        ''|*[!0-9]*) _chat_worker_pid="" ;;
    esac
    if [ -z "$_chat_worker_pid" ] || ! _pid_alive "$_chat_worker_pid"; then
        if source lib/outbound_queue.sh 2>/dev/null; then
            _outbound_rate_sec=2
            while outbound_queue_consume_once; do
                sleep "$_outbound_rate_sec"
            done
            outbound_queue_cleanup_sent 3600 2>/dev/null || true
        fi
    fi
    sleep 5
done
