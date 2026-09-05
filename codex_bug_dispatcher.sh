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
# docich#32: 自動dispatch対象外になったqueueの隔離先。生本文は複製せず、
# 元ファイルを移動するだけ(削除しない)。通知には件数/category/timeのみ書く。
quarantine_dir="${CODEX_BUG_QUARANTINE_DIR:-$queue_dir/quarantined}"
quarantine_notice_file="${CODEX_BUG_QUARANTINE_NOTICE_FILE:-$log_dir/quarantine_notice.log}"
min_interval="${CODEX_BUG_DISPATCH_MIN_INTERVAL_SEC:-900}"
codex_cmd="${CODEX_BUG_DISPATCH_CODEX_CMD:-codex}"
# docich#32: 視聴者コメント発の権限迂回fallback(旧: codexレート制限時にclaudeの
# permission-mode迂回オプションで自動フォールバック)はproduction codeから削除済み。
# 環境変数での復活経路は用意しない。

# docich#39: Coding Agent (codex) はTwitch/YouTube/Discord等のcredentialを
# 見る必要が無い。env -i で環境を空にした上で、agentの実行そのものに要る
# non-secretな変数だけを明示的に許可(allowlist)する。ここに挙げるのは
# ロケール/パス/ターミナル/一時ディレクトリ等の実行環境設定のみで、
# credential(トークン・APIキー・パスワード等)は一切含めない。
CODEX_AGENT_ENV_ALLOWLIST=(
	HOME PATH SHELL
	LANG LC_ALL LC_CTYPE LC_MESSAGES
	TERM TMPDIR TEMP TMP
	USER LOGNAME TZ
	XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME
)

# _run_with_restricted_env CMD [ARGS...]
# env -i で真っさらにした環境に、CODEX_AGENT_ENV_ALLOWLIST 記載かつ現在
# 実際にsetされている変数だけを積んでCMDを実行する。CMD(codex)が内部で
# 呼ぶ`./system_progress_report.sh`等の運用スクリプトは、それぞれ自前で
# `.env`を再読込する設計になっているため、ここでagentのenvを絞っても
# それらの機能自体は損なわれない(agent自身のprocess environmentに
# credentialが乗らなくなるだけ)。
_run_with_restricted_env() {
	local -a env_args=()
	local name
	for name in "${CODEX_AGENT_ENV_ALLOWLIST[@]}"; do
		if [ "${!name+set}" = "set" ]; then
			env_args+=("${name}=${!name}")
		fi
	done
	env -i "${env_args[@]}" "$@"
}

# docich#39: Coding Agentへ渡すprompt / agentが書くoutput・logは、保存前に
# secret redactorへ通す。値そのものはredactor(lib/secret_redactor.py)が
# 現在の環境変数から読むが、このdispatcher自身はredact後のテキストしか
# ファイルへ書かない。
_redact_secrets_file() {
	local src_path="$1" dest_path="$2"
	python3 "$ELOOP_LIB_DIR/lib/secret_redactor.py" <"$src_path" >"$dest_path" 2>/dev/null \
		|| cp "$src_path" "$dest_path"
}

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

# docich#32: 自動実行対象外(既定)の未処理queueをquarantineへ退避する。
# - 削除はしない(mv のみ)。
# - 通知(quarantine_notice_file)には件数/category/timeだけを書き、
#   viewerのコメント本文やuser名は一切複製しない。
_quarantine_pending_reports() {
	mkdir -p "$queue_dir" "$quarantine_dir" "$(dirname "$quarantine_notice_file")" 2>/dev/null || true
	local -a reports=()
	local f
	while IFS= read -r f; do
		[ -n "$f" ] && reports+=("$f")
	done < <(find "$queue_dir" -maxdepth 1 -type f -name '*.json' ! -name '.*' -print 2>/dev/null | sort)
	[ "${#reports[@]}" -gt 0 ] || return 0

	local summary=""
	summary=$(python3 - "$quarantine_dir" "${reports[@]}" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

dest_dir = Path(sys.argv[1])
dest_dir.mkdir(parents=True, exist_ok=True)
paths = sys.argv[2:]

counts = {}
oldest = None
newest = None
moved = 0
for raw_path in paths:
    src = Path(raw_path)
    category = "unknown"
    created_at = None
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        category = str(data.get("category") or "unknown")
        created_at = data.get("created_at")
    except Exception:
        pass
    counts[category] = counts.get(category, 0) + 1
    if isinstance(created_at, (int, float)):
        if oldest is None or created_at < oldest:
            oldest = created_at
        if newest is None or created_at > newest:
            newest = created_at
    dest = dest_dir / src.name
    try:
        os.replace(src, dest)
        moved += 1
    except Exception:
        pass

# Intentionally excludes comment body / user: operators get counts only.
result = {
    "quarantined_at": int(time.time()),
    "count": moved,
    "by_category": counts,
    "oldest_created_at": oldest,
    "newest_created_at": newest,
}
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
PY
	) || true
	[ -n "$summary" ] || return 0
	printf '%s\n' "$summary" >>"$quarantine_notice_file"
	echo "[codex_bug_dispatcher] auto-dispatch disabled: quarantined pending reports: $summary" >&2
}

_kick() {
	if [ "${CODEX_BUG_DISPATCH_ENABLED:-0}" != "1" ]; then
		_quarantine_pending_reports
		exit 0
	fi
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
	if [ "${CODEX_BUG_DISPATCH_ENABLED:-0}" != "1" ]; then
		_quarantine_pending_reports
		exit 0
	fi
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
	local raw_prompt_file raw_output_file raw_log_file
	ts=$(date +%Y%m%d_%H%M%S)
	prompt_file="$log_dir/prompt_${ts}_$$.txt"
	output_file="$log_dir/last_${ts}_$$.txt"
	log_file="$log_dir/run_${ts}_$$.log"
	# docich#39: 生の(redact前)成果物はagent実行が終わるまでlog_dir外の
	# 一時ファイルに留め、redact後にのみ最終パス(prompt_file/output_file/
	# log_file)へ書く。
	raw_prompt_file=$(mktemp "${log_dir}/.raw_prompt.XXXXXXXX")
	raw_output_file=$(mktemp "${log_dir}/.raw_output.XXXXXXXX")
	raw_log_file=$(mktemp "${log_dir}/.raw_log.XXXXXXXX")
	_build_prompt "$report_file" >"$raw_prompt_file"
	echo "[codex_bug_dispatcher] dispatching $(basename "$report_file")" >>"$raw_log_file"
	# Runs: codex exec -C /Users/azumag/azumag/work/soren "<prompt>"
	# docich#39: codexはenv -i + non-secret allowlistだけを持つ環境で起動する。
	if [ -n "${CODEX_BUG_DISPATCH_MODEL:-}" ]; then
		_run_with_restricted_env "$codex_cmd" exec -C "$ELOOP_LIB_DIR" -m "$CODEX_BUG_DISPATCH_MODEL" -o "$raw_output_file" "$(cat "$raw_prompt_file")" >>"$raw_log_file" 2>&1 || rc=$?
	else
		_run_with_restricted_env "$codex_cmd" exec -C "$ELOOP_LIB_DIR" -o "$raw_output_file" "$(cat "$raw_prompt_file")" >>"$raw_log_file" 2>&1 || rc=$?
	fi
	_redact_secrets_file "$raw_prompt_file" "$prompt_file"
	_redact_secrets_file "$raw_output_file" "$output_file"
	_redact_secrets_file "$raw_log_file" "$log_file"
	rm -f "$raw_prompt_file" "$raw_output_file" "$raw_log_file"
	printf '%s\n' "$(date +%s)" >"$last_file"
	# docich#32: codexレート制限時のclaude権限迂回fallbackは削除済み。復活させない。
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
quarantine) _quarantine_pending_reports ;;
status)
	if _lock_held; then
		echo "running"
	else
		echo "idle"
	fi
	;;
*)
	echo "usage: $0 [kick|run|status|quarantine]" >&2
	exit 2
	;;
esac
