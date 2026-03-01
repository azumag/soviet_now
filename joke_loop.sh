#!/bin/bash
# joke_loop.sh - 一定間隔でジョークコマンドを表示し続けるスクリプト
#
# 使い方:
#   ./joke_loop.sh          # デフォルト30秒間隔
#   ./joke_loop.sh 10       # 10秒間隔
#   ./joke_loop.sh 5 true   # 5秒間隔、毎回必ず表示（確率スキップなし）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTERVAL="${1:-30}"
ALWAYS="${2:-false}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# --- 多重起動防止 ---
LOCKFILE="tmp/joke_loop.lock"
mkdir -p tmp
if [ -f "$LOCKFILE" ]; then
	old_pid=$(cat "$LOCKFILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		log "ERROR: joke_loop.sh is already running (PID=$old_pid). Aborting."
		exit 1
	fi
fi
echo $$ > "$LOCKFILE"

# --- クリーンアップ ---
cleanup() {
	rm -f "$LOCKFILE"
	printf '\r\033[K' >&2
	log "joke_loop 終了"
	exit 0
}
trap 'cleanup' EXIT INT TERM

# --- ジョーク表示 ---
show_joke() {
	printf '\r\033[K' >&2

	local jokes=()
	command -v sl        &>/dev/null && jokes+=("sl")
	command -v fortune   &>/dev/null && command -v cowsay &>/dev/null && jokes+=("fortune_cowsay")
	command -v toilet    &>/dev/null && jokes+=("toilet")
	command -v figlet    &>/dev/null && jokes+=("figlet")
	command -v nyancat   &>/dev/null && jokes+=("nyancat")
	command -v aafire    &>/dev/null && jokes+=("aafire")
	command -v boxes     &>/dev/null && command -v fortune &>/dev/null && jokes+=("boxes")
	command -v genact    &>/dev/null && jokes+=("genact")
	command -v cmatrix   &>/dev/null && jokes+=("cmatrix")
	command -v lolcat    &>/dev/null && command -v fortune &>/dev/null && jokes+=("lolcat")
	command -v tty-clock &>/dev/null && jokes+=("tty-clock")

	if [ ${#jokes[@]} -eq 0 ]; then
		log "ジョークコマンドが見つかりません。以下をインストールしてください:"
		log "  brew install sl fortune cowsay figlet toilet cmatrix genact lolcat"
		return
	fi

	local pick="${jokes[$((RANDOM % ${#jokes[@]}))]}"
	log "🎪 [$pick]"

	local fullscreen=0
	case "$pick" in nyancat|aafire|cmatrix|tty-clock) fullscreen=1 ;; esac
	[ "$fullscreen" -eq 1 ] && tput smcup >&2 2>/dev/null

	case "$pick" in
	sl)
		timeout 10 sl -l >&2 2>/dev/null || true
		;;
	fortune_cowsay)
		fortune 2>/dev/null | cowsay >&2 2>/dev/null || true
		sleep 5
		;;
	toilet)
		local words=("HELLO!" "LOL" "WOW" "NICE" "SOREN" "BOOM" "YAY!" "HEH")
		echo "${words[$((RANDOM % ${#words[@]}))]}" | toilet --gay 2>/dev/null >&2 || true
		sleep 4
		;;
	figlet)
		local words=("HELLO!" "LOL" "WOW" "NICE" "SOREN" "BOOM" "YAY!" "HEH")
		echo "${words[$((RANDOM % ${#words[@]}))]}" | figlet >&2 2>/dev/null || true
		sleep 4
		;;
	nyancat)
		timeout 10 nyancat >&2 2>/dev/null || true
		;;
	aafire)
		timeout 10 aafire >&2 2>/dev/null || true
		;;
	boxes)
		fortune 2>/dev/null | boxes >&2 2>/dev/null || true
		sleep 5
		;;
	genact)
		timeout 12 genact >&2 2>/dev/null || true
		;;
	cmatrix)
		timeout 10 cmatrix -b >&2 2>/dev/null || true
		;;
	lolcat)
		fortune 2>/dev/null | lolcat >&2 2>/dev/null || true
		sleep 5
		;;
	tty-clock)
		timeout 10 tty-clock -scC 1 >&2 2>/dev/null || true
		;;
	esac

	[ "$fullscreen" -eq 1 ] && tput rmcup >&2 2>/dev/null
	printf '\r\033[K' >&2
}

# --- メインループ ---
log "=== Joke Loop 開始 ==="
log "間隔: ${INTERVAL}秒 | 常時表示: ${ALWAYS} | Ctrl+C で停止"

count=0
while true; do
	count=$((count + 1))

	if [ "$ALWAYS" = "true" ]; then
		show_joke
	else
		# 約30%の確率で表示（_maybe_show_joke より高頻度）
		[ $((RANDOM % 3)) -eq 0 ] && show_joke
	fi

	log "--- 待機中... (${count}回目完了) ---"
	sleep "$INTERVAL"
done
