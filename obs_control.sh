#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では OBS 制御を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# obs_control.sh - OBS WebSocket v5 wrapper
# Usage:
#   ./obs_control.sh show <scene> <source> [<source>...]
#   ./obs_control.sh hide <scene> <source> [<source>...]
#   ./obs_control.sh status <scene> <source> [<source>...]
#   ./obs_control.sh batch <scene> show:<src1>,<src2> hide:<src3>,<src4>
#   ./obs_control.sh transform <scene> <source> <x> <y> <scaleX> <scaleY> [<boundsW> <boundsH>]
#   ./obs_control.sh stack <scene>
#     By default this only initializes a source that still has OBS' default
#     transform. Set OBS_CONTROL_TRANSFORM_MODE=force to overwrite manually
#     adjusted OBS transforms.

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

_log() { echo "[obs_control $(date '+%H:%M:%S')] $*" >&2; }

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_control.sh show <scene> <source> [<source>...]
  ./obs_control.sh hide <scene> <source> [<source>...]
  ./obs_control.sh status <scene> <source> [<source>...]
  ./obs_control.sh batch <scene> show:<src1>,<src2> hide:<src3>,<src4>
  ./obs_control.sh transform <scene> <source> <x> <y> <scaleX> <scaleY> [<boundsW> <boundsH>]
  ./obs_control.sh stack <scene>
    Set OBS_CONTROL_TRANSFORM_MODE=force to overwrite an existing manual transform.
  ./obs_control.sh stream-status            # prints "streaming=on|off"
  ./obs_control.sh stream-start             # start streaming (no-op if already live)
  ./obs_control.sh stream-stop              # stop streaming (no-op if not live)
EOF
	exit 2
}

ACTION="${1:-}"
TARGET="${2:-}"

case "$ACTION" in
show|hide|status)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 3 ] || usage
	;;
batch)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 3 ] || usage
	;;
stack)
	[ -n "$TARGET" ] || usage
	;;
transform)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 7 ] || usage
	;;
stream-status|stream-start|stream-stop)
	# 配信(ストリーム)制御。scene/source 指定は不要。
	;;
*)
	usage
	;;
esac

# docich ADR 0007 / #3553 (G1): OBS は撤去済み。配信 encoder は docich-stream.service が所有する。
# 呼び出し側 (eloop*, overlay_notify, twitch_chat_daemon ほか) の契約を保つため、引数検証は残し、
# 常に何もせず成功する。以前は SOREN_STREAM_BACKEND が obs 以外のときだけここで終了していた
# (未設定の既定が obs で、.env の編集ミスひとつで OBS WebSocket に接続しに行く経路だった)。
exit 0
