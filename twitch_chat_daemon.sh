#!/bin/bash
# twitch_chat_daemon.sh - Twitch IRC 常駐プロセス（twitch_chat.sh から起動される）
# 4分ごとに再接続し、PRIVMSGをraw.logに追記する

CHAT_DIR="${TWITCH_CHAT_DIR:-tmp/.twitch_chat}"
RAW_LOG="$CHAT_DIR/raw.log"
CHANNEL="${1:-azumagbanjo}"

cd "$(dirname "$0")"
mkdir -p "$CHAT_DIR"

while true; do
    nick="justinfan$((RANDOM % 90000 + 10000))"
    {
        # display-name などのメタ情報タグを受け取る
        echo "CAP REQ :twitch.tv/tags"
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

            msg=$(echo "$payload" | sed 's/^.*PRIVMSG [^ ]* ://')
            # サニタイズ: 制御文字 + シェルメタ文字除去
            msg=$(echo "$msg" | tr -d '\000-\010\013-\037\r' | tr -d '`$\\{}|;<>&')
            user=$(echo "$user" | tr -d '`$\\{}|;<>&')
            # msg-id を先頭に保持しておくと、再接続時の同一コメント重複を抑止しやすい
            if [ -n "$msg_id" ]; then
                echo "id=${msg_id}"$'\t'"${user}: ${msg}" >> "$RAW_LOG"
            else
                echo "${user}: ${msg}" >> "$RAW_LOG"
            fi
        fi
    done
    sleep 5
done
