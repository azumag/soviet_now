#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では OBS ブラウザソース操作を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# obs_browser_source.sh - ensure an OBS browser source points at a local HTML file or URL.
#
# Usage:
#   ./obs_browser_source.sh ensure <scene> <source> <html-file-or-url> [width] [height] [show|hide]

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_browser_source.sh ensure <scene> <source> <html-file-or-url> [width] [height] [show|hide]
EOF
	exit 2
}

cmd="${1:-}"
[ "$cmd" = "ensure" ] || usage
[ "$#" -ge 4 ] || usage

scene="$2"
source_name="$3"
target="$4"
width="${5:-1920}"
height="${6:-1080}"
visibility="${7:-show}"

case "$visibility" in
show|hide) ;;
*) usage ;;
esac

# docich ADR 0007 / #3553 (G2): OBS は撤去済み。配信 encoder は docich-stream.service が所有する。
# 呼び出し側の契約を保つため引数検証は残し、OBS WebSocket へは接続せず常に成功する。
# 以前は OBS_WEBSOCKET_PORT/PASSWORD が設定されていると、動いていない OBS へ毎回接続を試みて失敗していた。
exit 0
