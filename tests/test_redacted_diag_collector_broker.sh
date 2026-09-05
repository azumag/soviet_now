#!/usr/bin/env bash
# tests/test_redacted_diag_collector_broker.sh - docich#33
#
# Verifies (against a fixture "root", never the real repo/VM):
#   - allowlist済みcollectorから必要なservice state/log excerpt/hashを
#     取得できる (正常系)
#   - collectorが未知sourceやpath逸脱を拒否する (権限拒否のnegative path)
#   - collectorが書き出すsnapshotはtoken/secret/PIIをredactしている
#   - brokerは list/get の読み取り専用verbしか持たず、event_id/
#     evidence_refのpath traversalを拒否する
#   - brokerがコピーしたevidenceはread-only(0400)で、元snapshotは
#     書き換えられない
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'chmod -R u+rwx "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

COLLECTOR="$ROOT/lib/redacted_diag_collector.py"
BROKER="$ROOT/lib/redacted_diag_broker.py"

# ---------------------------------------------------------------------------
# fixture "root": stands in for the repo/host state the collector may read.
# Never the real repo.
# ---------------------------------------------------------------------------
FIXTURE_ROOT="$TMP/fixture_root"
mkdir -p "$FIXTURE_ROOT/logs" "$FIXTURE_ROOT/tmp/codex_bug_queue" "$FIXTURE_ROOT/.secret_area"
SENTINEL_SECRET="SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
SENTINEL_EMAIL="viewer.pii.sentinel@example.com"
cat >"$FIXTURE_ROOT/logs/chat_worker.log" <<EOF
2026-09-04 12:00:00 worker started
2026-09-04 12:00:05 auth: Bearer $SENTINEL_SECRET
2026-09-04 12:00:06 viewer contact leak $SENTINEL_EMAIL
2026-09-04 12:00:07 ERROR queue stalled
EOF
touch "$FIXTURE_ROOT/tmp/codex_bug_queue/a.json" "$FIXTURE_ROOT/tmp/codex_bug_queue/b.json" "$FIXTURE_ROOT/tmp/codex_bug_queue/c.json"
echo "print('x')" >"$FIXTURE_ROOT/strategy.py"
echo "$SENTINEL_SECRET" >"$FIXTURE_ROOT/.secret_area/real_credential.env"

SNAPSHOT_DIR="$TMP/snapshots"

# ---------------------------------------------------------------------------
# A. positive path: allowlisted sources can be collected
# ---------------------------------------------------------------------------
list_out=$(python3 "$COLLECTOR" --list)
check "printf '%s' \"\$list_out\" | grep -q 'chat_worker_log_tail'" \
	"collector --list にchat_worker_log_tailが含まれる"

collect_out=$(python3 "$COLLECTOR" --event-id evt1 --source chat_worker_log_tail --root "$FIXTURE_ROOT" --out-dir "$SNAPSHOT_DIR" 2>&1)
collect_rc=$?
check "[ \"$collect_rc\" -eq 0 ]" "allowlist済みsource(chat_worker_log_tail)のcollectが成功する"
check "[ -f \"$SNAPSHOT_DIR/evt1/chat_worker_log_tail.json\" ]" "snapshotファイルが書き出される"

python3 "$COLLECTOR" --event-id evt1 --source codex_bug_queue_depth --root "$FIXTURE_ROOT" --out-dir "$SNAPSHOT_DIR" >/dev/null 2>&1
depth_content=$(python3 -c "import json,sys; print(json.load(open('$SNAPSHOT_DIR/evt1/codex_bug_queue_depth.json'))['content'])")
check "[ \"$depth_content\" = \"3\" ]" "dir_count種別のcollectが実測件数3を返す (実測: $depth_content)"

python3 "$COLLECTOR" --event-id evt1 --source strategy_py_hash --root "$FIXTURE_ROOT" --out-dir "$SNAPSHOT_DIR" >/dev/null 2>&1
expected_hash=$(shasum -a 256 "$FIXTURE_ROOT/strategy.py" | awk '{print $1}')
hash_content=$(python3 -c "import json; print(json.load(open('$SNAPSHOT_DIR/evt1/strategy_py_hash.json'))['content'])")
check "[ \"$hash_content\" = \"$expected_hash\" ]" "file_hash種別のcollectが正しいsha256を返す"

python3 "$COLLECTOR" --event-id evt1 --source codex_bug_dispatch_last_ts --root "$FIXTURE_ROOT" --out-dir "$SNAPSHOT_DIR" >/dev/null 2>&1
unavailable_status=$(python3 -c "import json; print(json.load(open('$SNAPSHOT_DIR/evt1/codex_bug_dispatch_last_ts.json'))['status'])")
check "[ \"$unavailable_status\" = \"unavailable\" ]" \
	"存在しないfile_readはcrashせずstatus=unavailableを返す (実測: $unavailable_status)"

# ---------------------------------------------------------------------------
# B. redaction: snapshotにtoken/secret/PIIがそのまま残らない
# ---------------------------------------------------------------------------
snapshot_blob=$(cat "$SNAPSHOT_DIR/evt1/chat_worker_log_tail.json")
check "! printf '%s' \"\$snapshot_blob\" | grep -q \"$SENTINEL_SECRET\"" \
	"chat_worker_log_tail snapshotにsecret sentinelが平文で残らない"
check "! printf '%s' \"\$snapshot_blob\" | grep -q \"$SENTINEL_EMAIL\"" \
	"chat_worker_log_tail snapshotにviewer emailが平文で残らない"
check "printf '%s' \"\$snapshot_blob\" | grep -q 'worker started'" \
	"redaction対象外の通常ログ行はそのまま残る(過剰redactionでない)"

# ---------------------------------------------------------------------------
# C. negative path: 未知source・path逸脱の拒否
# ---------------------------------------------------------------------------
python3 "$COLLECTOR" --event-id evt1 --source "../../etc/passwd" --root "$FIXTURE_ROOT" --out-dir "$SNAPSHOT_DIR" >/dev/null 2>"$TMP/unknown_source.err"
check "[ $? -ne 0 ]" "allowlistにない source名は拒否される"
check "[ ! -e \"$SNAPSHOT_DIR/evt1/../../etc/passwd.json\" ]" "拒否時にsnapshotファイルが作られない"

# シミュレーション: allowlistエントリ自体が誤ってroot外を指していた場合でも
# containment checkで二重に拒否される (defense in depth)。
traversal_check=$(python3 - "$ROOT" "$FIXTURE_ROOT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lib import redacted_diag_collector as c
c.ALLOWLIST["_test_evil_traversal"] = {"kind": "file_read", "relpath": "../../../../etc/passwd"}
try:
    c.collect("_test_evil_traversal", root=Path(sys.argv[2]), event_id="evtX")
    print("VULNERABLE")
except c.CollectorError:
    print("BLOCKED")
PY
)
check "[ \"$traversal_check\" = \"BLOCKED\" ]" \
	"allowlistエントリのpath逸脱もcontainment checkでBLOCKされる (実測: $traversal_check)"

# ---------------------------------------------------------------------------
# D. broker: 正常系(list/get)
# ---------------------------------------------------------------------------
broker_list=$(python3 "$BROKER" list --snapshot-dir "$SNAPSHOT_DIR" --event-id evt1)
check "printf '%s' \"\$broker_list\" | grep -q 'chat_worker_log_tail'" \
	"broker listがcollect済みevidence_refを列挙する"
check "! printf '%s' \"\$broker_list\" | grep -q \"$SENTINEL_SECRET\"" \
	"broker listの出力にsecret sentinelが含まれない(メタデータのみ)"

DEST="$TMP/dest"
get_out=$(python3 "$BROKER" get --snapshot-dir "$SNAPSHOT_DIR" --event-id evt1 --evidence-ref chat_worker_log_tail --dest "$DEST")
check "[ $? -eq 0 ]" "broker getが正常系で成功する"
check "[ -f \"$DEST/chat_worker_log_tail.json\" ]" "broker getがdestにファイルをコピーする"
dest_perm=$(stat -f '%Lp' "$DEST/chat_worker_log_tail.json" 2>/dev/null || stat -c '%a' "$DEST/chat_worker_log_tail.json")
check "[ \"$dest_perm\" = \"400\" ]" "broker getのコピー先ファイルは0400(read-only) (実測: $dest_perm)"

# 元のsnapshotが書き換わっていない(copyfileであり、move/linkでない)ことを確認
check "[ -f \"$SNAPSHOT_DIR/evt1/chat_worker_log_tail.json\" ]" "broker get後も元snapshotが存在する(移動していない)"
orig_after=$(cat "$SNAPSHOT_DIR/evt1/chat_worker_log_tail.json")
check "[ \"\$orig_after\" = \"\$snapshot_blob\" ]" "元snapshotの内容がbroker get前後で不変"

# ---------------------------------------------------------------------------
# E. broker: negative path (write verbが存在しない、path traversal拒否)
# ---------------------------------------------------------------------------
python3 "$BROKER" set --snapshot-dir "$SNAPSHOT_DIR" >/dev/null 2>"$TMP/no_set.err"
check "[ $? -ne 0 ]" "brokerに'set'(write)verbが存在せず拒否される"
python3 "$BROKER" delete --snapshot-dir "$SNAPSHOT_DIR" >/dev/null 2>"$TMP/no_delete.err"
check "[ $? -ne 0 ]" "brokerに'delete'verbが存在せず拒否される"

python3 "$BROKER" get --snapshot-dir "$SNAPSHOT_DIR" --event-id evt1 --evidence-ref "../../../../etc/passwd" --dest "$DEST" >/dev/null 2>"$TMP/traversal1.err"
check "[ $? -eq 3 ]" "evidence_refのpath traversalは専用の拒否コード(3)で拒否される"
check "! find \"$DEST\" -newer \"$TMP\" 2>/dev/null | grep -q passwd" "traversal拒否時にdestへファイルが作られない"

python3 "$BROKER" get --snapshot-dir "$SNAPSHOT_DIR" --event-id "../../etc" --evidence-ref passwd --dest "$DEST" >/dev/null 2>"$TMP/traversal2.err"
check "[ $? -eq 3 ]" "event_idのpath traversalも拒否される"

python3 "$BROKER" get --snapshot-dir "$SNAPSHOT_DIR" --event-id evt1 --evidence-ref does_not_exist --dest "$DEST" >/dev/null 2>"$TMP/notfound.err"
check "[ $? -eq 4 ]" "存在しないevidence_refは専用の拒否コード(4)で拒否される"

python3 "$BROKER" get --snapshot-dir "$SNAPSHOT_DIR" --event-id evt1 --evidence-ref "/etc/passwd" --dest "$DEST" >/dev/null 2>"$TMP/abs.err"
check "[ $? -eq 3 ]" "絶対パスのevidence_refも拒否される"

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
