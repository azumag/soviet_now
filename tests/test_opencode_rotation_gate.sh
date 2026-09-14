#!/usr/bin/env bash
# opencode rotation gate (flock 共有/排他) の回帰テスト。ADR 0002 / issue #389。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if ! command -v flock >/dev/null 2>&1; then
	echo "SKIP: flock unavailable"
	exit 0
fi

export OPENCODE_ROTATION_GATE="$TMP/gate.lock"
export OPENCODE_ROTATION_GATE_WAIT_SEC=1
# shellcheck source=../lib/opencode_db_retention.sh
source "$ROOT/lib/opencode_db_retention.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

# 1. 空いていればコマンドを実行する
out=$(_opencode_rotation_gate_run echo ran)
[ "$out" = "ran" ] || fail "free run returned '$out'"

# 2. 無効化時はゲートを通さず実行する
out=$(OPENCODE_ROTATION_GATE_ENABLED=0 _opencode_rotation_gate_run echo ran_disabled)
[ "$out" = "ran_disabled" ] || fail "disabled run returned '$out'"

# 3. 排他ロック保持中は共有取得がタイムアウトし、コマンドを実行しない (fail-closed)
flock -x "$OPENCODE_ROTATION_GATE" -c 'sleep 5' &
locker=$!
sleep 0.5
_opencode_rotation_gate_run echo should_not_run >"$TMP/out.txt" 2>/dev/null
rc=$?
[ "$rc" = "124" ] || fail "expected rc=124 while blocked, got $rc"
[ ! -s "$TMP/out.txt" ] || fail "command ran while rotation held the gate"
kill "$locker" 2>/dev/null
wait "$locker" 2>/dev/null

# 4. 解放後は再び実行できる
sleep 0.3
out=$(_opencode_rotation_gate_run echo ran_after)
[ "$out" = "ran_after" ] || fail "post-release run returned '$out'"

# 5. retention は古いセッションのみ削除する (統合)
if command -v python3 >/dev/null 2>&1; then
	db="$TMP/opencode.db"
	python3 - "$db" <<'PY'
import sqlite3, sys, time
con = sqlite3.connect(sys.argv[1])
con.executescript("create table session(id text primary key, time_created integer);")
now = int(time.time() * 1000)
con.execute("insert into session values ('old', ?)", (now - 10 * 86400000,))
con.execute("insert into session values ('new', ?)", (now - 86400000,))
con.commit(); con.close()
PY
	ELOOP_LIB_DIR="$ROOT" _opencode_db_retention_rotate 3 "$db" >/dev/null 2>&1
	rows=$(python3 - "$db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(",".join(r[0] for r in con.execute("select id from session order by id")))
PY
)
	[ "$rows" = "new" ] || fail "retention kept wrong sessions: '$rows'"
fi

echo "PASS"
