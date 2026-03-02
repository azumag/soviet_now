#!/bin/bash
# joke_loop.sh - 一定間隔でジョークコマンドを実行し続ける
# 使い方: ./joke_loop.sh [秒数(デフォルト15)]

INTERVAL="${1:-15}"

JOKES=()
command -v fortune &>/dev/null && command -v cowsay &>/dev/null && JOKES+=("fortune | cowsay")
command -v fortune &>/dev/null && command -v lolcat &>/dev/null && JOKES+=("fortune | lolcat")
command -v fortune &>/dev/null && command -v boxes  &>/dev/null && JOKES+=("fortune | boxes")
command -v fortune &>/dev/null && JOKES+=("fortune")
command -v figlet  &>/dev/null && JOKES+=("figlet")
command -v toilet  &>/dev/null && JOKES+=("toilet")
command -v genact  &>/dev/null && JOKES+=("genact")

if [ ${#JOKES[@]} -eq 0 ]; then
	echo "brew install fortune cowsay figlet toilet lolcat boxes genact"
	exit 1
fi

WORDS=("HELLO" "LOL" "WOW" "NICE" "SOREN" "BOOM" "YAY" "COOL" "YEAH")

while true; do
	pick="${JOKES[$((RANDOM % ${#JOKES[@]}))]}"
	word="${WORDS[$((RANDOM % ${#WORDS[@]}))]}"
	case "$pick" in
		figlet) echo "$word" | timeout 5 figlet ;;
		toilet) echo "$word" | timeout 5 toilet --gay ;;
		genact) timeout 10 genact ;;
		*)      timeout 5 bash -c "$pick" ;;
	esac
	sleep "$INTERVAL"
done
