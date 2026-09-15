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

SOREN_PERSIST_REPO="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"
SOREN_PERSIST_BRANCH="${SOREN_PERSIST_BRANCH:-runtime/eloop-improve}"
SOREN_PERSIST_GITHUB_REPO="${SOREN_PERSIST_GITHUB_REPO:-azumag/soviet_now}"

# tracked な成果物 (存在しない path はコピー対象から自然に外れる)。
SOREN_PERSIST_PATHS=(
	strategy.py strategy_helpers strategy_versions strategy_versions_archive best_score.txt
)

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
	[ -d "$repo/.git" ] || return 0
	[ -n "$message" ] || return 1
	[ -n "$branch" ] && [ "$branch" != "main" ] || return 1
	command -v gh >/dev/null 2>&1 || return 1

	# fresh clone でも candidate commit を作れる repo-local identity。
	if ! git -C "$repo" config user.email >/dev/null 2>&1; then
		git -C "$repo" config user.name "${SOREN_PERSIST_USER_NAME:-docich-vm}" >/dev/null 2>&1 || return 1
		git -C "$repo" config user.email "${SOREN_PERSIST_USER_EMAIL:-9018513+azumag@users.noreply.github.com}" >/dev/null 2>&1 || return 1
	fi

	if command -v flock >/dev/null 2>&1; then
		(
			exec 9>>"$repo/.git/persist.lock" || exit 1
			flock 9 || exit 1
			_persist_strategy_improve_locked "$message" "$runtime" "$repo" "$branch" "$repo_full" "${paths[@]}"
		)
	else
		_persist_strategy_improve_locked "$message" "$runtime" "$repo" "$branch" "$repo_full" "${paths[@]}"
	fi
}

_persist_strategy_improve_locked() {
	local message="$1" runtime="$2" repo="$3" branch="$4" repo_full="$5"
	shift 5
	local -a paths=("$@")

	git -C "$repo" fetch --quiet origin main || return 1

	# 専用 candidate branch の既存 HEAD を lease 用に取得する。存在しないのは正常。
	local remote_ref="refs/remotes/origin/$branch"
	local remote_head=""
	if git -C "$repo" fetch --quiet origin "refs/heads/$branch:$remote_ref" 2>/dev/null; then
		remote_head="$(git -C "$repo" rev-parse "$remote_ref" 2>/dev/null)" || return 1
	else
		git -C "$repo" update-ref -d "$remote_ref" >/dev/null 2>&1 || true
	fi

	# 毎回 current main から candidate を再構築し、古い runtime candidate の混入を防ぐ。
	git -C "$repo" checkout --quiet -B "$branch" origin/main || return 1
	git -C "$repo" reset --hard --quiet origin/main || return 1

	local f
	while IFS= read -r -d '' f; do
		[ -e "$runtime/$f" ] || continue
		mkdir -p "$repo/$(dirname "$f")" || return 1
		cp -p "$runtime/$f" "$repo/$f" || return 1
	done < <(git -C "$repo" ls-files -z -- "${paths[@]}" 2>/dev/null)

	git -C "$repo" add -- "${paths[@]}" 2>/dev/null || return 1
	if git -C "$repo" diff --cached --quiet; then
		# runtime が既に main と同一なら候補自体が不要。
		return 3
	fi

	git -C "$repo" commit --quiet -m "$message" || return 1
	local candidate_tree
	candidate_tree="$(git -C "$repo" rev-parse HEAD^{tree})" || return 1

	# remote candidate が同一 tree なら無駄な commit/push をせず、PRだけ保証する。
	if [ -n "$remote_head" ]; then
		local remote_tree
		remote_tree="$(git -C "$repo" rev-parse "$remote_head^{tree}" 2>/dev/null)" || return 1
		if [ "$candidate_tree" = "$remote_tree" ]; then
			local pr_rc=0
			_persist_ensure_pr "$message" "$branch" "$repo_full" || pr_rc=$?
			[ "$pr_rc" -eq 1 ] && return 1
			return 3
		fi
	fi

	# main へは決して push しない。専用候補branchだけを lease 付きで更新する。
	if [ -n "$remote_head" ]; then
		git -C "$repo" push --quiet \
			--force-with-lease="refs/heads/$branch:$remote_head" \
			origin "HEAD:refs/heads/$branch" || return 1
	else
		git -C "$repo" push --quiet origin "HEAD:refs/heads/$branch" || return 1
	fi

	local pr_rc=0
	_persist_ensure_pr "$message" "$branch" "$repo_full" || pr_rc=$?
	[ "$pr_rc" -eq 1 ] && return 1
	return 0
}
