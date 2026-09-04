#!/usr/bin/env bash
# diagnostics_runner.sh - docich#33 read-only diagnostic sandbox runner.
#
# Runs one operator-approved diagnostic script against redacted evidence
# fetched through redacted_diag_broker.py, inside a sandbox that provides:
#   - env -i (no inherited environment / credentials)
#   - read-only input (evidence snapshots only, copied out by the broker)
#   - a write-only-to-its-own-scratch output area (tmpfs on Linux; a plain
#     directory fallback where tmpfs is unavailable, e.g. macOS - see the
#     _prepare_output_dir comment below)
#   - no outbound network
#   - no ability to signal other processes
#   - a hard time limit and an output-size limit
#
# The script's stdout must be exactly a JSON array of
# {finding, evidence_ref, confidence, recommended_action} objects (schema
# enforced by lib/redacted_diag_report.py). Anything else - non-JSON output,
# extra/missing keys, an evidence_ref that was never fetched, a non-zero
# exit, or a timeout - becomes a failure report; production state is never
# touched either way; the runner does not generate or apply any code change.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$SCRIPT_DIR"
PYTHON_BIN="${DIAG_RUNNER_PYTHON:-python3}"
BROKER="$REPO_ROOT/lib/redacted_diag_broker.py"
REPORT_PY="$REPO_ROOT/lib/redacted_diag_report.py"

_usage() {
	cat <<'USAGE' >&2
usage: diagnostics_runner.sh run \
  --event-id ID --snapshot-dir DIR --script PATH --report-out FILE \
  --evidence-ref REF [--evidence-ref REF ...] \
  [--time-limit SEC] [--max-output-bytes N] [--work-root DIR]
USAGE
}

_timeout_bin() {
	if command -v timeout >/dev/null 2>&1; then
		echo "timeout"
	elif command -v gtimeout >/dev/null 2>&1; then
		echo "gtimeout"
	else
		echo ""
	fi
}

# Prepare the write-only scratch area for one run.
#
# docich#33 acceptance note: the design calls for a write-only tmpfs. Linux
# supports mounting one per-run (attempted below via `mount -t tmpfs`, which
# needs either root or a kernel with unprivileged user namespaces enabled -
# both common on production Linux hosts, so this is expected to succeed
# there). macOS has no tmpfs at all, so local verification on this
# development machine ALWAYS falls through to the plain-directory fallback
# below; that substitution is called out explicitly in every report this
# script writes via the `tmpfs_used` field, and is documented as a known
# macOS-only limitation, not a silent downgrade.
_prepare_output_dir() {
	local output_dir="$1"
	mkdir -p "$output_dir" || return 1
	TMPFS_USED="false"
	TMPFS_MOUNTED_BY_US="false"
	if [ "$(uname -s)" = "Linux" ] && command -v mount >/dev/null 2>&1; then
		if mount -t tmpfs -o "size=16m,mode=0700,uid=$(id -u),gid=$(id -g)" tmpfs "$output_dir" 2>/dev/null; then
			TMPFS_USED="true"
			TMPFS_MOUNTED_BY_US="true"
		fi
	fi
	chmod 700 "$output_dir" 2>/dev/null || true
}

_cleanup_output_dir() {
	local output_dir="$1"
	if [ "${TMPFS_MOUNTED_BY_US:-false}" = "true" ]; then
		umount "$output_dir" 2>/dev/null || true
	fi
}

# Choose (and, for macOS, write) the sandbox backend for this run.
# Sets SANDBOX_BACKEND and SANDBOX_PREFIX (an array-by-name via SANDBOX_PREFIX_*
# because bash 3 arrays can't easily be returned - see _build_sandbox_argv).
_build_sandbox_argv() {
	local input_dir="$1" output_dir="$2" script_abs="$3" profile_file="$4"
	local os_name
	os_name="$(uname -s)"
	SANDBOX_ARGV=()
	if [ "$os_name" = "Darwin" ]; then
		SANDBOX_BACKEND="macos-sandbox-exec-seatbelt"
		{
			printf '(version 1)\n'
			printf '(allow default)\n'
			printf '(deny network*)\n'
			printf '(deny signal)\n'
			printf '(deny file-write* (subpath "/"))\n'
			printf '(allow file-write* (subpath "%s"))\n' "$output_dir"
			printf '(deny file-read* (subpath "%s"))\n' "$REPO_ROOT"
			printf '(allow file-read* (literal "%s"))\n' "$script_abs"
			printf '(allow file-read* (subpath "%s"))\n' "$input_dir"
			printf '(allow file-read* (subpath "%s"))\n' "$output_dir"
		} >"$profile_file"
		SANDBOX_ARGV=(sandbox-exec -f "$profile_file")
		return 0
	fi
	if [ "$os_name" = "Linux" ]; then
		if command -v bwrap >/dev/null 2>&1; then
			# NOT executed/verified in this development sandbox (macOS only,
			# no Linux host available). Kept minimal and conservative:
			# no network namespace share, read-only bind of input+script,
			# read-write bind of output only.
			SANDBOX_BACKEND="linux-bwrap (UNVERIFIED in this environment)"
			SANDBOX_ARGV=(
				bwrap --unshare-all --die-with-parent
				--ro-bind "$input_dir" "$input_dir"
				--ro-bind "$script_abs" "$script_abs"
				--bind "$output_dir" "$output_dir"
				--proc /proc --dev /dev
			)
			return 0
		fi
		if unshare -rn true >/dev/null 2>&1; then
			# NOT executed/verified in this development sandbox.
			SANDBOX_BACKEND="linux-unshare-net-userns (UNVERIFIED in this environment)"
			SANDBOX_ARGV=(unshare -rn)
			return 0
		fi
		SANDBOX_BACKEND="linux-no-isolation-fallback (WARNING: network/signal isolation NOT enforced)"
		SANDBOX_ARGV=()
		return 0
	fi
	SANDBOX_BACKEND="unknown-os-no-isolation-fallback (WARNING: network/signal isolation NOT enforced)"
	SANDBOX_ARGV=()
}

_run() {
	local event_id="" snapshot_dir="" script="" report_out="" work_root="${DIAG_RUNNER_WORK_ROOT:-tmp/diag_runs}"
	local time_limit="${DIAG_RUNNER_TIME_LIMIT_SEC:-30}" max_output_bytes="${DIAG_RUNNER_MAX_OUTPUT_BYTES:-65536}"
	local -a evidence_refs=()

	while [ $# -gt 0 ]; do
		case "$1" in
		--event-id) event_id="$2"; shift 2 ;;
		--snapshot-dir) snapshot_dir="$2"; shift 2 ;;
		--script) script="$2"; shift 2 ;;
		--report-out) report_out="$2"; shift 2 ;;
		--evidence-ref) evidence_refs+=("$2"); shift 2 ;;
		--time-limit) time_limit="$2"; shift 2 ;;
		--max-output-bytes) max_output_bytes="$2"; shift 2 ;;
		--work-root) work_root="$2"; shift 2 ;;
		*) echo "unknown argument: $1" >&2; _usage; return 2 ;;
		esac
	done

	if [ -z "$event_id" ] || [ -z "$snapshot_dir" ] || [ -z "$script" ] || [ -z "$report_out" ] || [ "${#evidence_refs[@]}" -eq 0 ]; then
		_usage
		return 2
	fi
	case "$time_limit" in ''|*[!0-9]*) echo "invalid --time-limit" >&2; return 2 ;; esac
	case "$max_output_bytes" in ''|*[!0-9]*) echo "invalid --max-output-bytes" >&2; return 2 ;; esac

	local script_abs=""
	# pwd -P resolves symlinks (e.g. macOS /var -> /private/var under
	# mktemp): the Seatbelt profile below does literal subpath comparison,
	# so every path it references must be the canonical path the kernel
	# will actually see when the sandboxed process opens it.
	script_abs="$(cd "$(dirname "$script")" 2>/dev/null && pwd -P)/$(basename "$script")" || {
		"$PYTHON_BIN" "$REPORT_PY" --event-id "$event_id" --out "$report_out" --tmpfs-used false \
			--sandbox-backend "none" --fail-reason "script not found: $script" >/dev/null
		return 1
	}
	if [ ! -f "$script_abs" ]; then
		"$PYTHON_BIN" "$REPORT_PY" --event-id "$event_id" --out "$report_out" --tmpfs-used false \
			--sandbox-backend "none" --fail-reason "script not found: $script" >/dev/null
		return 1
	fi

	local run_id run_dir input_dir output_dir stdout_file stderr_file profile_file
	run_id="$(date +%s)_$$_${RANDOM}"
	run_dir="$work_root/$run_id"
	mkdir -p "$run_dir/input" "$run_dir/output" || return 1
	# Canonicalize run_dir itself (not just its children) once, up front:
	# every path derived below must match what the kernel resolves at open()
	# time, since the Seatbelt profile does literal subpath comparison.
	run_dir="$(cd "$run_dir" && pwd -P)"
	input_dir="$run_dir/input"
	output_dir="$run_dir/output"
	stdout_file="$run_dir/stdout.json"
	stderr_file="$run_dir/stderr.log"
	profile_file="$run_dir/seatbelt.sb"

	# trap makes sure the ephemeral run_dir (which briefly holds copies of
	# redacted evidence and any stray output) never survives this invocation,
	# on every exit path including timeout/crash/early-return.
	# DIAG_RUNNER_KEEP_RUN_DIR=1 is a manual-debugging escape hatch only
	# (defeats the ephemeral-workdir guarantee) -- never set it in the
	# automated pipeline or production default.
	if [ "${DIAG_RUNNER_KEEP_RUN_DIR:-0}" = "1" ]; then
		# shellcheck disable=SC2064
		trap "_cleanup_output_dir '$output_dir'" EXIT INT TERM
	else
		# shellcheck disable=SC2064
		trap "_cleanup_output_dir '$output_dir'; chmod -R u+rwx '$run_dir' 2>/dev/null; rm -rf '$run_dir'" EXIT INT TERM
	fi

	local ref fetch_out fetch_rc=0
	local -a fetched_refs=()
	for ref in "${evidence_refs[@]}"; do
		fetch_out=$("$PYTHON_BIN" "$BROKER" get --snapshot-dir "$snapshot_dir" --event-id "$event_id" --evidence-ref "$ref" --dest "$input_dir" 2>&1)
		fetch_rc=$?
		if [ "$fetch_rc" -ne 0 ]; then
			"$PYTHON_BIN" "$REPORT_PY" --event-id "$event_id" --out "$report_out" --tmpfs-used false \
				--sandbox-backend "none" --fail-reason "evidence fetch rejected for '$ref': $fetch_out" >/dev/null
			return 1
		fi
		fetched_refs+=("$ref")
	done
	chmod 500 "$input_dir" 2>/dev/null || true

	_prepare_output_dir "$output_dir"

	_build_sandbox_argv "$input_dir" "$output_dir" "$script_abs" "$profile_file"

	local tbin
	tbin="$(_timeout_bin)"
	local -a run_cmd=()
	if [ -n "$tbin" ]; then
		run_cmd+=("$tbin" "${time_limit}s")
	fi
	run_cmd+=("${SANDBOX_ARGV[@]}")
	run_cmd+=(env -i "PATH=/usr/bin:/bin" "PYTHONDONTWRITEBYTECODE=1" \
		"DIAG_INPUT_DIR=$input_dir" "DIAG_OUTPUT_DIR=$output_dir" "DIAG_EVENT_ID=$event_id" \
		"$PYTHON_BIN" -B "$script_abs")

	local max_output_blocks=$(((max_output_bytes + 511) / 512))
	[ "$max_output_blocks" -lt 1 ] && max_output_blocks=1

	(
		ulimit -f "$max_output_blocks" 2>/dev/null || true
		exec "${run_cmd[@]}"
	) >"$stdout_file" 2>"$stderr_file"
	local rc=$?

	local joined_refs
	joined_refs="$(IFS=,; echo "${fetched_refs[*]}")"

	local final_rc
	if [ "$rc" -eq 0 ]; then
		"$PYTHON_BIN" "$REPORT_PY" --event-id "$event_id" --out "$report_out" \
			--tmpfs-used "$TMPFS_USED" --sandbox-backend "$SANDBOX_BACKEND" \
			--allowed-refs "$joined_refs" --stdout-file "$stdout_file" >/dev/null
		final_rc=$?
	else
		local reason
		if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
			reason="diagnostic script timed out after ${time_limit}s"
		else
			# stderr is redacted before it ever leaves the ephemeral run_dir,
			# and only a bounded tail is kept, to avoid the failure report
			# itself becoming a leak vector.
			local stderr_tail
			stderr_tail=$(tail -c 2000 "$stderr_file" 2>/dev/null | "$PYTHON_BIN" "$REPO_ROOT/lib/redacted_diag_redact.py" 2>/dev/null)
			reason="diagnostic script exited with code $rc: ${stderr_tail:-<no stderr>}"
		fi
		"$PYTHON_BIN" "$REPORT_PY" --event-id "$event_id" --out "$report_out" \
			--tmpfs-used "$TMPFS_USED" --sandbox-backend "$SANDBOX_BACKEND" \
			--fail-reason "$reason" >/dev/null
		final_rc=1
	fi

	cat "$report_out"
	return "$final_rc"
}

case "${1:-}" in
run)
	shift
	_run "$@"
	exit $?
	;;
*)
	_usage
	exit 2
	;;
esac
