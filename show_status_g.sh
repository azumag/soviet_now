#!/bin/zsh
# show_status_g.sh - CUI Graphical Statistics Dashboard
#
# Usage: ./show_status_g.sh       # 10秒間隔で常時表示
#        ./show_status_g.sh 5     # 5秒間隔で常時表示

SCRIPT_DIR="${0:a:h}"
cd "$SCRIPT_DIR"

WATCH_INTERVAL=${1:-10}

CLR=$'\033[K'

render() {
	local buf=""
	while IFS= read -r line; do
		buf+="${line}${CLR}"$'\n'
	done
	printf '\033[H%s\033[J' "$buf"
}

printf '\033[?25l'          # カーソル非表示
trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
printf '\033[2J'            # 初回だけ画面クリア

while true; do
	python3 status_dashboard.py 2>/dev/null | render
	sleep "$WATCH_INTERVAL"
done
