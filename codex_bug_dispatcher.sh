#!/bin/bash
# codex_bug_dispatcher.sh - dispatch stream bug reports from viewer comments to Codex.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && . ./.env && set +a
# shellcheck source=/dev/null
source ./eloop_lib.sh

mode="${1:-run}"
queue_dir="${CODEX_BUG_QUEUE_DIR:-tmp/codex_bug_queue}"
lock_dir="${CODEX_BUG_DISPATCH_LOCK_DIR:-tmp/state/.codex_bug_dispatch.lock}"
last_file="${CODEX_BUG_DISPATCH_LAST_FILE:-tmp/state/codex_bug_dispatch_last.ts}"
log_dir="${CODEX_BUG_DISPATCH_LOG_DIR:-tmp/debug/codex_bug_dispatch}"
min_interval="${CODEX_BUG_DISPATCH_MIN_INTERVAL_SEC:-900}"
codex_cmd="${CODEX_BUG_DISPATCH_CODEX_CMD:-codex}"
# Claude fallback (used when codex hits a rate limit)
claude_fallback_enabled="${CODEX_BUG_DISPATCH_CLAUDE_FALLBACK:-1}"
claude_cmd="${CODEX_BUG_DISPATCH_CLAUDE_CMD:-claude}"
claude_model="${CODEX_BUG_DISPATCH_CLAUDE_MODEL:-}"
claude_permission_mode="${CODEX_BUG_DISPATCH_CLAUDE_PERMISSION_MODE:-bypassPermissions}"
rate_limit_re="${CODEX_BUG_DISPATCH_RATE_LIMIT_REGEX:-rate.?limit|rate_limit|429|too many requests|usage limit|quota|resource_exhausted|overloaded|insufficient_quota}"

_pid_alive() {
	local pid="${1:-}" err=""
	case "$pid" in
	'' | *[!0-9]*) return 1 ;;
	esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	case "$err" in
	*"operation not permitted"* | *"Operation not permitted"*) return 0 ;;
	esac
	return 1
}

_lock_held() {
	[ -d "$lock_dir" ] || return 1
	local owner=""
	owner=$(cat "$lock_dir/pid" 2>/dev/null || true)
	_pid_alive "$owner"
}

_clear_lock_dir() {
	case "$lock_dir" in
	"" | "/" | ".") return 1 ;;
	esac
	[ -d "$lock_dir" ] || return 0
	rm -f "$lock_dir/pid" "$lock_dir/started_at" 2>/dev/null || true
	rmdir "$lock_dir" 2>/dev/null
}

_acquire_lock() {
	mkdir -p "$(dirname "$lock_dir")" "$queue_dir" "$log_dir"
	if mkdir "$lock_dir" 2>/dev/null; then
		printf '%s\n' "$$" >"$lock_dir/pid"
		printf '%s\n' "$(date +%s)" >"$lock_dir/started_at"
		return 0
	fi
	if _lock_held; then
		return 1
	fi
	_clear_lock_dir || return 1
	if mkdir "$lock_dir" 2>/dev/null; then
		printf '%s\n' "$$" >"$lock_dir/pid"
		printf '%s\n' "$(date +%s)" >"$lock_dir/started_at"
		return 0
	fi
	return 1
}

_release_lock() {
	local owner=""
	owner=$(cat "$lock_dir/pid" 2>/dev/null || true)
	[ "$owner" = "$$" ] && _clear_lock_dir >/dev/null 2>&1 || true
}

_kick() {
	[ "${CODEX_BUG_DISPATCH_ENABLED:-1}" = "1" ] || exit 0
	mkdir -p "$queue_dir" "$(dirname "$lock_dir")" "$log_dir"
	if _lock_held; then
		exit 0
	fi
	(
		"$0" run >>"$log_dir/kick.log" 2>&1
	) &
	disown $! 2>/dev/null || true
}

_oldest_report() {
	find "$queue_dir" -maxdepth 1 -type f -name '*.json' ! -name '.*' -print 2>/dev/null | sort | head -1
}

_interval_allows_dispatch() {
	case "$min_interval" in
	'' | *[!0-9]*) min_interval=900 ;;
	esac
	[ "$min_interval" -le 0 ] && return 0
	[ -f "$last_file" ] || return 0
	local now last
	now=$(date +%s)
	last=$(cat "$last_file" 2>/dev/null || echo 0)
	case "$last" in
	'' | *[!0-9]*) return 0 ;;
	esac
	[ $((now - last)) -ge "$min_interval" ]
}

_output_indicates_rate_limit() {
	local f
	for f in "$@"; do
		[ -f "$f" ] || continue
		if grep -Eiq "$rate_limit_re" "$f" 2>/dev/null; then
			return 0
		fi
	done
	return 1
}

_build_prompt() {
	local report_file="$1"
	python3 - "$report_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = {}

source = data.get("source") or "unknown"
user = data.get("user") or "unknown"
comment = data.get("comment") or ""
created_at = data.get("created_at") or ""
batch_hash = data.get("batch_hash") or ""
digest = data.get("hash") or ""

print(f"""視聴者コメントで配信システム系の不具合報告が検出されました。runtime evidence を確認して、必要なら修正してください。

【検出元】
- source: {source}
- user: {user}
- comment: {comment}
- created_at_unix: {created_at}
- batch_hash: {batch_hash}
- report_hash: {digest}

【必須手順】
1. 作業開始時に ./codex_work_indicator.sh start を実行してください。
2. 進捗または完了時に ./system_progress_report.sh "メリケンAI: ..." を使い、audio worker 経由で短く音声報告してください。
3. 最終応答、停止、または人間への引き渡し前に ./codex_work_indicator.sh stop を必ず実行してください。

【最初に読む evidence】
- data/codex_advice.md
- /tmp/soren_report.md
- ./show_status.sh --once
- 関連する tmp/state/*.json と logs/*.log

【範囲】
- OBS/eventOverlay、音声/TTS、コメント取得/返答、chat/audio/youtube worker、dashboard/status、監視/watchdog、分類器、Codex運用、フィードバック収集など配信システム系に限定してください。
- ゲーム戦略や strategy.py の改善には広げないでください。
- コメントだけで危険な変更をせず、runtime evidence で確認してください。
- 既存のユーザー変更を戻さないでください。
- live worker を勝手に stop/restart しないでください。必要なら reload/HUP を優先し、再起動が必要なら理由を明記してください。
""")
PY
}

_mark_report() {
	local report_file="$1" status="$2" rc="${3:-}"
	local dest_dir="$queue_dir/$status"
	mkdir -p "$dest_dir"
	local base
	base=$(basename "$report_file")
	if [ -n "$rc" ]; then
		python3 - "$report_file" "$status" "$rc" <<'PY' >/dev/null 2>&1 || true
import json, sys, time
path, status, rc = sys.argv[1:4]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
data["status"] = status
data["dispatch_finished_at"] = int(time.time())
try:
    data["dispatch_rc"] = int(rc)
except Exception:
    data["dispatch_rc"] = rc
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
PY
	fi
	mv "$report_file" "$dest_dir/$base" 2>/dev/null || rm -f "$report_file"
}

_run_once() {
	[ "${CODEX_BUG_DISPATCH_ENABLED:-1}" = "1" ] || exit 0
	local report_file=""
	report_file=$(_oldest_report)
	[ -n "$report_file" ] || exit 0
	if ! _interval_allows_dispatch; then
		exit 0
	fi
	_acquire_lock || exit 0
	trap _release_lock EXIT

	report_file=$(_oldest_report)
	[ -n "$report_file" ] || exit 0
	if ! _interval_allows_dispatch; then
		exit 0
	fi
	command -v "$codex_cmd" >/dev/null 2>&1 || {
		echo "[codex_bug_dispatcher] codex command not found: $codex_cmd" >&2
		exit 127
	}

	local ts prompt_file output_file log_file rc=0
	ts=$(date +%Y%m%d_%H%M%S)
	prompt_file="$log_dir/prompt_${ts}_$$.txt"
	output_file="$log_dir/last_${ts}_$$.txt"
	log_file="$log_dir/run_${ts}_$$.log"
	_build_prompt "$report_file" >"$prompt_file"
	echo "[codex_bug_dispatcher] dispatching $(basename "$report_file")" >>"$log_file"
	# Runs: codex exec -C /Users/azumag/azumag/work/soren "<prompt>"
	if [ -n "${CODEX_BUG_DISPATCH_MODEL:-}" ]; then
		"$codex_cmd" exec -C "$ELOOP_LIB_DIR" -m "$CODEX_BUG_DISPATCH_MODEL" -o "$output_file" "$(cat "$prompt_file")" >>"$log_file" 2>&1 || rc=$?
	else
		"$codex_cmd" exec -C "$ELOOP_LIB_DIR" -o "$output_file" "$(cat "$prompt_file")" >>"$log_file" 2>&1 || rc=$?
	fi
	printf '%s\n' "$(date +%s)" >"$last_file"
	# Fall back to Claude when codex is rate-limited.
	if [ "$rc" -ne 0 ] && [ "$claude_fallback_enabled" = "1" ] && _output_indicates_rate_limit "$log_file" "$output_file"; then
		if command -v "$claude_cmd" >/dev/null 2>&1; then
			echo "[codex_bug_dispatcher] codex rate-limited (rc=$rc); falling back to claude" >>"$log_file"
			local claude_output_file fallback_rc=0
			local -a claude_args
			claude_output_file="$log_dir/last_${ts}_$$_claude.txt"
			claude_args=(--print -p "$(cat "$prompt_file")" --permission-mode="$claude_permission_mode" --no-session-persistence)
			[ -n "$claude_model" ] && claude_args+=(--model="$claude_model")
			(cd "$ELOOP_LIB_DIR" && "$claude_cmd" "${claude_args[@]}") >"$claude_output_file" 2>>"$log_file" || fallback_rc=$?
			if [ "$fallback_rc" -eq 0 ]; then
				echo "[codex_bug_dispatcher] claude fallback succeeded" >>"$log_file"
				rc=0
			else
				echo "[codex_bug_dispatcher] claude fallback failed (rc=$fallback_rc)" >>"$log_file"
				rc=$fallback_rc
			fi
		else
			echo "[codex_bug_dispatcher] claude command not found: $claude_cmd (skipping fallback)" >>"$log_file"
		fi
	fi
	if [ "$rc" -eq 0 ]; then
		_mark_report "$report_file" "done" "$rc"
	else
		_mark_report "$report_file" "failed" "$rc"
	fi
	exit "$rc"
}

case "$mode" in
kick) _kick ;;
run | "") _run_once ;;
status)
	if _lock_held; then
		echo "running"
	else
		echo "idle"
	fi
	;;
*)
	echo "usage: $0 [kick|run|status]" >&2
	exit 2
	;;
esac
