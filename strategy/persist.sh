#!/bin/bash
# strategy/persist.sh - turn-based 戦略の GitHub 永続化
#
# 本番ランタイム /home/ubuntu/soren は projection 先で .git を持たないため、
# 採用済み戦略は管理 clone へ写して永続化する。ただし runtime 生成物を main へ
# 直接 push してはいけない。専用 candidate branch を更新し、PR + CI + review を
# 通したものだけを main へ入れる。
#
# 契約:
#   - clone 未整備や VM 以外では何もせず 0 を返す (runtime を変更しない)。
#   - 戻り値: 0 = candidate branch/PR を作成または更新 / 3 = 変更なし / 1 = 失敗。
#   - origin/main は fetch/reset の基準として読むだけで、絶対に push/force-push しない。
#     (soviet_now main は ruleset で pull_request 必須 + non_fast_forward + deletion 禁止。
#      直接 push は GitHub 側でも "Changes must be made through a pull request." で拒否される。)
#   - 専用 branch の更新だけ、取得済み remote HEAD に対する --force-with-lease を許す。
#   - 同時実行は clone 内の flock で直列化する。
#   - persist の結果（成功/変更なし/失敗）は SOREN_PERSIST_STATUS_FILE に JSON で記録する。
#     呼び出し側はこのファイルを読んで失敗を検知できる（silent な 2>/dev/null は非推奨）。
#   - status JSON は result / reason / rc を持つ。reason は下記の固定 enum で、
#     生のエラーメッセージ (error) とは別に worker health へ投影できる。
#     reason enum: none, clone_missing, invalid_message, invalid_branch, gh_missing,
#       identity_setup_failed, fetch_failed, remote_head_failed, checkout_failed,
#       reset_failed, copy_failed, add_failed, commit_failed, tree_failed,
#       push_failed, pr_failed, unknown
#   - status は origin_main (最後に fetch した origin/main の sha)、last_success_at
#     (最後に candidate 作成/更新へ成功した epoch)、error (失敗した git の stderr 抜粋)
#     も持つ。clone が main から取り残される停止 (#2489 の HEAD 2026-09-17 型) を
#     機械的に検知するための材料であり、状態の修復はここでは行わない。
#   - persist 失敗時は stderr に [persist] ERROR: ... を出力する。呼び出し側で
#     2>/dev/null すると失敗が隠れるため、運用では stderr を保持するか status file を読むこと。
#   - _persist_log_failure <label> <rc> は失敗を stderr に加えて durable な
#     strategy_persist_last_failure.json (failure_code=strategy_persist_failed) に残す。
#     次の成功/no-change でこの record は消える。docich の diagnostics は
#     status file とこの record の両方を読む。
#
# candidate と main の競合解決手順（review で解決）:
#   1. persist は毎回 current main から candidate を再構築する（git checkout -B branch origin/main
#      + reset --hard origin/main）。古い runtime candidate の混入を防ぐ。
#   2. runtime の採用成果物を main ツリーへ上書きコピーする。これは「main が基準」
#      ではない。コピー対象 path については runtime が勝ち、main 側で同じ path が
#      変更されていた場合、その変更は candidate diff 上で revert として現れる。
#      したがって並行進化の解消は review で行う必要がある。
#   3. candidate と main の競合（main 側の strategy.py 変更と runtime 採用の並行進化）は
#      PR review で解決する。reviewer は candidate diff を見て、main 側の変更を candidate へ
#      マージし直すか、runtime 採用を main へ取り込むかを判断する。
#   4. persist が conflict を検出できない場合（push が reject された場合）、戻り値 1 を返し
#      status file に failure を記録する。呼び出し側は次回の persist 呼び出しで自動的に
#      main を再 fetch して candidate を再構築する（冪等）。
#   5. 採用完了の条件: candidate PR が CI green + review OK で main に merge されたこと。
#      persist_strategy_improve の戻り値 0 は「candidate を作成/更新した」だけであり、
#      main への統合完了ではない。main への統合は PR merge で初めて完了する。

SOREN_PERSIST_REPO="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"
SOREN_PERSIST_BRANCH="${SOREN_PERSIST_BRANCH:-runtime/eloop-improve}"
SOREN_PERSIST_GITHUB_REPO="${SOREN_PERSIST_GITHUB_REPO:-azumag/soviet_now}"
SOREN_PERSIST_STATUS_FILE="${SOREN_PERSIST_STATUS_FILE:-}"

# tracked な成果物 (存在しない path はコピー対象から自然に外れる)。
SOREN_PERSIST_PATHS=(
	strategy.py strategy_helpers strategy_versions strategy_versions_archive best_score.txt
)

_persist_status_file() {
	local repo="$1"
	if [ -n "$SOREN_PERSIST_STATUS_FILE" ]; then
		printf '%s' "$SOREN_PERSIST_STATUS_FILE"
		return 0
	fi
	printf '%s/.git/persist-status.json' "$repo"
}

_persist_last_success_file() {
	local repo="$1"
	printf '%s/.git/persist-last-success.json' "$repo"
}

_persist_last_success_at() {
	local repo="$1" file
	file="$(_persist_last_success_file "$repo")"
	sed -n 's/.*"success_at": \([0-9][0-9]*\).*/\1/p' "$file" 2>/dev/null | head -1
}

# candidate 作成/更新に成功した時刻を、失敗時の status にも引き継げるよう別ファイルに残す。
_persist_record_success() {
	local repo="$1" commit="$2" origin_main="${3:-}"
	[ -d "$repo/.git" ] || return 0
	local file tmp now
	file="$(_persist_last_success_file "$repo")"
	tmp="${file}.tmp.$$"
	now="$(date +%s 2>/dev/null || echo 0)"
	printf '{"success_at": %s, "commit": "%s", "origin_main": "%s"}\n' \
		"$now" "$commit" "$origin_main" >"$tmp" 2>/dev/null \
		&& mv "$tmp" "$file" 2>/dev/null || rm -f "$tmp" 2>/dev/null
	return 0
}

# git の stderr を 1 行・400 文字に丸めて status の error に入れる。
_persist_error_excerpt() {
	local file="$1" fallback="$2" text
	text="$(tail -c 400 "$file" 2>/dev/null | tr -d '\000-\010\013\014\016-\037' | tr '\n\r\t' '   ' | sed 's/  */ /g; s/^ //; s/ $//')"
	[ -n "$text" ] || text="$fallback"
	printf '%s' "$text"
}

_persist_write_status() {
	local repo="$1" result="$2" message="$3" branch="$4" commit="${5:-}" reason="${6:-none}" rc="${7:-}" error="${8:-}"
	# clone が無い環境では status file を書かない (no-op)。
	[ -d "$repo" ] || return 0
	local status_file
	status_file="$(_persist_status_file "$repo")"
	mkdir -p "$(dirname "$status_file")" 2>/dev/null || return 0
	local ts
	ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
	local rc_json="null"
	case "$rc" in
	''|*[!0-9]*) ;;
	*) rc_json="$rc" ;;
	esac
	local last_success_json="null" last_success
	last_success="$(_persist_last_success_at "$repo")"
	case "$last_success" in
	''|*[!0-9]*) ;;
	*) last_success_json="$last_success" ;;
	esac
	local tmp="${status_file}.tmp.$$"
	{
		printf '{\n'
		printf '  "timestamp": "%s",\n' "$ts"
		printf '  "result": "%s",\n' "$result"
		printf '  "reason": "%s",\n' "$reason"
		printf '  "rc": %s,\n' "$rc_json"
		printf '  "origin_main": "%s",\n' "${PERSIST_ORIGIN_MAIN_SHA:-}"
		printf '  "last_success_at": %s,\n' "$last_success_json"
		printf '  "message": "%s",\n' "$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')"
		printf '  "branch": "%s",\n' "$(printf '%s' "$branch" | sed 's/\\/\\\\/g; s/"/\\"/g')"
		printf '  "commit": "%s",\n' "$(printf '%s' "$commit" | sed 's/\\/\\\\/g; s/"/\\"/g')"
		printf '  "error": "%s"\n' "$(printf '%s' "$error" | sed 's/\\/\\\\/g; s/"/\\"/g')"
		printf '}\n'
	} > "$tmp" 2>/dev/null && mv "$tmp" "$status_file" 2>/dev/null || rm -f "$tmp" 2>/dev/null
}

_persist_last_reason() {
	local repo="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"
	local status_file
	status_file="$(_persist_status_file "$repo")"
	# status file の reason は本スクリプトが書く固定 enum。読めなければ空。
	sed -n 's/^  "reason": "\([a-z_][a-z_]*\)",$/\1/p' "$status_file" 2>/dev/null | head -1
}

_persist_log() {
	local level="$1"; shift
	printf '[persist] %s: %s\n' "$level" "$*" >&2
}

# _persist_log_failure <label> <rc> - persist 失敗を記録する（eloop_improve.sh から呼ばれる）
_persist_log_failure() {
	local label="${1:-unknown}" rc="${2:-1}" reason="${3:-}"
	# rc 3 は「runtime が main と同一 = 変更なし」。失敗ではないので記録しない。
	if [ "$rc" = "3" ]; then
		return 0
	fi
	[ -n "$reason" ] || reason="$(_persist_last_reason)"
	[ -n "$reason" ] || reason=unknown
	_persist_log "ERROR" "persist_strategy_improve failed (label=$label rc=$rc reason=$reason). Check persist-status.json and persist-errors.log."
	# durable な fixed-enum record。呼び出し側のログが流れても diagnostics が拾える。
	if declare -F _record_strategy_persist_failure >/dev/null 2>&1; then
		_record_strategy_persist_failure "$reason" "$rc" "$label"
	fi
	return 0
}

# _persist_clear_failure_record - 直近の persist が成功/no-change なら失敗 record を消す。
_persist_clear_failure_record() {
	if declare -F _clear_strategy_persist_failure >/dev/null 2>&1; then
		_clear_strategy_persist_failure
	fi
}

_persist_ensure_pr() {
	local message="$1" branch="$2" repo_full="$3"
	command -v gh >/dev/null 2>&1 || return 1

	local existing=""
	existing="$(gh pr list --repo "$repo_full" --state open --base main --head "$branch" \
		--json number --jq '.[0].number // empty' 2>/dev/null)" || return 1
	if [ -n "$existing" ]; then
		return 3
	fi

	gh pr create --repo "$repo_full" --base main --head "$branch" \
		--title "$message" \
		--body $'Automated runtime strategy candidate.\n\nThis branch contains an adopted turn-based strategy copied from the production runtime. It must pass repository CI and review before merge. The runtime persistence path never pushes directly to main.' \
		>/dev/null 2>&1 || return 1
	return 0
}

# persist_strategy_improve <commit message> [path ...]
persist_strategy_improve() {
	local message="${1:-}"
	shift || true
	local runtime="${SOREN_SCRIPT_ROOT:-${HOST_ROOT:-$PWD}}"
	local repo="$SOREN_PERSIST_REPO"
	local branch="$SOREN_PERSIST_BRANCH"
	local repo_full="$SOREN_PERSIST_GITHUB_REPO"
	local -a paths=("$@")
	[ "${#paths[@]}" -gt 0 ] || paths=("${SOREN_PERSIST_PATHS[@]}")

	# clone が無い環境では何もしない (runtime 互換・CI 互換)。
	if [ ! -d "$repo/.git" ]; then
		_persist_write_status "$repo" "no-op" "$message" "$branch" "" "clone_missing" "0" "clone not present"
		# durable record は TMP_STATE_DIR 側に残る (clone 消失後も診断可能)。
		if declare -F _record_strategy_persist_failure >/dev/null 2>&1; then
			_record_strategy_persist_failure "clone_missing" "0" "persist"
		fi
		return 0
	fi
	if [ -z "$message" ]; then
		_persist_log "ERROR" "empty commit message"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "invalid_message" "1" "empty commit message"
		return 1
	fi
	if [ -z "$branch" ] || [ "$branch" = "main" ]; then
		_persist_log "ERROR" "invalid branch: $branch"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "invalid_branch" "1" "invalid branch"
		return 1
	fi
	if ! command -v gh >/dev/null 2>&1; then
		_persist_log "ERROR" "gh CLI not found"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "gh_missing" "1" "gh CLI not found"
		return 1
	fi

	# fresh clone でも candidate commit を作れる repo-local identity。
	if ! git -C "$repo" config user.email >/dev/null 2>&1; then
		git -C "$repo" config user.name "${SOREN_PERSIST_USER_NAME:-docich-vm}" >/dev/null 2>&1 || {
			_persist_log "ERROR" "failed to set git user.name"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "identity_setup_failed" "1" "failed to set git user.name"
			return 1
		}
		git -C "$repo" config user.email "${SOREN_PERSIST_USER_EMAIL:-9018513+azumag@users.noreply.github.com}" >/dev/null 2>&1 || {
			_persist_log "ERROR" "failed to set git user.email"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "identity_setup_failed" "1" "failed to set git user.email"
			return 1
		}
	fi

	local rc=0
	if command -v flock >/dev/null 2>&1; then
		(
			exec 9>>"$repo/.git/persist.lock" || exit 1
			flock 9 || exit 1
			_persist_strategy_improve_locked "$message" "$runtime" "$repo" "$branch" "$repo_full" "${paths[@]}"
		)
		rc=$?
	else
		_persist_strategy_improve_locked "$message" "$runtime" "$repo" "$branch" "$repo_full" "${paths[@]}"
		rc=$?
	fi
	# 0 (candidate 作成/更新) と 3 (変更なし) は直近の永続化が健全。
	# 1 (失敗) は呼び出し側が _persist_log_failure で durable record を残す。
	if [ "$rc" -eq 0 ] || [ "$rc" -eq 3 ]; then
		_persist_clear_failure_record
	fi
	return "$rc"
}

_persist_strategy_improve_locked() {
	local message="$1" runtime="$2" repo="$3" branch="$4" repo_full="$5"
	shift 5
	local -a paths=("$@")
	local err_file="$repo/.git/persist-last-error.txt"
	PERSIST_ORIGIN_MAIN_SHA=""

	if ! git -C "$repo" fetch --quiet origin main >/dev/null 2>"$err_file"; then
		local err
		err="$(_persist_error_excerpt "$err_file" "git fetch origin main failed")"
		_persist_log "ERROR" "git fetch origin main failed: $err"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "fetch_failed" "1" "$err"
		return 1
	fi
	PERSIST_ORIGIN_MAIN_SHA="$(git -C "$repo" rev-parse origin/main 2>/dev/null || true)"

	# 専用 candidate branch の既存 HEAD を lease 用に取得する。存在しないのは正常。
	local remote_ref="refs/remotes/origin/$branch"
	local remote_head=""
	if git -C "$repo" fetch --quiet origin "refs/heads/$branch:$remote_ref" 2>/dev/null; then
		remote_head="$(git -C "$repo" rev-parse "$remote_ref" 2>/dev/null)" || {
			_persist_log "ERROR" "failed to resolve remote head"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "remote_head_failed" "1" "failed to resolve remote head"
			return 1
		}
	else
		git -C "$repo" update-ref -d "$remote_ref" >/dev/null 2>&1 || true
	fi

	# 毎回 current main から candidate を再構築し、古い runtime candidate の混入を防ぐ。
	if ! git -C "$repo" checkout --quiet -B "$branch" origin/main >/dev/null 2>"$err_file"; then
		local err
		err="$(_persist_error_excerpt "$err_file" "git checkout failed")"
		_persist_log "ERROR" "git checkout -B $branch origin/main failed: $err"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "checkout_failed" "1" "$err"
		return 1
	fi
	if ! git -C "$repo" reset --hard --quiet origin/main >/dev/null 2>"$err_file"; then
		local err
		err="$(_persist_error_excerpt "$err_file" "git reset failed")"
		_persist_log "ERROR" "git reset --hard origin/main failed: $err"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "reset_failed" "1" "$err"
		return 1
	fi

	local f
	local -a copied=()
	while IFS= read -r -d '' f; do
		[ -e "$runtime/$f" ] || continue
		mkdir -p "$repo/$(dirname "$f")" || {
			_persist_log "ERROR" "mkdir failed for $f"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "copy_failed" "1" "mkdir failed"
			return 1
		}
		cp -p "$runtime/$f" "$repo/$f" || {
			_persist_log "ERROR" "cp failed for $f"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "copy_failed" "1" "cp failed for $f"
			return 1
		}
		copied+=("$f")
	done < <(git -C "$repo" ls-files -z -- "${paths[@]}" 2>/dev/null)

	if [ "${#copied[@]}" -eq 0 ]; then
		# tracked な対象が runtime に一つも無い = 設定ミス (runtime の tracked path が空)。
		# no-change ではなく失敗として扱い、failure record を残す。
		_persist_log "ERROR" "no tracked paths in runtime"
		_persist_write_status "$repo" "fail" "$message" "$branch" "$(git -C "$repo" rev-parse HEAD 2>/dev/null)" "copy_failed" "1" "no tracked paths in runtime"
		return 1
	fi
	if ! git -C "$repo" add -- "${copied[@]}" 2>/dev/null; then
		_persist_log "ERROR" "git add failed"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "add_failed" "1" "git add failed"
		return 1
	fi
	if git -C "$repo" diff --cached --quiet; then
		# runtime が既に main と同一なら候補自体が不要。
		_persist_write_status "$repo" "no-change" "$message" "$branch" "$(git -C "$repo" rev-parse HEAD 2>/dev/null)" "none" "3" ""
		return 3
	fi

	if ! git -C "$repo" commit --quiet -m "$message" >/dev/null 2>"$err_file"; then
		local err
		err="$(_persist_error_excerpt "$err_file" "git commit failed")"
		_persist_log "ERROR" "git commit failed: $err"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "commit_failed" "1" "$err"
		return 1
	fi
	local candidate_tree
	candidate_tree="$(git -C "$repo" rev-parse HEAD^{tree})" || {
		_persist_log "ERROR" "failed to resolve candidate tree"
		_persist_write_status "$repo" "fail" "$message" "$branch" "" "tree_failed" "1" "failed to resolve candidate tree"
		return 1
	}

	# remote candidate が同一 tree なら無駄な commit/push をせず、PRだけ保証する。
	if [ -n "$remote_head" ]; then
		local remote_tree
		remote_tree="$(git -C "$repo" rev-parse "$remote_head^{tree}" 2>/dev/null)" || {
			_persist_log "ERROR" "failed to resolve remote tree"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "tree_failed" "1" "failed to resolve remote tree"
			return 1
		}
		if [ "$candidate_tree" = "$remote_tree" ]; then
			local pr_rc=0
			_persist_ensure_pr "$message" "$branch" "$repo_full" || pr_rc=$?
			if [ "$pr_rc" -eq 1 ]; then
				_persist_log "ERROR" "PR ensure failed"
				_persist_write_status "$repo" "fail" "$message" "$branch" "$remote_head" "pr_failed" "1" "PR ensure failed"
				return 1
			fi
			_persist_write_status "$repo" "no-change" "$message" "$branch" "$remote_head" "none" "3" ""
			return 3
		fi
	fi

	# main へは決して push しない。専用候補branchだけを lease 付きで更新する。
	if [ -n "$remote_head" ]; then
		if ! git -C "$repo" push --quiet \
			--force-with-lease="refs/heads/$branch:$remote_head" \
			origin "HEAD:refs/heads/$branch" >/dev/null 2>"$err_file"; then
			local err
			err="$(_persist_error_excerpt "$err_file" "git push --force-with-lease failed")"
			_persist_log "ERROR" "git push --force-with-lease failed (conflict or network): $err"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "push_failed" "1" "$err"
			return 1
		fi
	else
		if ! git -C "$repo" push --quiet origin "HEAD:refs/heads/$branch" >/dev/null 2>"$err_file"; then
			local err
			err="$(_persist_error_excerpt "$err_file" "git push failed")"
			_persist_log "ERROR" "git push failed: $err"
			_persist_write_status "$repo" "fail" "$message" "$branch" "" "push_failed" "1" "$err"
			return 1
		fi
	fi

	local pr_rc=0
	_persist_ensure_pr "$message" "$branch" "$repo_full" || pr_rc=$?
	if [ "$pr_rc" -eq 1 ]; then
		_persist_log "ERROR" "PR ensure failed after push"
		_persist_write_status "$repo" "fail" "$message" "$branch" "$(git -C "$repo" rev-parse HEAD 2>/dev/null)" "pr_failed" "1" "PR ensure failed after push"
		return 1
	fi
	local commit_sha
	commit_sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
	_persist_record_success "$repo" "$commit_sha" "$PERSIST_ORIGIN_MAIN_SHA"
	_persist_write_status "$repo" "ok" "$message" "$branch" "$commit_sha" "none" "0" ""
	return 0
}

# persist_status - 最後の persist 結果を読み出す
persist_status() {
	local repo="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"
	local status_file
	status_file="$(_persist_status_file "$repo")"
	if [ -f "$status_file" ]; then
		cat "$status_file"
	else
		printf '{"result":"unknown","error":"no status file"}\n'
	fi
}
