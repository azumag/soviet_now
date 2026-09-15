#!/bin/bash
# strategy/persist.sh - turn-based 戦略の GitHub 永続化
#
# 背景:
#   本番ランタイム /home/ubuntu/soren は docich の projection 先で .git を持たない。
#   そのため eloop_improve.sh 内の素の `git add/commit/push` は無言で no-op になり、
#   改善が GitHub へ永続化されない (2026-09-15 に判明)。
#
# 方式:
#   管理用 clone (SOREN_PERSIST_REPO, 既定 /home/ubuntu/soren-persist) を
#   origin/main へ fast-forward し、runtime 側で **tracked な成果物**だけをコピーして
#   commit → main へ push する。turn-based 戦略の品質は A/B テストと branch ranking が
#   担保するため main 直 push を採用する。
#
# 契約:
#   - clone 未整備や VM 以外では何もせず 0 を返す (no-op)。runtime を変更しない。
#   - 戻り値: 0 = push した / 3 = 変更なし / 非0(1) = 失敗。
#     呼び出し側は `|| true` で改善自体は継続してよい。
#   - 途中失敗時は runtime に触れず非0を返す。force-push はしない。
#   - 同時実行は clone 内の flock で直列化する。

SOREN_PERSIST_REPO="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"

# tracked な成果物 (存在しない path はコピー対象から自然に外れる)。
SOREN_PERSIST_PATHS=(
	strategy.py strategy_helpers strategy_versions strategy_versions_archive best_score.txt
)

# persist_strategy_improve <commit message> [path ...]
persist_strategy_improve() {
	local message="${1:-}"
	shift || true
	local runtime="${SOREN_SCRIPT_ROOT:-${HOST_ROOT:-$PWD}}"
	local repo="$SOREN_PERSIST_REPO"
	local -a paths=("$@")
	[ "${#paths[@]}" -gt 0 ] || paths=("${SOREN_PERSIST_PATHS[@]}")

	# clone が無い環境では何もしない (runtime 互換・CI 互換)。
	[ -d "$repo/.git" ] || return 0
	[ -n "$message" ] || return 1

	# A fresh clone may have no committer identity; without this the commit fails
	# and the loop would silently drop the improvement (as it did before).
	if ! git -C "$repo" config user.email >/dev/null 2>&1; then
		git -C "$repo" config user.name "${SOREN_PERSIST_USER_NAME:-docich-vm}" >/dev/null 2>&1 || true
		git -C "$repo" config user.email "${SOREN_PERSIST_USER_EMAIL:-9018513+azumag@users.noreply.github.com}" >/dev/null 2>&1 || true
	fi

	if command -v flock >/dev/null 2>&1; then
		(
			exec 9>>"$repo/.git/persist.lock" || exit 1
			flock 9 || exit 1
			_persist_strategy_improve_locked "$message" "$runtime" "$repo" "${paths[@]}"
		)
	else
		_persist_strategy_improve_locked "$message" "$runtime" "$repo" "${paths[@]}"
	fi
}

_persist_strategy_improve_locked() {
	local message="$1" runtime="$2" repo="$3"
	shift 3
	local -a paths=("$@")

	local attempt
	for attempt in 1 2 3; do
		git -C "$repo" fetch --quiet origin main || return 1
		git -C "$repo" checkout --quiet main 2>/dev/null \
			|| git -C "$repo" checkout --quiet -B main origin/main || return 1
		git -C "$repo" reset --hard --quiet origin/main || return 1

		local f
		while IFS= read -r -d '' f; do
			[ -e "$runtime/$f" ] || continue
			mkdir -p "$repo/$(dirname "$f")" || return 1
			cp -p "$runtime/$f" "$repo/$f" || return 1
		done < <(git -C "$repo" ls-files -z -- "${paths[@]}" 2>/dev/null)

		git -C "$repo" add -- "${paths[@]}" 2>/dev/null || true
		if git -C "$repo" diff --cached --quiet; then
			return 3
		fi
		git -C "$repo" commit --quiet -m "$message" || return 1
		if git -C "$repo" push --quiet origin main; then
			return 0
		fi
		sleep 2
	done
	return 1
}
