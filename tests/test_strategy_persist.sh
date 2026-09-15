#!/usr/bin/env bash
# turn-based 戦略の GitHub 永続化 (strategy/persist.sh) の契約テスト。
#
# 本番ランタイムは .git を持たないため、eloop_improve.sh の素の git add/commit/push は
# no-op だった。管理用 clone 経由の永続化が main へ push し、未整備時は no-op、
# 変更なしは 3 を返すことを保証する。
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/.." && pwd)"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok - $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

seed="$tmp/seed"
origin="$tmp/origin.git"
work="$tmp/persist"
runtime="$tmp/runtime"
mkdir -p "$seed" "$runtime" "$seed/strategy_helpers" "$runtime/strategy_helpers"

git init -q "$seed"
git -C "$seed" config user.name t
git -C "$seed" config user.email t@example.invalid
printf 'v1\n' > "$seed/strategy.py"
printf 'h1\n' > "$seed/strategy_helpers/a.sh"
git -C "$seed" add -A
git -C "$seed" commit -qm init
git init -q --bare "$origin"
git -C "$seed" remote add origin "$origin"
git -C "$seed" push -q origin HEAD:main
git --git-dir="$origin" symbolic-ref HEAD refs/heads/main >/dev/null
git clone -q "$origin" "$work"

# runtime 側の成果物
cp -a "$seed/strategy.py" "$runtime/strategy.py"
cp -a "$seed/strategy_helpers/a.sh" "$runtime/strategy_helpers/a.sh"

export HOST_ROOT="$runtime"
# shellcheck disable=SC1091
source "$repo_root/strategy/persist.sh"

# 1) clone 未整備では何もせず 0
SOREN_PERSIST_REPO="$tmp/does-not-exist"
rc=0; persist_strategy_improve "m" strategy.py || rc=$?
[ "$rc" -eq 0 ] && pass "missing repo no-op returns 0" || fail "missing repo rc=$rc (want 0)"

# 2) 変更なしは 3
SOREN_PERSIST_REPO="$work"
rc=0; persist_strategy_improve "no-change" strategy.py || rc=$?
[ "$rc" -eq 3 ] && pass "no change returns 3" || fail "no-change rc=$rc (want 3)"

# 3) 変更ありは main へ push
printf 'v2-improved\n' > "$runtime/strategy.py"
printf 'h2\n' > "$runtime/strategy_helpers/a.sh"
rc=0; persist_strategy_improve "eloop Improve test" strategy.py strategy_helpers || rc=$?
[ "$rc" -eq 0 ] && pass "changed push returns 0" || fail "push rc=$rc (want 0)"

got="$(git --git-dir="$origin" show main:strategy.py)"
[ "$got" = "v2-improved" ] && pass "strategy.py persisted to origin/main" || fail "origin strategy.py='$got'"
got2="$(git --git-dir="$origin" show main:strategy_helpers/a.sh)"
[ "$got2" = "h2" ] && pass "strategy_helpers persisted to origin/main" || fail "origin helper='$got2'"

# 4) 再度呼ぶと変更なしで 3 (多重 push しない)
rc=0; persist_strategy_improve "again" strategy.py strategy_helpers || rc=$?
[ "$rc" -eq 3 ] && pass "idempotent second call returns 3" || fail "second call rc=$rc (want 3)"

echo "ALL PASS"
