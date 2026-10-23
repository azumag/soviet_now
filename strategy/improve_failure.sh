#!/bin/bash
# strategy/improve_failure.sh - durable, fixed-enum improvement failure history
#
# improve_state.json is a live status file and is intentionally overwritten by
# later runs/recovery. Keep the most recent terminal failed_no_apply class in a
# separate atomic file so an incident remains diagnosable after the next run.

_improve_failure_history_path() {
	printf '%s\n' "${IMPROVE_LAST_FAILURE_FILE:-${TMP_STATE_DIR:-tmp/state}/improve_last_failure.json}"
}

_improve_failure_normalize_code() {
	local raw="${1:-other}"
	case "$raw" in
	apply_bundle_failed|projection_guard_refused|validation_failed|model_no_response|deadline_exhausted|isolated_runner_unavailable|analysis_evidence_invalid|wall_timeout|rate_limited|analysis_failed|analysis_contract_invalid|analysis_hold|process_exited_without_apply|manual_no_change|strategy_persist_failed)
		printf '%s\n' "$raw"
		;;
	*) printf '%s\n' other ;;
	esac
}

_improve_failure_persist_reason() {
	local raw="${1:-unknown}"
	case "$raw" in
	clone_missing|invalid_message|invalid_branch|gh_missing|identity_setup_failed|fetch_failed|remote_head_failed|checkout_failed|reset_failed|copy_failed|add_failed|commit_failed|tree_failed|push_failed|pr_failed|unknown)
		printf '%s\n' "$raw"
		;;
	*) printf '%s\n' unknown ;;
	esac
}

_strategy_persist_failure_path() {
	printf '%s\n' "${STRATEGY_PERSIST_FAILURE_FILE:-${TMP_STATE_DIR:-tmp/state}/strategy_persist_last_failure.json}"
}

# strategy/persist.sh から呼ばれる durable record。採用 (apply) 自体は成功して
# いても GitHub への永続化が失敗した状態を、次の成功/no-change まで残す。
_record_strategy_persist_failure() {
	local reason="${1:-unknown}" rc="${2:-1}" label="${3:-unknown}" path now
	reason=$(_improve_failure_persist_reason "$reason")
	case "$label" in
	''|*[!A-Za-z0-9_-]*) label=unknown ;;
	esac
	case "$rc" in
	''|*[!0-9]*) rc=1 ;;
	esac
	path=$(_strategy_persist_failure_path)
	now=$(date +%s)
	# terminal failure history と同じく fsync + 0600 で durable にする。
	python3 - "$path" "$reason" "$rc" "$label" "$now" <<'PY' >/dev/null 2>&1 || true
import json
import os
import sys
import tempfile

path, reason, rc, label, failed_at = sys.argv[1:6]
payload = {
    "schema_version": 1,
    "status": "failed",
    "failure_code": "strategy_persist_failed",
    "reason": reason,
    "rc": int(rc),
    "label": label,
    "failed_at": int(failed_at),
}
parent = os.path.dirname(path) or "."
os.makedirs(parent, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".strategy_persist_last_failure.", dir=parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
	return 0
}

_clear_strategy_persist_failure() {
	local path
	path=$(_strategy_persist_failure_path)
	rm -f "$path" 2>/dev/null || true
	return 0
}

_improve_failure_recent_apply_code() {
	local started_at="${1:-0}" marker
	marker="${STRATEGY_APPLY_FAILURE_FILE:-${TMP_STATE_DIR:-tmp/state}/strategy_apply_last_failure.json}"
	python3 - "$marker" "$started_at" <<'PY' 2>/dev/null
import json
import os
import sys
import time

path, started_raw = sys.argv[1:3]
try:
    started = int(float(started_raw or 0))
except Exception:
    started = 0
try:
    data = json.load(open(path, encoding="utf-8"))
    failed_at = int(data.get("failed_at", 0) or 0)
    code = str(data.get("failure_code") or "")
except Exception:
    raise SystemExit(1)
if code not in ("apply_bundle_failed", "projection_guard_refused") or failed_at <= 0:
    raise SystemExit(1)
# A nonzero run start is authoritative. The age guard only covers legacy callers
# that did not carry started_at, and prevents an old apply incident from being
# attributed to a later validation failure.
if started > 0:
    if failed_at < started:
        raise SystemExit(1)
elif int(time.time()) - failed_at > 7200:
    raise SystemExit(1)
print(code)
PY
}

_improve_failure_code_from_state() {
	local phase="${1:-}" detail="${2:-}" raw=""
	case "$detail" in
	failed_no_apply:*) raw="${detail#failed_no_apply:}" ;;
	*)
		if [ "$phase" = "failed_no_apply" ]; then
			case "$detail" in
			manual_no_change) raw=manual_no_change ;;
			process_exited_without_apply|'') raw=process_exited_without_apply ;;
			*) raw=other ;;
			esac
		fi
		;;
	esac
	[ -n "$raw" ] || return 1
	_improve_failure_normalize_code "$raw"
}

_persist_improve_terminal_failure() {
	local raw_code="${1:-other}" code path now
	code=$(_improve_failure_normalize_code "$raw_code")
	path=$(_improve_failure_history_path)
	now=$(date +%s)
	mkdir -p "$(dirname "$path")" 2>/dev/null || true
	python3 - "$path" "$code" "$now" <<'PY' >/dev/null 2>&1 || true
import json
import os
import sys
import tempfile

path, code, failed_at = sys.argv[1:4]
payload = {
    "schema_version": 1,
    "status": "failed_no_apply",
    "failure_code": code,
    "failed_at": int(failed_at),
}
parent = os.path.dirname(path) or "."
os.makedirs(parent, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".improve_last_failure.", dir=parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
}

# strategy/improve.sh defines the authoritative writer. Wrap it after sourcing so
# live-state semantics remain unchanged while terminal failures gain durability.
if declare -F _write_improve_state >/dev/null 2>&1 && ! declare -F _write_improve_state_without_failure_history >/dev/null 2>&1; then
	eval "$(declare -f _write_improve_state | sed '1s/_write_improve_state/_write_improve_state_without_failure_history/')"

	_write_improve_state() {
		local status="$1" pid="$2" hash="$3"
		local phase="${4:-}" progress="${5:-0}" detail="${6:-}" started_at="${7:-0}" pid_birth_epoch="${8:-0}"
		local improve_reason="${9:-}" apply_code="" failure_code="" rc

		# eloop_improve historically labels a repeated atomic-apply failure as
		# validation_failed because the failure code is not set at that call site.
		# The runtime layer now writes a fixed marker only after BOTH bounded apply
		# attempts fail (or the projection gate refuses the apply, docich#2487),
		# so safely correct that one ambiguous terminal detail here.
		if [ "$detail" = "failed_no_apply:validation_failed" ]; then
			apply_code=$(_improve_failure_recent_apply_code "$started_at" 2>/dev/null || true)
			if [ -n "$apply_code" ]; then
				detail="failed_no_apply:${apply_code}"
			fi
		fi

		_write_improve_state_without_failure_history \
			"$status" "$pid" "$hash" "$phase" "$progress" "$detail" \
			"$started_at" "$pid_birth_epoch" "$improve_reason"
		rc=$?
		[ "$rc" -eq 0 ] || return "$rc"

		failure_code=$(_improve_failure_code_from_state "$phase" "$detail" 2>/dev/null || true)
		if [ -n "$failure_code" ]; then
			_persist_improve_terminal_failure "$failure_code"
		fi
		return 0
	}
fi
