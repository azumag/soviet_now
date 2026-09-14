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

# 1b. コマンドの stderr を潰さない (exec のリダイレクト永続化の回帰)
_opencode_rotation_gate_run sh -c 'echo gate_stderr_probe >&2' 2>"$TMP/gate_err.txt"
grep -q gate_stderr_probe "$TMP/gate_err.txt" || fail "gate swallowed command stderr"

# 2. 無効化時はゲートを通さず実行する
out=$(OPENCODE_ROTATION_GATE_ENABLED=0 _opencode_rotation_gate_run echo ran_disabled)
[ "$out" = "ran_disabled" ] || fail "disabled run returned '$out'"

# 3. 排他ロック保持中は共有取得がタイムアウトし、コマンドを実行しない (fail-closed)。
# `flock -c 'sleep'` は kill 後も子がlock fdを継承し得るため、Python本体が
# 直接lockを保持してready markerを書き、killで必ずfdを閉じるようにする。
python3 - "$OPENCODE_ROTATION_GATE" "$TMP/locker-ready" <<'PY' &
import fcntl
import pathlib
import sys
import time

with open(sys.argv[1], "a+") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    pathlib.Path(sys.argv[2]).write_text("ready", encoding="utf-8")
    time.sleep(30)
PY
locker=$!
for _ in $(seq 1 50); do
	[ -f "$TMP/locker-ready" ] && break
	sleep 0.02
done
[ -f "$TMP/locker-ready" ] || fail "exclusive locker did not become ready"
_opencode_rotation_gate_run echo should_not_run >"$TMP/out.txt" 2>/dev/null
rc=$?
[ "$rc" = "124" ] || fail "expected rc=124 while blocked, got $rc"
[ ! -s "$TMP/out.txt" ] || fail "command ran while rotation held the gate"
kill "$locker" 2>/dev/null
wait "$locker" 2>/dev/null

# 4. 解放後は再び実行できる
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

	# 6. default-XDG DB は全 writer が gate 参加するまで rotation しない。
	# soren91/text_ai.mjs / probe_free_slot.sh の direct opencode run が残る間、
	# 排他 flock だけでは新規 writer を止められないため #404 の競合を再発させない。
	export HOME="$TMP/home"
	default_db="$HOME/.local/share/opencode/opencode.db"
	mkdir -p "$(dirname "$default_db")"
	python3 - "$default_db" <<'PY'
import sqlite3, sys, time
con = sqlite3.connect(sys.argv[1])
con.executescript("create table session(id text primary key, time_created integer);")
now = int(time.time() * 1000)
con.execute("insert into session values ('old_default', ?)", (now - 10 * 86400000,))
con.commit(); con.close()
PY
	ELOOP_LIB_DIR="$ROOT" _opencode_db_retention_rotate 3 "$default_db" >"$TMP/default_out.txt" 2>"$TMP/default_err.txt"
	default_rows=$(python3 - "$default_db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(",".join(r[0] for r in con.execute("select id from session order by id")))
PY
)
	[ "$default_rows" = "old_default" ] || fail "default DB was mutated before all writers were gated: '$default_rows'"
	grep -q 'default DB has ungated writers; skip rotation' "$TMP/default_err.txt" || fail "default DB skip reason missing"

	# Explicit enable is reserved for the follow-up that gates every default-XDG writer.
	OPENCODE_DEFAULT_DB_RETENTION_ENABLED=1 ELOOP_LIB_DIR="$ROOT" _opencode_db_retention_rotate 3 "$default_db" >/dev/null 2>&1
	default_rows=$(python3 - "$default_db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(",".join(r[0] for r in con.execute("select id from session order by id")))
PY
)
	[ -z "$default_rows" ] || fail "explicitly enabled default DB retention did not prune old session: '$default_rows'"
fi

echo "PASS"
