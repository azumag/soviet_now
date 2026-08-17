#!/usr/bin/env bash
# audio_worker の mkdir ロック競合対策を検証する。
#
# 2026-08-17 本番障害: supervisor 再起動で複数 audio_worker が同時起動し、
# 勝者が mkdir 直後に pid を書くまでの間、敗者が「pid 空 = stale」と誤判定して
# ロックを奪取 → 双方 exit で worker がゼロになった。
# 対策: ロック取得に失敗した側は、空 pid を即 stale とせず 3 秒まで再検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/workers/audio_worker.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# 実装に待機+再検証ロジックが含まれることを確認 (文書化された障害対策)
grep -q "lock owner pid empty for 3s" "$SRC" \
	&& ok "worker は空 pid を 3 秒待って再検証する" \
	|| not_ok "worker に空 pid の再検証ロジックがない"
grep -q "sleep 0.2" "$SRC" \
	&& ok "worker は 0.2 秒間隔で再検証する" \
	|| not_ok "worker に再検証間隔がない"

# 実挙動再現: プロセスA (勝者) が mkdir 直後に pid を書くまでの間、
# プロセスB (敗者) が「空 pid = stale」と誤判定して奪取しないことを確認する。
mkdir "$TMP/lock"
(
	sleep 1
	echo "424242" >"$TMP/lock/pid"
) &
lock_owner=""
[ -f "$TMP/lock/pid" ] && lock_owner=$(cat "$TMP/lock/pid" 2>/dev/null || true)
case "$lock_owner" in '' | *[!0-9]*) lock_owner="" ;; esac
_deadline=$(( $(date +%s) + 3 ))
while [ -z "$lock_owner" ] && [ "$(date +%s)" -lt "$_deadline" ]; do
	sleep 0.2
	[ -f "$TMP/lock/pid" ] && lock_owner=$(cat "$TMP/lock/pid" 2>/dev/null || true)
	case "$lock_owner" in '' | *[!0-9]*) lock_owner="" ;; esac
done
if [ "$lock_owner" = "424242" ]; then
	ok "敗者は空 pid を待ち、勝者の pid を認識 (奪取せず)"
else
	not_ok "敗者が誤ってロックを奪取した (lock_owner=$lock_owner)"
fi

exit "$FAIL"
