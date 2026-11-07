#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では OBS ウィンドウキャプチャ設定を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# obs_window_capture_source.sh - ensure an OBS window capture source targets a real window.
#
# macOS keeps using ScreenCaptureKit (`screen_capture`). On Linux/X11 the
# default is OBS's XComposite source (`xcomposite_input`), which stores its
# selected window in `capture_window`. Linux operators may explicitly opt into
# OBS's full-display XSHM source (`xshm_input`) when XComposite cannot produce
# frames. Explicit input-kind/family/property overrides remain authoritative for
# unusual OBS builds.
#
# Usage:
#   ./obs_window_capture_source.sh ensure <scene> <source> <window-title-regex> [app-id] [show|hide]

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_window_capture_source.sh ensure <scene> <source> <window-title-regex> [app-id] [show|hide]
EOF
	exit 2
}

cmd="${1:-}"
[ "$cmd" = "ensure" ] || usage
[ "$#" -ge 4 ] || usage

scene="$2"
source_name="$3"
window_title_regex="$4"
app_id="${5:-com.google.chrome.for.testing}"
visibility="${6:-show}"

case "$visibility" in
show|hide) ;;
*) usage ;;
esac

# docich ADR 0007 / #3553 (G2): OBS は撤去済み。配信 encoder は docich-stream.service が所有する。
# 呼び出し側の契約を保つため引数検証は残し、OBS WebSocket へは接続せず常に成功する。
# 以前は OBS_WEBSOCKET_PORT/PASSWORD が設定されていると、動いていない OBS へ毎回接続を試みて失敗していた。
exit 0
