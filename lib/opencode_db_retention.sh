# lib/opencode_db_retention.sh - opencode セッションDBの writer exclusion gate と bounded retention
#
# 背景: opencode のセッションDB (`opencode.db`) は実行ごとに単調増加し、opencode 自身に
# 保持期間の設定が無い。単発の `pgrep` 観測で削除すると、mutation 中に新規 `opencode run`
# が開始して競合する (docich #404 で revert された実装の欠陥)。
#
# 契約:
#   - producer は `_opencode_rotation_gate_run` 経由で `opencode run` を実行し、
#     実行中だけ **共有ロック (flock -s)** を保持する。
#   - retention は `_opencode_db_retention_rotate` で **排他ロック (flock -x)** を取り、
#     実行中 writer の drain を待ってから単一トランザクションで削除し VACUUM する。
#   - 排他ロックが取得できない場合は **何も変更せず** スキップする (fail-closed)。
#
# 設計の詳細は docich `docs/adr/0002-opencode-db-retention.md` を参照。

if ! command -v log >/dev/null 2>&1; then
	log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fi

_opencode_rotation_gate_path() {
	if [ -n "${OPENCODE_ROTATION_GATE:-}" ]; then
		printf '%s\n' "$OPENCODE_ROTATION_GATE"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/.opencode_rotation_gate.lock\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/.opencode_rotation_gate.lock\n'
	fi
}

# _opencode_rotation_gate_run CMD [ARGS...]
# CMD の実行中だけ共有ロックを保持する。取得できない場合は fail-closed で
# 124 を返し、CMD を実行しない。
_opencode_rotation_gate_run() {
	case "${OPENCODE_ROTATION_GATE_ENABLED:-1}" in
	1) ;;
	*) "$@"; return $? ;;
	esac
	if ! command -v flock >/dev/null 2>&1; then
		log "[OPENCODE:gate] flock unavailable; running ungated" >&2
		"$@"
		return $?
	fi
	local gate wait_sec fd rc
	gate="$(_opencode_rotation_gate_path)"
	wait_sec="${OPENCODE_ROTATION_GATE_WAIT_SEC:-120}"
	case "$wait_sec" in '' | *[!0-9]*) wait_sec=120 ;; esac
	[ "$wait_sec" -lt 1 ] && wait_sec=1
	mkdir -p "$(dirname "$gate")" 2>/dev/null || true
	# NB: do not attach 2>/dev/null to `exec` with only redirections; it would
	# permanently redirect this shell's stderr to /dev/null.
	if ! : >>"$gate" 2>/dev/null; then
		log "[OPENCODE:gate] cannot open gate; running ungated" >&2
		"$@"
		return $?
	fi
	exec {fd}>>"$gate"
	if ! flock -s -w "$wait_sec" "$fd"; then
		log "[OPENCODE:gate] rotation in progress >${wait_sec}s; aborting run" >&2
		exec {fd}>&-
		return 124
	fi
	"$@"
	rc=$?
	exec {fd}>&-
	return "$rc"
}

# Returns 0 only for completed/explicitly disabled work; 75 means deferred.
_opencode_db_retention_rotate() {
    local days="$1"
    shift
    case "$days" in '' | *[!0-9]*) days=3 ;; esac
    [ "$days" -lt 1 ] && days=1
    local libdir gate wait_sec fd db default_db result state_dir rc=0 one_rc label
    libdir="${ELOOP_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
    state_dir="${OPENCODE_RETENTION_STATE_DIR:-$libdir/tmp/state}"
    result="$state_dir/opencode_db_retention.json"
    command -v python3 >/dev/null 2>&1 || return 1
    _opencode_retention_record() {
        python3 "$libdir/lib/opencode_db_retention.py" --result-file "$result" --record "$1"
    }
    _opencode_retention_record running || return 1
    if ! command -v flock >/dev/null 2>&1; then
        _opencode_retention_record failed
        return 1
    fi
    gate="$(_opencode_rotation_gate_path)"
    wait_sec="${OPENCODE_ROTATION_GATE_WAIT_SEC:-120}"
    case "$wait_sec" in '' | *[!0-9]*) wait_sec=120 ;; esac
    [ "$wait_sec" -lt 1 ] && wait_sec=1
    [ "$wait_sec" -gt 600 ] && wait_sec=600
    mkdir -p "$(dirname "$gate")" || return 1
    if ! : >>"$gate"; then
        _opencode_retention_record failed
        return 1
    fi
    exec {fd}>>"$gate"
    if ! flock -x -w "$wait_sec" "$fd"; then
        log "[OPENCODE:retention] writers active after ${wait_sec}s; skip rotation" >&2
        exec {fd}>&-
        _opencode_retention_record gate_timeout
        return 75
    fi
    default_db="${HOME:-/home/ubuntu}/.local/share/opencode/opencode.db"
    for db in "$@"; do
        [ -f "$db" ] || continue
        label=worker
        [ "$db" = "$default_db" ] && label=default
        if [ "$label" = default ] && [ "${OPENCODE_DEFAULT_DB_RETENTION_ENABLED:-1}" != 1 ]; then
            log "[OPENCODE:retention] default DB retention disabled; skip rotation" >&2
            python3 "$libdir/lib/opencode_db_retention.py" --result-file "$state_dir/opencode_retention_default.json" --record disabled || rc=1
            continue
        fi
        if python3 "$libdir/lib/opencode_db_retention.py" "$db" "$days" --result-file "$state_dir/opencode_retention_${label}.json"; then
            log "[OPENCODE:retention] completed database=$label"
        else
            one_rc=$?
            [ "$rc" -eq 0 ] && rc="$one_rc"
            [ "$one_rc" -ne 75 ] && rc=1
            log "[OPENCODE:retention] incomplete database=$label code=$one_rc" >&2
        fi
    done
    exec {fd}>&-
    case "$rc" in
        0) _opencode_retention_record completed || rc=1 ;;
        75) _opencode_retention_record deferred || rc=1 ;;
        *) _opencode_retention_record failed || rc=1 ;;
    esac
    return "$rc"
}
