#!/usr/bin/env bash
# Fail-open orchestration for synchronized native closed captions.
# The caller owns audio playback; every function here may fail without making
# the caller's audio path fail.

DOCICH_CC_PLAN_PID="${DOCICH_CC_PLAN_PID:-}"
DOCICH_CC_PLAN_READY="${DOCICH_CC_PLAN_READY:-0}"
DOCICH_CC_PLAN_CHUNK_COUNT="${DOCICH_CC_PLAN_CHUNK_COUNT:-0}"
DOCICH_CC_DIRTY="${DOCICH_CC_DIRTY:-0}"
DOCICH_CC_ACTIVE="${DOCICH_CC_ACTIVE:-0}"

_docich_cc_log() {
	if declare -F _log >/dev/null 2>&1; then
		_log "closed captions: $*"
	else
		printf '[closed_captions] %s\n' "$*" >&2
	fi
}

docich_cc_is_enabled() {
	case "${DOCICH_CC_ENABLED:-0}" in
	1 | true | TRUE | yes | YES | on | ON) ;;
	*) return 1 ;;
	esac
	[ "${IS_LINUX:-0}" = "1" ] || return 1
	[ "${USE_VOICEVOX:-0}" = "1" ] || return 1
	[ "${WAV_MODE:-false}" = "false" ] || return 1
	[ "${RENDER_ONLY:-false}" = "false" ] || return 1
	[ -f "${DOCICH_CC_CONTROLLER:-lib/closed_captions.py}" ] || return 1
	command -v "${DOCICH_CC_PYTHON:-python3}" >/dev/null 2>&1 || return 1
	[ -n "${DOCICH_CC_SOCKET:-}" ] && [ -S "$DOCICH_CC_SOCKET" ] || return 1
	return 0
}

docich_cc_init() {
	local token="$1" content_file="$2"
	DOCICH_CC_EXECUTION_ID="say-${token}"
	local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
	DOCICH_CC_SOCKET="${DOCICH_CC_SOCKET:-${runtime_dir%/}/docich/ffmpeg-cc.sock}"
	DOCICH_CC_CONTROLLER="${DOCICH_CC_CONTROLLER:-lib/closed_captions.py}"
	DOCICH_CC_PYTHON="${DOCICH_CC_PYTHON:-python3}"
	DOCICH_CC_PLAN_FILE="${content_file%.txt}_cc_plan.json"
	DOCICH_CC_CHUNKS_FILE="${content_file%.txt}_cc_chunks.txt"
	DOCICH_CC_PLAN_PID=""
	DOCICH_CC_PLAN_READY=0
	DOCICH_CC_PLAN_CHUNK_COUNT=0
	DOCICH_CC_DIRTY=0
	DOCICH_CC_ACTIVE=0
}

docich_cc_start_plan() {
	docich_cc_is_enabled || return 1
	[ "$#" -gt 0 ] || return 1
	: >"$DOCICH_CC_CHUNKS_FILE" || return 1
	local chunk
	for chunk in "$@"; do
		[ -n "$chunk" ] || continue
		printf '%s\n' "$chunk" >>"$DOCICH_CC_CHUNKS_FILE" || return 1
	done
	DOCICH_CC_PLAN_CHUNK_COUNT="$#"
	local plan_args=(
		"$DOCICH_CC_CONTROLLER" plan
		--chunks-file "$DOCICH_CC_CHUNKS_FILE"
		--execution-id "$DOCICH_CC_EXECUTION_ID"
		--output "$DOCICH_CC_PLAN_FILE"
	)
	if [ -n "${DOCICH_CC_TRANSLATIONS_FILE:-}" ]; then
		plan_args+=(--translations-file "$DOCICH_CC_TRANSLATIONS_FILE")
	fi
	"$DOCICH_CC_PYTHON" "${plan_args[@]}" >>"${DEBUG_LOG_FILE:-/dev/null}" 2>&1 &
	DOCICH_CC_PLAN_PID=$!
	_docich_cc_log "translation started (${DOCICH_CC_PLAN_CHUNK_COUNT} chunks)"
	return 0
}

docich_cc_wait_plan() {
	[ "$DOCICH_CC_PLAN_READY" = "1" ] && return 0
	[ -n "$DOCICH_CC_PLAN_PID" ] || return 1
	local plan_rc=0
	wait "$DOCICH_CC_PLAN_PID" || plan_rc=$?
	DOCICH_CC_PLAN_PID=""
	if [ "$plan_rc" -ne 0 ] || [ ! -s "$DOCICH_CC_PLAN_FILE" ]; then
		_docich_cc_log "translation unavailable; audio continues without captions"
		DOCICH_CC_PLAN_READY=0
		return 1
	fi
	DOCICH_CC_PLAN_READY=1
	_docich_cc_log "translation ready"
	return 0
}

_docich_cc_send() {
	local operation="$1"
	shift
	"$DOCICH_CC_PYTHON" "$DOCICH_CC_CONTROLLER" send "$operation" \
		--socket "$DOCICH_CC_SOCKET" \
		--timeout "${DOCICH_CC_SOCKET_TIMEOUT_SEC:-3}" \
		"$@" >>"${DEBUG_LOG_FILE:-/dev/null}" 2>&1
}

docich_cc_prepare() {
	local chunk_index="$1" sequence="$2"
	docich_cc_wait_plan || return 1
	# The request may reach FFmpeg even when the acknowledgement times out.
	# Mark it dirty before sending so cleanup still attempts an execution-scoped
	# clear and does not leave the filter stuck on an uncommitted page.
	DOCICH_CC_DIRTY=1
	if ! _docich_cc_send prepare \
		--plan "$DOCICH_CC_PLAN_FILE" \
		--chunk "$chunk_index" --page 0 --sequence "$sequence"; then
		_docich_cc_log "prepare failed; audio continues"
		return 1
	fi
	return 0
}

docich_cc_commit() {
	local sequence="$1"
	[ "$DOCICH_CC_DIRTY" = "1" ] || return 1
	if ! _docich_cc_send commit \
		--plan "$DOCICH_CC_PLAN_FILE" --page 0 --sequence "$sequence"; then
		_docich_cc_log "commit failed; audio continues"
		return 1
	fi
	DOCICH_CC_ACTIVE=1
	return 0
}

docich_cc_clear() {
	[ "$DOCICH_CC_DIRTY" = "1" ] || return 0
	if ! _docich_cc_send clear --execution-id "$DOCICH_CC_EXECUTION_ID"; then
		_docich_cc_log "clear failed; FFmpeg reconnect/reset remains available"
		return 1
	fi
	DOCICH_CC_DIRTY=0
	DOCICH_CC_ACTIVE=0
	return 0
}

docich_cc_cleanup() {
	if [ -n "$DOCICH_CC_PLAN_PID" ] && kill -0 "$DOCICH_CC_PLAN_PID" 2>/dev/null; then
		kill -TERM "$DOCICH_CC_PLAN_PID" 2>/dev/null || true
		wait "$DOCICH_CC_PLAN_PID" 2>/dev/null || true
	fi
	DOCICH_CC_PLAN_PID=""
	docich_cc_clear >/dev/null 2>&1 || true
	if [ -n "${DOCICH_CC_PLAN_FILE:-}" ]; then
		rm -f "$DOCICH_CC_PLAN_FILE" 2>/dev/null || true
	fi
	if [ -n "${DOCICH_CC_CHUNKS_FILE:-}" ]; then
		rm -f "$DOCICH_CC_CHUNKS_FILE" 2>/dev/null || true
	fi
}
