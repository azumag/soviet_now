#!/bin/bash
# core/strategy_runtime.sh - strategy.py の実行スナップショットと原子的反映

_strategy_runtime_lock_path() {
	printf '%s\n' "${STRATEGY_APPLY_LOCK_FILE:-${TMP_STATE_DIR:-tmp/state}/strategy_apply.lock}"
}

_strategy_runtime_apply_failure_path() {
	printf '%s\n' "${STRATEGY_APPLY_FAILURE_FILE:-${TMP_STATE_DIR:-tmp/state}/strategy_apply_last_failure.json}"
}

_strategy_runtime_record_apply_failure() {
	local failure_code="${1:-apply_bundle_failed}" path now
	path=$(_strategy_runtime_apply_failure_path)
	now=$(date +%s)
	mkdir -p "$(dirname "$path")" 2>/dev/null || true
	python3 - "$path" "$failure_code" "$now" <<'PY' >/dev/null 2>&1 || true
import json
import os
import sys
import tempfile

path, code, failed_at = sys.argv[1:4]
if code not in ("apply_bundle_failed", "projection_guard_refused"):
    code = "apply_bundle_failed"
payload = {
    "schema_version": 1,
    "failure_code": code,
    "failed_at": int(failed_at),
}
parent = os.path.dirname(path) or "."
os.makedirs(parent, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".strategy_apply_failure.", dir=parent)
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

# --- projection live-apply gate (docich#2487 / soviet_now#707) ---------------
# The production runtime (/home/ubuntu/soren) is a git-less projection of the
# reviewed games/soviet_now gitlink. The docich deploy plane fail-closes
# (vm-operations.yml "Reconcile reviewed pre-synced Soren checkout", lineage
# rules from docich#279) unless every tracked path's live bytes+mode match a
# reviewed commit across the gitlink transition. Runtime writers of
# strategy.py / strategy_helpers (improve adopt, AB gate adopt, repair, revert)
# must therefore never place unreviewed bytes on live paths. This gate is the
# single choke point shared by all structured writers and is policy driven so
# the rollout can be staged:
#
#   IMPROVE_LIVE_APPLY_POLICY = dry_run (default) | enforce_reviewed | off
#     dry_run          : log + durable marker only; the apply proceeds. Use
#                        this to observe what would be refused before flipping
#                        the production runtime to enforcement.
#     enforce_reviewed : refuse applies whose candidate bytes are not already
#                        part of the reviewed lineage; the candidate is
#                        quarantined under tmp/state/projection_guard/ so no
#                        work is lost and the persist/PR path (or the runbook)
#                        can upstream it later.
#     off              : gate disabled (escape hatch).
#
# SOREN_PROJECTION_FORCE=1|0 overrides projection auto-detection (auto = the
# runtime root has no .git directory, cf. strategy/persist.sh).
# SOREN_LINEAGE_REF_REPO overrides the git clone used to resolve reviewed blobs
# (default: SOREN_PERSIST_REPO, then /home/ubuntu/soren-persist).
# SOREN_LINEAGE_REF overrides the preferred reviewed ref (default origin/main,
# then main). The gate never fetches; a stale clone only over-refuses until the
# next persist run refreshes origin/main.
#
# Return codes of strategy_runtime_projection_apply_gate:
#   0 = apply may proceed (allowed / gate inactive / dry_run)
#   1 = gate could not evaluate safely (callers fail closed)
#   3 = refused under enforce_reviewed (candidate quarantined)

_STRATEGY_RUNTIME_GUARD_REFUSED=3

_strategy_runtime_log() {
	if declare -F log >/dev/null 2>&1; then
		log "$*"
	else
		printf '%s\n' "PROJECTION-GUARD: $*" >&2
	fi
}

_strategy_runtime_projection_root() {
	printf '%s\n' "${SOREN_PROJECTION_ROOT:-$PWD}"
}

_strategy_runtime_is_projection() {
	case "${SOREN_PROJECTION_FORCE:-auto}" in
	1 | true | yes) return 0 ;;
	0 | false | no) return 1 ;;
	esac
	# Projection checkouts carry no .git directory (strategy/persist.sh).
	[ ! -e "$(_strategy_runtime_projection_root)/.git" ]
}

_strategy_runtime_live_apply_policy() {
	case "${IMPROVE_LIVE_APPLY_POLICY:-dry_run}" in
	enforce_reviewed) printf '%s\n' enforce_reviewed ;;
	off) printf '%s\n' off ;;
	*) printf '%s\n' dry_run ;;
	esac
}

_strategy_runtime_lineage_repo() {
	local repo="${SOREN_LINEAGE_REF_REPO:-${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}}"
	[ -n "$repo" ] || return 1
	command -v git >/dev/null 2>&1 || return 1
	git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || return 1
	printf '%s\n' "$repo"
}

_strategy_runtime_rel_to_projection_root() {
	local root
	root=$(_strategy_runtime_projection_root) || return 1
	python3 - "$1" "$root" <<'PY' 2>/dev/null
import os
import sys

# realpath both sides: PWD and a relative target can resolve through
# different symlink prefixes (e.g. /tmp vs /private/tmp), and relpath would
# then wrongly report the live path as outside the projection root.
target = os.path.realpath(
    sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(os.getcwd(), sys.argv[1])
)
root = os.path.realpath(
    sys.argv[2] if os.path.isabs(sys.argv[2]) else os.path.join(os.getcwd(), sys.argv[2])
)
rel = os.path.relpath(target, root)
if rel.startswith("..") or os.path.isabs(rel):
    raise SystemExit(1)
print(rel)
PY
}

_strategy_runtime_lineage_verdict() {
	# Compare a candidate against the reviewed lineage in <repo>.
	#   argv: <repo> <candidate_file> <rel_path> [helpers_dir] [helpers_prefix]
	# Prints exactly one verdict line:
	#   reviewed            — bytes (and helpers tree) match the lineage ref;
	#                         the candidate file mode is normalized to the
	#                         reviewed mode so the applied live file passes the
	#                         deploy plane's sha256+mode comparison (docich#2482)
	#   mismatch:<reason>   — candidate is not part of the reviewed lineage
	#   unavailable         — no reviewed ref could be resolved
	python3 - "$@" <<'PY'
import hashlib
import os
import stat
import subprocess
import sys

repo, cand_file, rel_path = sys.argv[1], sys.argv[2], sys.argv[3]
helpers_dir = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else ""
helpers_prefix = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else "strategy_helpers"
refs = [r for r in (os.environ.get("SOREN_LINEAGE_REF", "origin/main"), "main") if r]


def git(*args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=False,
        timeout=30,
        check=False,
    )


ref = None
for candidate_ref in refs:
    done = git("rev-parse", "--verify", "--quiet", candidate_ref + "^{commit}")
    if done.returncode == 0 and done.stdout.strip():
        ref = done.stdout.strip().decode("ascii", "replace")
        break
if ref is None:
    print("unavailable")
    raise SystemExit(0)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reviewed_blob(rel):
    cat = git("cat-file", "blob", f"{ref}:{rel}")
    if cat.returncode != 0:
        return None, None
    ls = git("ls-tree", ref, "--", rel)
    mode = None
    for line in ls.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t", 1)[0].split()
        if len(parts) >= 3 and parts[1] == "blob":
            mode = int(parts[0], 8)
            break
    return cat.stdout, mode


with open(cand_file, "rb") as handle:
    cand_bytes = handle.read()
blob, mode = reviewed_blob(rel_path)
if blob is None or blob != cand_bytes:
    print("mismatch:strategy bytes differ from the reviewed lineage")
    raise SystemExit(0)

if helpers_dir:
    ls = git("ls-tree", "-r", ref, "--", helpers_prefix + "/")
    reviewed = {}
    for line in ls.stdout.decode("utf-8", "replace").splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            reviewed[path] = (parts[2], int(parts[0], 8))
    if not reviewed:
        print("mismatch:helpers tree missing from the reviewed lineage")
        raise SystemExit(0)
    seen = {}
    for root_dir, _dirs, files in os.walk(helpers_dir):
        for name in files:
            full = os.path.join(root_dir, name)
            rel = os.path.relpath(full, helpers_dir).replace(os.sep, "/")
            with open(full, "rb") as handle:
                seen[f"{helpers_prefix}/{rel}"] = sha256_bytes(handle.read())
    for key in sorted(seen):
        if key not in reviewed:
            print(f"mismatch:helpers file not in the reviewed lineage: {key}")
            raise SystemExit(0)
        cat = git("cat-file", "blob", f"{ref}:{key}")
        if cat.returncode != 0 or sha256_bytes(cat.stdout) != seen[key]:
            print(f"mismatch:helpers bytes differ from the reviewed lineage: {key}")
            raise SystemExit(0)
    # Entries missing from the candidate would be dropped by the bundle apply,
    # which drifts live helpers away from the gitlink just as extra bytes do.
    for key in sorted(reviewed):
        if key not in seen:
            print(f"mismatch:reviewed helpers file missing from candidate: {key}")
            raise SystemExit(0)
# Report the reviewed permission bits so the apply writes live bytes with
# exactly the mode the deploy plane compares (git stores 100644/100755; the
# leading "100" is the object type, so mask to the permission bits).
print(f"reviewed {(mode & 0o7777):o}" if mode is not None else "reviewed")
PY
}

_strategy_runtime_record_guard_event() {
	# Durable, fixed-schema guard event marker (separate from the apply failure
	# marker so existing consumers keep their semantics).
	local event="$1" policy="$2" reason="${3:-}" quarantine="${4:-}"
	local path="${SOREN_GUARD_EVENT_FILE:-${TMP_STATE_DIR:-tmp/state}/projection_guard_last_event.json}"
	mkdir -p "$(dirname "$path")" 2>/dev/null || true
	SOREN_GUARD_EVENT_PATH="$path" python3 - "$event" "$policy" "$reason" "$quarantine" <<'PY' >/dev/null 2>&1 || true
import json
import os
import sys
import tempfile
import time

path = os.environ["SOREN_GUARD_EVENT_PATH"]
event, policy, reason, quarantine = sys.argv[1:5]
payload = {
    "schema_version": 1,
    "event": event,
    "policy": policy,
    "reason": reason,
    "quarantine_dir": quarantine,
    "recorded_at": int(time.time()),
}
parent = os.path.dirname(path) or "."
os.makedirs(parent, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".projection_guard_event.", dir=parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
}

_strategy_runtime_quarantine_candidate() {
	# <source_file> [helpers_source] — preserve the refused candidate so the
	# persist/PR path can upstream it later. Echoes the quarantine directory.
	local source_file="$1" helpers_source="${2:-}"
	local state_dir quarantine_dir
	state_dir="${TMP_STATE_DIR:-tmp/state}"
	quarantine_dir="${state_dir}/projection_guard/$(date -u +%Y%m%dT%H%M%SZ)_$$"
	mkdir -p "$quarantine_dir" 2>/dev/null || return 1
	cp -p "$source_file" "$quarantine_dir/strategy.py" 2>/dev/null || return 1
	if [ -n "$helpers_source" ] && [ -d "$helpers_source" ]; then
		cp -RL "$helpers_source" "$quarantine_dir/strategy_helpers" 2>/dev/null || true
	fi
	printf '%s\n' "$quarantine_dir"
}

strategy_runtime_projection_apply_gate() {
	# Public gate: <source_file> <target_file> [helpers_source] [tag]
	#   0 = apply may proceed, 1 = gate error (fail closed), 3 = refused.
	local source_file="${1:-}" target_file="${2:-}" helpers_source="${3:-}" tag="${4:-apply}"
	local policy repo rel verdict quarantine_dir reviewed_mode

	# Never let a previous gated apply leak its reviewed mode into this one.
	_STRATEGY_RUNTIME_TARGET_MODE=""

	policy=$(_strategy_runtime_live_apply_policy)
	[ "$policy" = "off" ] && return 0
	_strategy_runtime_is_projection || return 0
	{ [ -n "$source_file" ] && [ -f "$source_file" ]; } || return 0
	[ -n "$target_file" ] || return 0

	repo=$(_strategy_runtime_lineage_repo) || {
		# Without a lineage clone the gate cannot prove anything; degrade to
		# dry_run instead of freezing the whole improvement loop.
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "lineage repository unavailable" ""
		_strategy_runtime_log "[${tag}] lineage repository unavailable → dry_run degrade (apply proceeds)"
		return 0
	}

	rel=$(_strategy_runtime_rel_to_projection_root "$target_file") || {
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "target outside projection root" ""
		_strategy_runtime_log "[${tag}] target outside projection root → dry_run degrade (apply proceeds)"
		return 0
	}

	verdict=$(_strategy_runtime_lineage_verdict "$repo" "$source_file" "$rel" "$helpers_source" "strategy_helpers")
	case "$verdict" in
	reviewed)
		return 0
		;;
	reviewed\ *)
		# "reviewed <mode>": apply the reviewed permission bits to the live file.
		reviewed_mode="${verdict#reviewed }"
		case "$reviewed_mode" in
		'' | *[!0-7]*) reviewed_mode="" ;;
		esac
		_STRATEGY_RUNTIME_TARGET_MODE="$reviewed_mode"
		return 0
		;;
	unavailable)
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "reviewed ref unavailable" ""
		_strategy_runtime_log "[${tag}] reviewed ref unavailable → dry_run degrade (apply proceeds)"
		return 0
		;;
	mismatch:*)
		if [ "$policy" = "enforce_reviewed" ]; then
			quarantine_dir=$(_strategy_runtime_quarantine_candidate "$source_file" "$helpers_source") || quarantine_dir=""
			_strategy_runtime_record_apply_failure "projection_guard_refused"
			_strategy_runtime_record_guard_event "refused" "$policy" "$verdict" "$quarantine_dir"
			_strategy_runtime_log "[${tag}] refused unreviewed live apply (${verdict}); candidate preserved at ${quarantine_dir:-<quarantine failed>}"
			return "$_STRATEGY_RUNTIME_GUARD_REFUSED"
		fi
		_strategy_runtime_record_guard_event "would_refuse" "$policy" "$verdict" ""
		_strategy_runtime_log "[${tag}] dry_run — unreviewed live apply would be refused (${verdict})"
		return 0
		;;
	*)
		_strategy_runtime_record_guard_event "gate_degraded" "$policy" "unexpected verdict: ${verdict}" ""
		_strategy_runtime_log "[${tag}] unexpected guard verdict → dry_run degrade (apply proceeds)"
		return 0
		;;
	esac
}

_strategy_runtime_copy_mode() {
	# Carry the intended permission bits onto <dst>. mktemp creates apply staging
	# files with mode 600; without this, every atomic apply landed live with mode
	# 600 while the reviewed mode is 100755 — the mode drift docich#2482 had to
	# repair by hand. When the projection gate resolved a reviewed mode for this
	# apply it wins (the candidate file itself is never mutated); otherwise the
	# source's own mode is carried.
	local mode="${_STRATEGY_RUNTIME_TARGET_MODE:-}"
	case "$mode" in
	'' | *[!0-7]*) mode="" ;;
	esac
	if [ -n "$mode" ]; then
		chmod "$mode" "$2" 2>/dev/null || true
		return 0
	fi
	python3 - "$1" "$2" <<'PY' 2>/dev/null
import os
import stat
import sys

os.chmod(sys.argv[2], stat.S_IMODE(os.stat(sys.argv[1]).st_mode))
PY
}

_strategy_runtime_atomic_copy_unlocked() {
	local source_file="$1" target_file="$2"
	local target_dir target_base temp_file
	[ -f "$source_file" ] || return 1
	target_dir=$(dirname "$target_file")
	target_base=$(basename "$target_file")
	mkdir -p "$target_dir" || return 1
	temp_file=$(mktemp "$target_dir/.${target_base}.apply.XXXXXX") || return 1
	if ! cp "$source_file" "$temp_file"; then
		rm -f "$temp_file"
		return 1
	fi
	_strategy_runtime_copy_mode "$source_file" "$temp_file" || true
	if ! mv -f "$temp_file" "$target_file"; then
		rm -f "$temp_file"
		return 1
	fi
}

strategy_runtime_atomic_apply_then() {
	local source_file="$1" target_file="${2:-$STRATEGY_FILE}" callback="${3:-}"
	local lock_file
	# docich#2487: projection gate runs before the lock; a refusal (rc=3) skips
	# the copy entirely so unreviewed bytes never reach the live path.
	strategy_runtime_projection_apply_gate "$source_file" "$target_file" "" "atomic_apply" || return $?
	shift 3
	lock_file=$(_strategy_runtime_lock_path)
	mkdir -p "$(dirname "$lock_file")" || return 1
	if command -v flock >/dev/null 2>&1; then
		(
			flock -x 9 || exit 1
			_strategy_runtime_atomic_copy_unlocked "$source_file" "$target_file" || exit 1
			[ -z "$callback" ] || "$callback" "$@"
		) 9>"$lock_file"
	else
		_strategy_runtime_atomic_copy_unlocked "$source_file" "$target_file" || return 1
		[ -z "$callback" ] || "$callback" "$@"
	fi
}

strategy_runtime_atomic_apply() {
	strategy_runtime_atomic_apply_then "$1" "${2:-$STRATEGY_FILE}" ""
}

_strategy_runtime_apply_bundle_unlocked() {
	local source_file="$1" target_file="$2" helpers_source="$3" helpers_target="$4"
	local target_dir target_base strategy_temp
	local helpers_parent helpers_base helpers_stage="" helpers_previous=""

	[ -f "$source_file" ] || return 1
	target_dir=$(dirname "$target_file")
	target_base=$(basename "$target_file")
	mkdir -p "$target_dir" || return 1
	strategy_temp=$(mktemp "$target_dir/.${target_base}.apply.XXXXXX") || return 1
	if ! cp "$source_file" "$strategy_temp"; then
		rm -f "$strategy_temp"
		return 1
	fi
	_strategy_runtime_copy_mode "$source_file" "$strategy_temp" || true

	if [ -d "$helpers_source" ]; then
		helpers_parent=$(dirname "$helpers_target")
		helpers_base=$(basename "$helpers_target")
		mkdir -p "$helpers_parent" || {
			rm -f "$strategy_temp"
			return 1
		}
		helpers_stage=$(mktemp -d "$helpers_parent/.${helpers_base}.apply.XXXXXX") || {
			rm -f "$strategy_temp"
			return 1
		}
		if ! cp -RL "$helpers_source"/. "$helpers_stage"/; then
			rm -rf "$helpers_stage"
			rm -f "$strategy_temp"
			return 1
		fi
		[ -f "$helpers_stage/__init__.py" ] || : >"$helpers_stage/__init__.py"

		if [ -e "$helpers_target" ] || [ -L "$helpers_target" ]; then
			helpers_previous=$(mktemp -d "$helpers_parent/.${helpers_base}.previous.XXXXXX") || {
				rm -rf "$helpers_stage"
				rm -f "$strategy_temp"
				return 1
			}
			rmdir "$helpers_previous" || return 1
			if ! mv "$helpers_target" "$helpers_previous"; then
				rm -rf "$helpers_stage"
				rm -f "$strategy_temp"
				return 1
			fi
		fi
		if ! mv "$helpers_stage" "$helpers_target"; then
			[ -n "$helpers_previous" ] && mv "$helpers_previous" "$helpers_target" 2>/dev/null || true
			rm -rf "$helpers_stage"
			rm -f "$strategy_temp"
			return 1
		fi
	fi

	if ! mv -f "$strategy_temp" "$target_file"; then
		if [ -n "$helpers_stage" ]; then
			rm -rf "$helpers_target"
			[ -n "$helpers_previous" ] && mv "$helpers_previous" "$helpers_target" 2>/dev/null || true
		fi
		rm -f "$strategy_temp"
		return 1
	fi
	[ -n "$helpers_previous" ] && rm -rf "$helpers_previous"
}

_strategy_runtime_atomic_apply_bundle_then_once() {
	local source_file="$1" target_file="${2:-$STRATEGY_FILE}"
	local helpers_source="${3:-}" helpers_target="${4:-strategy_helpers}" callback="${5:-}"
	local lock_file
	shift 5
	lock_file=$(_strategy_runtime_lock_path)
	mkdir -p "$(dirname "$lock_file")" || return 1
	if command -v flock >/dev/null 2>&1; then
		(
			flock -x 9 || exit 1
			_strategy_runtime_apply_bundle_unlocked "$source_file" "$target_file" "$helpers_source" "$helpers_target" || exit 1
			[ -z "$callback" ] || "$callback" "$@"
		) 9>"$lock_file"
	else
		_strategy_runtime_apply_bundle_unlocked "$source_file" "$target_file" "$helpers_source" "$helpers_target" || return 1
		[ -z "$callback" ] || "$callback" "$@"
	fi
}

strategy_runtime_atomic_apply_bundle_then() {
	# The improvement callback used by Soren is explicitly idempotent. A transient
	# lock/copy/rename failure should therefore not discard an already validated
	# candidate. Retry the exact same reviewed bundle once, then fail closed and
	# leave a fixed diagnostic marker if both attempts fail.
	local retry_delay="${STRATEGY_APPLY_RETRY_DELAY_SEC:-1}"
	case "$retry_delay" in '' | *[!0-9]*) retry_delay=1 ;; esac
	[ "$retry_delay" -gt 5 ] && retry_delay=5

	# docich#2487: projection gate runs once, before any retry. A refusal (rc=3)
	# is final — the candidate is quarantined and the live bytes stay reviewed.
	strategy_runtime_projection_apply_gate "$1" "${2:-$STRATEGY_FILE}" "${3:-}" "bundle_apply" || return $?

	if _strategy_runtime_atomic_apply_bundle_then_once "$@"; then
		return 0
	fi
	[ "$retry_delay" -le 0 ] || sleep "$retry_delay"
	if _strategy_runtime_atomic_apply_bundle_then_once "$@"; then
		return 0
	fi
	_strategy_runtime_record_apply_failure "apply_bundle_failed"
	return 1
}

strategy_runtime_atomic_apply_bundle() {
	strategy_runtime_atomic_apply_bundle_then "$1" "${2:-$STRATEGY_FILE}" "${3:-}" "${4:-strategy_helpers}" ""
}

_strategy_runtime_snapshot_unlocked() {
	local source_file="$1" runtime_dir="$2" record_file="$3" helpers_source="$4" before_snapshot="${5:-}"
	case "$runtime_dir" in
	'' | / | .) return 1 ;;
	esac
	[ -z "$before_snapshot" ] || "$before_snapshot" || return 1
	rm -rf "$runtime_dir"
	mkdir -p "$runtime_dir" || return 1
	cp "$source_file" "$runtime_dir/strategy.py" || {
		rm -rf "$runtime_dir"
		return 1
	}
	if [ -d "$helpers_source" ]; then
		cp -RL "$helpers_source" "$runtime_dir/strategy_helpers" || {
			rm -rf "$runtime_dir"
			return 1
		}
	else
		mkdir -p "$runtime_dir/strategy_helpers" || return 1
		: >"$runtime_dir/strategy_helpers/__init__.py"
	fi
	_strategy_runtime_atomic_copy_unlocked "$runtime_dir/strategy.py" "$record_file" || {
		rm -rf "$runtime_dir"
		return 1
	}
}

strategy_runtime_create_game_snapshot() {
	local source_file="$1" runtime_dir="$2"
	local record_file="${3:-${source_file}.game_snapshot}" helpers_source="${4:-strategy_helpers}" before_snapshot="${5:-}"
	local lock_file
	lock_file=$(_strategy_runtime_lock_path)
	mkdir -p "$(dirname "$lock_file")" || return 1
	if command -v flock >/dev/null 2>&1; then
		(
			flock -x 9 || exit 1
			_strategy_runtime_snapshot_unlocked "$source_file" "$runtime_dir" "$record_file" "$helpers_source" "$before_snapshot"
		) 9>"$lock_file"
	else
		_strategy_runtime_snapshot_unlocked "$source_file" "$runtime_dir" "$record_file" "$helpers_source" "$before_snapshot"
	fi
}
