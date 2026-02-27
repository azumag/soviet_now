#!/bin/bash
# twitch_chat_daemon.sh - Twitch IRC 常駐プロセス（twitch_chat.sh から起動される）
# 4分ごとに再接続し、PRIVMSGをraw.logに追記する

CHAT_DIR="tmp/.twitch_chat"
RAW_LOG="$CHAT_DIR/raw.log"
CHANNEL="${1:-azumagbanjo}"

cd "$(dirname "$0")"
mkdir -p "$CHAT_DIR"

while true; do
    nick="justinfan$((RANDOM % 90000 + 10000))"
    {
        echo "NICK $nick"
        echo "JOIN #${CHANNEL}"
        sleep 240
    } | nc -w 250 irc.chat.twitch.tv 6667 2>/dev/null | while IFS= read -r line; do
        if [[ "$line" == *"PRIVMSG"* ]]; then
            user=$(echo "$line" | sed 's/^:\([^!]*\)!.*/\1/')
            msg=$(echo "$line" | sed 's/^.*PRIVMSG [^ ]* ://')
            # サニタイズ: 制御文字 + シェルメタ文字除去
            msg=$(echo "$msg" | tr -d '\000-\010\013-\037\r' | tr -d '`$\\{}|;<>&')
            user=$(echo "$user" | tr -d '`$\\{}|;<>&')
            echo "${user}: ${msg}" >> "$RAW_LOG"
        fi
    done
    sleep 5
done
