#!/bin/bash
# joke_loop.sh - 一定間隔でジョークコマンドを表示し続けるスクリプト
#
# 使い方:
#   ./joke_loop.sh          # デフォルト15秒間隔
#   ./joke_loop.sh 10       # 10秒間隔

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTERVAL="${1:-15}"

# --- 多重起動防止 ---
LOCKFILE="tmp/joke_loop.lock"
mkdir -p tmp
if [ -f "$LOCKFILE" ]; then
	old_pid=$(cat "$LOCKFILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		echo "ERROR: joke_loop.sh is already running (PID=$old_pid). Aborting."
		exit 1
	fi
fi
echo $$ > "$LOCKFILE"

trap 'rm -f "$LOCKFILE"; echo; echo "bye!"; exit 0' EXIT INT TERM

# --- 利用可能なジョークを収集 ---
JOKES=()
command -v sl        &>/dev/null && JOKES+=("sl")
command -v fortune   &>/dev/null && command -v cowsay &>/dev/null && JOKES+=("fortune_cowsay")
command -v toilet    &>/dev/null && JOKES+=("toilet")
command -v figlet    &>/dev/null && JOKES+=("figlet")
command -v boxes     &>/dev/null && command -v fortune &>/dev/null && JOKES+=("boxes")
command -v genact    &>/dev/null && JOKES+=("genact")
command -v lolcat    &>/dev/null && command -v fortune &>/dev/null && JOKES+=("lolcat")
command -v fortune   &>/dev/null && JOKES+=("fortune")

if [ ${#JOKES[@]} -eq 0 ]; then
	echo "ジョークコマンドが見つかりません。以下をインストールしてください:"
	echo "  brew install sl fortune cowsay figlet toilet genact lolcat boxes"
	exit 1
fi

# --- 区切り線 ---
COLS=$(tput cols 2>/dev/null || echo 60)
separator() {
	printf '%*s\n' "$COLS" '' | tr ' ' '─'
}

# --- ジョーク表示 ---
WORDS=("HELLO!" "LOL" "WOW" "NICE" "SOREN" "BOOM" "YAY!" "HEH" "HAHA" "COOL" "YEAH")

show_joke() {
	local pick="${JOKES[$((RANDOM % ${#JOKES[@]}))]}"
	local word="${WORDS[$((RANDOM % ${#WORDS[@]}))]}"

	echo
	separator
	echo "  $(date '+%H:%M:%S')  [$pick]"
	separator
	echo

	case "$pick" in
	sl)
		timeout 10 sl -l 2>/dev/null || true
		;;
	fortune_cowsay)
		fortune 2>/dev/null | cowsay 2>/dev/null || true
		;;
	toilet)
		echo "$word" | toilet --gay 2>/dev/null || true
		;;
	figlet)
		echo "$word" | figlet 2>/dev/null || true
		;;
	boxes)
		fortune 2>/dev/null | boxes 2>/dev/null || true
		;;
	genact)
		timeout 12 genact 2>/dev/null || true
		;;
	lolcat)
		fortune 2>/dev/null | lolcat 2>/dev/null || true
		;;
	fortune)
		fortune 2>/dev/null || true
		;;
	esac

	echo
}

# --- メインループ ---
echo
echo "  ╔══════════════════════════════════╗"
echo "  ║     JOKE LOOP - Ctrl+C で停止    ║"
echo "  ║     間隔: ${INTERVAL}秒                    ║"
echo "  ║     検出: ${#JOKES[@]}個 (${JOKES[*]})  "
echo "  ╚══════════════════════════════════╝"
echo

while true; do
	show_joke
	sleep "$INTERVAL"
done
