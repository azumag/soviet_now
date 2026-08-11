# obs_source_lock.sh — cross-process mutex for OBS mac-capture source updates.
#
# WHY: macOS mac-capture (ScreenCaptureKit) double-frees / heap-corrupts inside
# obs_source_update when two SetInputSettings calls race on SCStream teardown —
# even across DIFFERENT sources and DIFFERENT OS processes. OBS crash reports show
# SIGABRT (___BUG_IN_CLIENT_OF_LIBMALLOC_POINTER_BEING_FREED_WAS_NOT_ALLOCATED) in
# mac-capture from the obs-websocket thread. The in-process lock in
# wildcard_parallel.py only serializes its own slot threads; it cannot see the
# game-capture watchdog, the main soviet_local bridge, or soren91. During a
# param-parallel / wildcard session those still fire SetInputSettings on
# `sorengame` (watchdog rebind/bounce, main bridge) while candidate updates fire on
# `wildcardParallelCandN` — that cross-process race is what crashes OBS entirely.
#
# So EVERY site that issues SetInputSettings on a mac-capture (screen_capture /
# sck_audio_capture) source must hold THIS one filesystem lock.
# Atomic mkdir (macOS-compatible), stale-owner detection, and a post-op settle so
# macOS finishes tearing down the old SCStream before the next holder runs.
# Linux callers retain ordering but default the macOS-only settle delay to zero.
#
# Usage (source this, then wrap the SetInputSettings call):
#     . ./lib/obs_source_lock.sh
#     obs_source_lock_acquire || true
#     trap 'obs_source_lock_release || true' EXIT
#     ... node call that does SetInputSettings ...
#
# Mirrored in lib/obs_source_lock.mjs for the Node.js sites (watchdog, bridge).

if [ -z "${OBS_SOURCE_LOCK_DIR:-}" ]; then
	_osl_self="${BASH_SOURCE[0]:-$0}"
	_osl_repo="$(cd "$(dirname "$_osl_self")/.." 2>/dev/null && pwd)"
	[ -n "$_osl_repo" ] || _osl_repo="$PWD"
	OBS_SOURCE_LOCK_DIR="${_osl_repo}/tmp/state/obs_source_update.lock"
fi
: "${OBS_SOURCE_LOCK_STALE_SEC:=30}"
if [ "${OBS_SOURCE_LOCK_SETTLE_SEC+x}" != x ]; then
	_obs_source_lock_platform="${SOREN_OBS_PLATFORM:-}"
	if [ -z "$_obs_source_lock_platform" ]; then
		case "$(uname -s 2>/dev/null || true)" in
			Linux) _obs_source_lock_platform=linux ;;
			*) _obs_source_lock_platform=darwin ;;
		esac
	else
		_obs_source_lock_platform=$(printf '%s' "$_obs_source_lock_platform" | tr '[:upper:]' '[:lower:]')
	fi
	if [ "$_obs_source_lock_platform" = "linux" ]; then
		OBS_SOURCE_LOCK_SETTLE_SEC=0
	else
		# Preserve the established macOS ScreenCaptureKit teardown guard.
		OBS_SOURCE_LOCK_SETTLE_SEC=2
	fi
	unset _obs_source_lock_platform
fi
: "${OBS_SOURCE_LOCK_MAX_WAIT_SEC:=30}"
_OBS_SOURCE_LOCK_HELD=0

obs_source_lock_acquire() {
	mkdir -p "$(dirname "$OBS_SOURCE_LOCK_DIR")" 2>/dev/null || true
	local deadline now mt age owner_raw owner_pid alive
	now=$(date +%s 2>/dev/null || echo 0)
	deadline=$((now + OBS_SOURCE_LOCK_MAX_WAIT_SEC))
	while ! mkdir "$OBS_SOURCE_LOCK_DIR" 2>/dev/null; do
		if [ -d "$OBS_SOURCE_LOCK_DIR" ]; then
			alive=false
			owner_raw=$(cat "$OBS_SOURCE_LOCK_DIR/owner" 2>/dev/null || true)
			owner_pid="${owner_raw%%:*}"
			case "$owner_pid" in '' | *[!0-9]*) owner_pid="" ;; esac
			if [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
				alive=true
			fi
			now=$(date +%s 2>/dev/null || echo 0)
			mt=$(stat -f %m "$OBS_SOURCE_LOCK_DIR" 2>/dev/null) \
				|| mt=$(stat -c %Y "$OBS_SOURCE_LOCK_DIR" 2>/dev/null) \
				|| mt="$now"
			age=$((now - mt))
			if [ "$alive" = false ] && [ "$age" -gt "$OBS_SOURCE_LOCK_STALE_SEC" ]; then
				rm -rf "$OBS_SOURCE_LOCK_DIR" 2>/dev/null || true
				continue
			fi
		fi
		now=$(date +%s 2>/dev/null || echo 0)
		if [ "$now" -ge "$deadline" ]; then
			# Never hang the broadcast: give up waiting and proceed best-effort
			# (unlocked). Serialization is degraded for this one call, not lost
			# forever, and a hung lock would freeze the stream which is worse.
			return 1
		fi
		sleep 0.2 2>/dev/null || true
	done
	printf '%s:%s' "$$" "$(date +%s 2>/dev/null || echo 0)" >"$OBS_SOURCE_LOCK_DIR/owner" 2>/dev/null || true
	_OBS_SOURCE_LOCK_HELD=1
	return 0
}

obs_source_lock_release() {
	[ "${_OBS_SOURCE_LOCK_HELD:-0}" = 1 ] || return 0
	# Settle while STILL holding the lock so the next holder waits out the macOS
	# SCStream teardown before issuing its own SetInputSettings.
	if [ "${OBS_SOURCE_LOCK_SETTLE_SEC:-0}" != 0 ]; then
		sleep "$OBS_SOURCE_LOCK_SETTLE_SEC" 2>/dev/null || true
	fi
	rm -rf "$OBS_SOURCE_LOCK_DIR" 2>/dev/null || true
	_OBS_SOURCE_LOCK_HELD=0
	return 0
}
