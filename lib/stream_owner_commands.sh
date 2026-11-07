# shellcheck shell=bash disable=SC2034
# Default stop/run commands and operator pause marker of the encoder, by owner
# (docich ADR 0007). Sourced by workers/stream_noon_audit.sh.
#
#   soren   (default) the legacy lib/direct_stream.py runner, supervised by start_all.sh
#   docich  docich-stream.service; it restarts itself (Restart=always), so there is
#           deliberately no "start it ourselves" fallback and the pause marker is
#           the docich one (tmp/state/docich_stream.paused)
#
# stream_owner_defaults <owner> <soren_root> [<docich_root>] sets
# STREAM_OWNER_STOP_CMD / STREAM_OWNER_RUN_CMD / STREAM_OWNER_PAUSE_MARKER and
# returns 1 for any other value. The comparison is exact (no trimming), like
# start_all.sh and docich.stream_supervisor.
stream_owner_defaults() {
	local owner="${1:-soren}" root="${2:-.}" docich_root="${3:-/home/ubuntu/docich}"
	case "$owner" in
	soren)
		STREAM_OWNER_STOP_CMD='python3 lib/direct_stream.py stop'
		STREAM_OWNER_RUN_CMD='./direct_stream.sh run'
		STREAM_OWNER_PAUSE_MARKER='tmp/state/direct_stream.paused'
		;;
	docich)
		STREAM_OWNER_STOP_CMD="env PYTHONPATH=${docich_root}/src python3 -m docich.stream_supervisor --root ${root} stop"
		STREAM_OWNER_RUN_CMD='true'
		STREAM_OWNER_PAUSE_MARKER='tmp/state/docich_stream.paused'
		;;
	*)
		return 1
		;;
	esac
}
