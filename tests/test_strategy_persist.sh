#!/usr/bin/env bash
# turn-based 戦略の GitHub 永続化契約。
# runtime の採用戦略は main へ直pushせず、専用 candidate branch + PR に限定する。
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
fakebin="$tmp/bin"
gh_log="$tmp/gh.log"
gh_open_pr="$tmp/gh-open-pr"
mkdir -p "$seed" "$runtime" "$seed/strategy_helpers" "$runtime/strategy_helpers" "$fakebin"

cat > "$fakebin/gh" <<'EOF'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$GH_LOG"
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "list" ]; then
  if [ -f "$GH_OPEN_PR" ]; then
    printf '42\n'
  fi
  exit 0
fi
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "create" ]; then
  touch "$GH_OPEN_PR"
  printf 'https://example.invalid/pull/42\n'
  exit 0
fi
exit 1
EOF
chmod +x "$fakebin/gh"
export PATH="$fakebin:$PATH" GH_LOG="$gh_log" GH_OPEN_PR="$gh_open_pr"

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

# 2) runtime == main は 3。PRも作らない。
SOREN_PERSIST_REPO="$work"
SOREN_PERSIST_BRANCH="runtime/eloop-improve"
SOREN_PERSIST_GITHUB_REPO="azumag/soviet_now"
rc=0; persist_strategy_improve "no-change" strategy.py || rc=$?
[ "$rc" -eq 3 ] && pass "no change returns 3" || fail "no-change rc=$rc (want 3)"
[ ! -s "$gh_log" ] && pass "no change does not touch GitHub PR API" || fail "unexpected gh call on no-change"

# 3) 変更ありは main を変更せず candidate branch + PR へ出す。
printf 'v2-improved\n' > "$runtime/strategy.py"
printf 'h2\n' > "$runtime/strategy_helpers/a.sh"
rc=0; persist_strategy_improve "eloop Improve test" strategy.py strategy_helpers || rc=$?
[ "$rc" -eq 0 ] && pass "changed candidate returns 0" || fail "candidate rc=$rc (want 0)"

main_value="$(git --git-dir="$origin" show main:strategy.py)"
[ "$main_value" = "v1" ] && pass "origin/main remains unchanged" || fail "origin/main was mutated: '$main_value'"
candidate_value="$(git --git-dir="$origin" show runtime/eloop-improve:strategy.py)"
[ "$candidate_value" = "v2-improved" ] && pass "strategy persisted to candidate branch" || fail "candidate strategy='$candidate_value'"
candidate_helper="$(git --git-dir="$origin" show runtime/eloop-improve:strategy_helpers/a.sh)"
[ "$candidate_helper" = "h2" ] && pass "helper persisted to candidate branch" || fail "candidate helper='$candidate_helper'"
grep -q '^pr create .*--base main .*--head runtime/eloop-improve ' "$gh_log" \
  && pass "candidate opens PR against main" || fail "PR create contract missing"
[ "$(grep -c '^pr create ' "$gh_log")" -eq 1 ] || fail "expected exactly one PR creation"

# 4) 同じ runtime を再度永続化しても branch を書き換えず、既存PRを再利用して 3。
rc=0; persist_strategy_improve "eloop Improve same" strategy.py strategy_helpers || rc=$?
[ "$rc" -eq 3 ] && pass "same candidate returns 3" || fail "same candidate rc=$rc (want 3)"
[ "$(grep -c '^pr create ' "$gh_log")" -eq 1 ] && pass "same candidate does not duplicate PR" || fail "duplicate PR created"

# 5) 新しい採用結果は同じ専用branchを更新するが、mainは依然不変。
printf 'v3-improved\n' > "$runtime/strategy.py"
rc=0; persist_strategy_improve "eloop Improve v3" strategy.py strategy_helpers || rc=$?
[ "$rc" -eq 0 ] && pass "updated candidate returns 0" || fail "updated candidate rc=$rc (want 0)"
main_value="$(git --git-dir="$origin" show main:strategy.py)"
[ "$main_value" = "v1" ] && pass "origin/main still unchanged after update" || fail "origin/main changed after candidate update"
candidate_value="$(git --git-dir="$origin" show runtime/eloop-improve:strategy.py)"
[ "$candidate_value" = "v3-improved" ] && pass "candidate branch updates to latest adopted strategy" || fail "updated candidate='$candidate_value'"
[ "$(grep -c '^pr create ' "$gh_log")" -eq 1 ] && pass "updated candidate reuses open PR" || fail "updated candidate duplicated PR"

echo "ALL PASS"
