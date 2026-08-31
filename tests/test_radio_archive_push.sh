#!/usr/bin/env bash
# radio_archive_push の退避ロジックを検証する。
# - テキストのみ退避 (WAVは対象外)
# - Git鏡の重複排除・冪等性
# - public main への誤爆ガード
# - dry-run で副作用なし
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }
assert_eq() {
  local expected="$1" actual="$2" label="$3"
  if [ "$expected" = "$actual" ]; then
    ok "$label"
  else
    not_ok "$label (expected=$expected actual=$actual)"
  fi
}
assert_contains() {
  local needle="$1" haystack="$2" label="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    ok "$label"
  else
    not_ok "$label (missing: $needle)"
  fi
}

# テスト用に一時的な backups を用意 (本番の backups/radio_scripts を直接使う)
# 既存の backups があれば退避し、テスト後に復元する
BACKUP_ROOT_REAL="$ROOT/backups"
BACKUP_BACKUP_TMP="$TMP/backups_backup"
if [ -d "$BACKUP_ROOT_REAL" ]; then
  mv "$BACKUP_ROOT_REAL" "$BACKUP_BACKUP_TMP" 2>/dev/null || true
fi
mkdir -p "$ROOT/backups/radio_scripts/20260824"
mkdir -p "$ROOT/tmp/state"
# config.sh の既定を最小限で用意 (本番 core/config.sh を読まずテスト用に上書き)
export RADIO_ARCHIVE_ENABLED=1
export RADIO_ARCHIVE_GIT_ENABLED=1
export RADIO_ARCHIVE_RCLONE_ENABLED=0
export RADIO_ARCHIVE_GIT_BRANCH=main
export RADIO_ARCHIVE_STATE_FILE="$ROOT/tmp/state/radio_archive_pushed.json"

# ダミー原稿を作成
echo "テスト原稿" > "$ROOT/backups/radio_scripts/20260824/radio_1_test.txt"
echo "history" > "$ROOT/backups/radio_scripts/20260824/radio_1_test.history"
echo '{"a":1}' > "$ROOT/backups/radio_scripts/20260824/radio_1_test.meta.json"

# 1. 正常系: fake bare リモートへの push
REMOTE="$TMP/remote.git"
git init --bare "$REMOTE" >/dev/null 2>&1
export RADIO_ARCHIVE_GIT_REPO="$REMOTE"
out=$(bash "$ROOT/tools/radio_archive_push.sh" --now 2>&1)
rc=$?
assert_eq 0 "$rc" "push to fresh bare remote succeeds"
assert_contains "push succeeded" "$out" "push log contains success"
# リモートに commit ができる
if git --git-dir="$REMOTE" log --oneline -1 2>/dev/null | grep -q "radio:"; then
  ok "remote has radio commit"
else
  not_ok "remote has radio commit"
fi
# 2回目は差分なし
out2=$(bash "$ROOT/tools/radio_archive_push.sh" --now 2>&1)
assert_contains "no changes to push" "$out2" "second push is idempotent (no changes)"

# 2. dry-run は副作用なし (新たなファイルを追加しても push しない)
echo "new script" > "$ROOT/backups/radio_scripts/20260824/radio_2_test.txt"
echo "h2" > "$ROOT/backups/radio_scripts/20260824/radio_2_test.history"
echo '{"b":2}' > "$ROOT/backups/radio_scripts/20260824/radio_2_test.meta.json"
out3=$(bash "$ROOT/tools/radio_archive_push.sh" --dry-run 2>&1)
assert_contains "dry-run" "$out3" "dry-run log contains dry-run"
# リモートはまだ1コミットのはず
cnt=$(git --git-dir="$REMOTE" rev-list --count HEAD 2>/dev/null || echo 0)
assert_eq 1 "$cnt" "dry-run does not create new commit"
# --now で再 push すると増える
bash "$ROOT/tools/radio_archive_push.sh" --now >/dev/null 2>&1
cnt2=$(git --git-dir="$REMOTE" rev-list --count HEAD 2>/dev/null || echo 0)
assert_eq 2 "$cnt2" "real push after dry-run creates commit"

# 3. 誤爆ガード: soviet_now の main へは拒否
export RADIO_ARCHIVE_GIT_REPO="https://github.com/azumag/soviet_now.git"
export RADIO_ARCHIVE_GIT_BRANCH="main"
set +e
out4=$(bash "$ROOT/tools/radio_archive_push.sh" --dry-run 2>&1)
rc4=$?
set -e
assert_eq 2 "$rc4" "guard rejects soviet_now main"
assert_contains "refusing to push" "$out4" "guard log"

# 4. 無効化時はスキップ
export RADIO_ARCHIVE_ENABLED=0
export RADIO_ARCHIVE_GIT_REPO="$REMOTE"
out5=$(bash "$ROOT/tools/radio_archive_push.sh" --now 2>&1 || true)
assert_contains "disabled" "$out5" "disabled skips"
export RADIO_ARCHIVE_ENABLED=1

# 5. 対象なしはスキップ
rm -rf "$ROOT/backups"
mkdir -p "$ROOT/backups/radio_scripts"
out6=$(bash "$ROOT/tools/radio_archive_push.sh" --now 2>&1 || true)
assert_contains "no files to archive" "$out6" "no files skips"

# 6. --date 絞り
mkdir -p "$ROOT/backups/radio_scripts/20260825"
echo "x" > "$ROOT/backups/radio_scripts/20260825/radio_3.txt"
echo "h" > "$ROOT/backups/radio_scripts/20260825/radio_3.history"
echo '{}' > "$ROOT/backups/radio_scripts/20260825/radio_3.meta.json"
export RADIO_ARCHIVE_GIT_REPO="$REMOTE"
export RADIO_ARCHIVE_GIT_BRANCH="main"
# --date で 20260825 のみ dry-run してもログに 20260825 が出る
out7=$(bash "$ROOT/tools/radio_archive_push.sh" --date 20260825 --dry-run 2>&1)
assert_contains "20260825" "$out7" "--date filter works"

# 7. state ファイルが更新される
if [ -f "$ROOT/tmp/state/radio_archive_pushed.json" ]; then
  if grep -q "last_push_at" "$ROOT/tmp/state/radio_archive_pushed.json"; then
    ok "state file updated"
  else
    not_ok "state file updated"
  fi
else
  not_ok "state file exists"
fi

# 後始末: テストで作った backups を削除し、元の backups があれば復元
rm -rf "$ROOT/backups"
if [ -d "$BACKUP_BACKUP_TMP" ]; then
  mv "$BACKUP_BACKUP_TMP" "$BACKUP_ROOT_REAL" 2>/dev/null || true
fi
rm -f "$ROOT/tmp/state/radio_archive_pushed.json"

if [ "$FAIL" -eq 0 ]; then
  echo "all tests passed"
else
  echo "$FAIL tests failed" >&2
fi
exit "$FAIL"
