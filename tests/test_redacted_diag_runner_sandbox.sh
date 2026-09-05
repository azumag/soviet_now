#!/usr/bin/env bash
# tests/test_redacted_diag_runner_sandbox.sh - docich#33
#
# End-to-end tests of diagnostics_runner.sh: the sandbox that actually runs
# a diagnostic script against broker-fetched evidence. Covers all five
# acceptance criteria at once, in the same run:
#   - agentからrepository/VM/process/credentialへ直接accessできない
#     (negative path, tests/fixtures/redacted_diag_hostile_probe.py)
#   - operatorが必要証拠を取得できる正常系 (positive path)
#   - timeout/crash時に本番状態を変更せず、operatorへ失敗理由を返す
#   - 出力はfinding/evidence_ref/confidence/recommended_actionのschemaに
#     限定 (invalid-schema probe)
#
# macOS note (see diagnostics_runner.sh _prepare_output_dir): this machine
# has no tmpfs, so every run here uses the plain-directory fallback -
# tmpfs_used is asserted to be the literal string "false" throughout, and
# that is the expected/known result on macOS, not a bug. The sandbox
# backend actually enforcing network/signal/file-read denial on this
# platform is macOS Seatbelt (sandbox-exec), which IS real OS-level
# enforcement (verified below with real syscalls, not a self-report), not a
# placeholder. The Linux path (bwrap/unshare) is exercised by
# diagnostics_runner.sh's OS-detection code but NOT executed by this test
# suite - there is no Linux host in this environment - and is UNVERIFIED.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cleanup() {
	kill "${LISTENER_PID:-0}" >/dev/null 2>&1 || true
	wait "${LISTENER_PID:-0}" >/dev/null 2>&1 || true
	chmod -R u+rwx "$TMP" 2>/dev/null || true
	rm -rf "$TMP"
}
trap cleanup EXIT

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

RUNNER="$ROOT/diagnostics_runner.sh"

if [ "$(uname -s)" != "Darwin" ]; then
	echo "SKIP: this suite's negative-path enforcement is verified via macOS sandbox-exec only" >&2
	echo "0 passed, 0 failed (skipped: not Darwin)"
	exit 0
fi
if ! command -v sandbox-exec >/dev/null 2>&1; then
	echo "SKIP: sandbox-exec not available on this host" >&2
	echo "0 passed, 0 failed (skipped: no sandbox-exec)"
	exit 0
fi

# ---------------------------------------------------------------------------
# repo-state-untouched helper: hash every file under the real repo (this
# worktree), excluding .git and the ephemeral tmp/ working area, before and
# after each destructive-looking scenario (timeout/crash/hostile).
# ---------------------------------------------------------------------------
_repo_state_hash() {
	find "$ROOT" -type f -not -path '*/.git/*' -not -path "$ROOT/tmp/*" -print0 2>/dev/null \
		| sort -z \
		| xargs -0 shasum -a 256 2>/dev/null \
		| shasum -a 256 \
		| awk '{print $1}'
}

# ---------------------------------------------------------------------------
# A. positive path: operator gets a schema-valid finding traceable to a real
#    evidence_ref, from a snapshot never touched directly by the script.
# ---------------------------------------------------------------------------
SNAP_A="$TMP/snapshot_a"
mkdir -p "$SNAP_A/evtA"
cat >"$SNAP_A/evtA/chat_worker_log_tail.json" <<'JSON'
{"event_id":"evtA","source":"chat_worker_log_tail","kind":"log_tail","status":"ok","captured_at":1,"evidence_ref":"chat_worker_log_tail","content":["worker started fine","ERROR queue stalled"],"content_sha256":"abc"}
JSON

before_hash_a="$(_repo_state_hash)"
report_a="$TMP/report_a.json"
work_root_a="$TMP/runs_a"
out_a=$(bash "$RUNNER" run \
	--event-id evtA --snapshot-dir "$SNAP_A" \
	--script "$ROOT/lib/redacted_diag_sample_check.py" \
	--report-out "$report_a" --evidence-ref chat_worker_log_tail \
	--work-root "$work_root_a" 2>&1)
rc_a=$?
after_hash_a="$(_repo_state_hash)"

check "[ \"$rc_a\" -eq 0 ]" "正常系: runnerが成功終了する (rc=$rc_a)"
check "[ -f \"$report_a\" ]" "正常系: report-outファイルが書かれる"
status_a=$(python3 -c "import json; print(json.load(open('$report_a'))['status'])" 2>/dev/null)
check "[ \"$status_a\" = \"ok\" ]" "正常系: report.status=ok (実測: $status_a)"
ref_a=$(python3 -c "import json; print(json.load(open('$report_a'))['findings'][0]['evidence_ref'])" 2>/dev/null)
check "[ \"$ref_a\" = \"chat_worker_log_tail\" ]" "正常系: findingがfetchした実evidence_refを指す(追跡可能) (実測: $ref_a)"
keys_a=$(python3 -c "import json; print(sorted(json.load(open('$report_a'))['findings'][0].keys()))" 2>/dev/null)
check "[ \"$keys_a\" = \"['confidence', 'evidence_ref', 'finding', 'recommended_action']\" ]" \
	"正常系: findingのkeyがschemaちょうど4つ (実測: $keys_a)"
tmpfs_a=$(python3 -c "import json; print(json.load(open('$report_a'))['tmpfs_used'])" 2>/dev/null)
check "[ \"$tmpfs_a\" = \"False\" ]" \
	"macOS代替: tmpfs_used=false (macOSにtmpfsが無いための既知のフォールバック、実測: $tmpfs_a)"
check "[ ! -d \"$work_root_a\" ] || [ -z \"\$(ls -A \"$work_root_a\" 2>/dev/null)\" ]" \
	"正常系: run終了後にephemeral run_dir(evidence copyを含む)が残っていない"
check "[ \"\$before_hash_a\" = \"\$after_hash_a\" ]" "正常系: 実行前後でリポジトリの実ファイルが一切変化しない"

# ---------------------------------------------------------------------------
# B. negative path: agent (script) cannot reach repository/VM state,
#    credentials, network, or send a signal to a real process - even when
#    the script itself is the one trying (simulating prompt injection /
#    a compromised diagnostic script), not merely a well-behaved caller.
# ---------------------------------------------------------------------------
mkdir -p "$ROOT/tmp"
REPO_SENTINEL="$ROOT/tmp/.diag_test_repo_secret_$$.txt"
CRED_SENTINEL="$ROOT/tmp/.diag_test_credential_$$.env"
FOREIGN_WRITE="$ROOT/tmp/.diag_test_foreign_write_$$.txt"
REPO_SECRET_VALUE="REPO_SECRET_SENTINEL_negtest_$$"
CRED_SECRET_VALUE="CREDENTIAL_SENTINEL_negtest_$$"
echo "$REPO_SECRET_VALUE" >"$REPO_SENTINEL"
echo "$CRED_SECRET_VALUE" >"$CRED_SENTINEL"
rm -f "$FOREIGN_WRITE"
cleanup_negative_fixtures() { rm -f "$REPO_SENTINEL" "$CRED_SENTINEL" "$FOREIGN_WRITE"; }
trap 'cleanup_negative_fixtures; cleanup' EXIT

python3 - "$TMP/canary_result.log" >"$TMP/canary_port.txt" 2>"$TMP/canary.err" <<'PY' &
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.listen(1)
print(port, flush=True)
s.settimeout(6)
try:
    conn, addr = s.accept()
    with open(sys.argv[1], "w") as f:
        f.write("ACCEPTED\n")
except socket.timeout:
    with open(sys.argv[1], "w") as f:
        f.write("NO_CONNECTION\n")
PY
LISTENER_PID=$!
sleep 0.5
CANARY_PORT=$(cat "$TMP/canary_port.txt" 2>/dev/null)
check "[ -n \"$CANARY_PORT\" ]" "negative path: canary TCP listenerが起動しportを確保できた"
check "kill -0 \"$LISTENER_PID\" 2>/dev/null" "negative path: canary process(署名対象)が実行中"

SNAP_H="$TMP/snapshot_hostile"
mkdir -p "$SNAP_H/evtHostile"
python3 - "$SNAP_H/evtHostile/config.json" "$REPO_SENTINEL" "$CRED_SENTINEL" "$CANARY_PORT" "$LISTENER_PID" "$FOREIGN_WRITE" <<'PY'
import json, sys
out, repo_s, cred_s, port, pid, fw = sys.argv[1:7]
data = {
    "event_id": "evtHostile", "source": "config", "kind": "test_config", "status": "ok",
    "captured_at": 1, "evidence_ref": "config",
    "content": {
        "repo_sentinel_path": repo_s,
        "credential_sentinel_path": cred_s,
        "canary_port": int(port),
        "canary_pid": int(pid),
        "foreign_write_path": fw,
    },
    "content_sha256": "n/a",
}
with open(out, "w") as f:
    json.dump(data, f)
PY

before_hash_h="$(_repo_state_hash)"
report_h="$TMP/report_hostile.json"
work_root_h="$TMP/runs_h"
DIAG_RUNNER_KEEP_RUN_DIR=1 DIAG_RUNNER_WORK_ROOT="$work_root_h" \
	bash "$RUNNER" run \
	--event-id evtHostile --snapshot-dir "$SNAP_H" \
	--script "$ROOT/tests/fixtures/redacted_diag_hostile_probe.py" \
	--report-out "$report_h" --evidence-ref config \
	>"$TMP/hostile_stdout.txt" 2>&1
rc_h=$?
after_hash_h="$(_repo_state_hash)"

run_dir_h=$(find "$work_root_h" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)
self_report=$(cat "$run_dir_h/stdout.json" 2>/dev/null)

check "printf '%s' \"\$self_report\" | grep -q '\"repo_read_blocked\": true'" \
	"negative: 悪意scriptからの repository state 直接read が拒否される"
check "printf '%s' \"\$self_report\" | grep -q '\"credential_read_blocked\": true'" \
	"negative: 悪意scriptからの credential 直接read が拒否される"
check "printf '%s' \"\$self_report\" | grep -q '\"network_blocked\": true'" \
	"negative: 悪意scriptからの outbound network(loopback含む)が拒否される"
check "printf '%s' \"\$self_report\" | grep -q '\"signal_blocked\": true'" \
	"negative: 悪意scriptからの process signal(実在processへ)が拒否される"
check "printf '%s' \"\$self_report\" | grep -q '\"foreign_write_blocked\": true'" \
	"negative: output dir外への書き込みが拒否される"
check "printf '%s' \"\$self_report\" | grep -q '\"output_write_ok\": true'" \
	"negative: output dir内への書き込みは許可される(sandboxが壊れているだけではない証拠)"

# canary process still-alive must be checked BEFORE its own accept()
# timeout elapses and it exits on its own - that natural exit is unrelated
# to whether our (blocked) signal attempt had any effect.
check "kill -0 \"$LISTENER_PID\" 2>/dev/null" "negative: canary processはsignal試行直後も生存(signalが実効しなかった証拠)"

sleep 6
canary_result=$(cat "$TMP/canary_result.log" 2>/dev/null)
check "[ \"$canary_result\" = \"NO_CONNECTION\" ]" \
	"negative: sandbox外の独立canary listenerが実測で0接続 (実測: ${canary_result:-<none>})"
check "[ ! -e \"$FOREIGN_WRITE\" ]" "negative: sandbox外のforeign write先ファイルが実際に作られていない"

report_h_blob=$(cat "$report_h" 2>/dev/null)
check "! printf '%s' \"\$report_h_blob\" | grep -q \"$REPO_SECRET_VALUE\"" \
	"negative: 最終reportにrepo secretがそもそも含まれない(schema違反としてforwardされない)"
check "! printf '%s' \"\$report_h_blob\" | grep -q \"$CRED_SECRET_VALUE\"" \
	"negative: 最終reportにcredential secretが含まれない"
status_h=$(python3 -c "import json; print(json.load(open('$report_h'))['status'])" 2>/dev/null)
check "[ \"$status_h\" = \"failed\" ]" \
	"negative: 悪意scriptの出力はschema違反としてfailure報告になる (実測status: $status_h)"
check "[ \"\$before_hash_h\" = \"\$after_hash_h\" ]" \
	"negative: 攻撃試行の実行前後でリポジトリの実ファイルが一切変化しない"

kill "$LISTENER_PID" >/dev/null 2>&1 || true
wait "$LISTENER_PID" >/dev/null 2>&1 || true
cleanup_negative_fixtures
trap cleanup EXIT

# ---------------------------------------------------------------------------
# C. timeout: script hangs forever -> runner kills it, reports timeout,
#    does not fabricate a success, and leaves nothing behind.
# ---------------------------------------------------------------------------
SNAP_T="$TMP/snapshot_t"
mkdir -p "$SNAP_T/evtT"
cat >"$SNAP_T/evtT/chat_worker_log_tail.json" <<'JSON'
{"event_id":"evtT","source":"chat_worker_log_tail","kind":"log_tail","status":"ok","captured_at":1,"evidence_ref":"chat_worker_log_tail","content":["ok"],"content_sha256":"x"}
JSON
report_t="$TMP/report_t.json"
work_root_t="$TMP/runs_t"
before_hash_t="$(_repo_state_hash)"
bash "$RUNNER" run --event-id evtT --snapshot-dir "$SNAP_T" \
	--script "$ROOT/tests/fixtures/redacted_diag_timeout_probe.py" \
	--report-out "$report_t" --evidence-ref chat_worker_log_tail \
	--work-root "$work_root_t" --time-limit 2 >/dev/null 2>&1
rc_t=$?
after_hash_t="$(_repo_state_hash)"
status_t=$(python3 -c "import json; print(json.load(open('$report_t'))['status'])" 2>/dev/null)
reason_t=$(python3 -c "import json; print(json.load(open('$report_t'))['reason'])" 2>/dev/null)
check "[ \"$rc_t\" -ne 0 ]" "timeout: runnerが非ゼロで終了する"
check "[ \"$status_t\" = \"failed\" ]" "timeout: report.status=failed (実測: $status_t)"
check "printf '%s' \"\$reason_t\" | grep -qi 'timed out'" "timeout: 失敗理由にtimeoutが明記される (実測: $reason_t)"
check "[ \"\$before_hash_t\" = \"\$after_hash_t\" ]" "timeout: 実行前後でリポジトリの実ファイルが変化しない"
check "[ ! -d \"$work_root_t\" ] || [ -z \"\$(ls -A \"$work_root_t\" 2>/dev/null)\" ]" "timeout: ephemeral run_dirが残らない"

# ---------------------------------------------------------------------------
# D. crash: script exits non-zero -> failure report, no fabricated success.
# ---------------------------------------------------------------------------
report_c="$TMP/report_c.json"
bash "$RUNNER" run --event-id evtT --snapshot-dir "$SNAP_T" \
	--script "$ROOT/tests/fixtures/redacted_diag_crash_probe.py" \
	--report-out "$report_c" --evidence-ref chat_worker_log_tail \
	--work-root "$TMP/runs_c" >/dev/null 2>&1
rc_c=$?
status_c=$(python3 -c "import json; print(json.load(open('$report_c'))['status'])" 2>/dev/null)
reason_c=$(python3 -c "import json; print(json.load(open('$report_c'))['reason'])" 2>/dev/null)
check "[ \"$rc_c\" -ne 0 ]" "crash: runnerが非ゼロで終了する"
check "[ \"$status_c\" = \"failed\" ]" "crash: report.status=failed (実測: $status_c)"
check "printf '%s' \"\$reason_c\" | grep -q 'exited with code 7'" "crash: 失敗理由に元の終了コードが記録される (実測: $reason_c)"

# ---------------------------------------------------------------------------
# E. invalid schema: near-miss output (extra key) is rejected, not forwarded.
# ---------------------------------------------------------------------------
report_s="$TMP/report_s.json"
bash "$RUNNER" run --event-id evtT --snapshot-dir "$SNAP_T" \
	--script "$ROOT/tests/fixtures/redacted_diag_invalid_schema_probe.py" \
	--report-out "$report_s" --evidence-ref chat_worker_log_tail \
	--work-root "$TMP/runs_s" >/dev/null 2>&1
status_s=$(python3 -c "import json; print(json.load(open('$report_s'))['status'])" 2>/dev/null)
check "[ \"$status_s\" = \"failed\" ]" "invalid-schema: 余分なkeyを含む出力はfailureになる (実測: $status_s)"
# 拒否されたkey名(shell_command)がreasonに出るのは正しい運用者向け診断情報。
# セキュリティ上重要なのは、そのkeyの"値"(悪意あるcode-change的な中身)が
# 転記されないこと。
check "! grep -q 'rm -rf' \"$report_s\"" "invalid-schema: 拒否された出力の値(code-change的な中身)がreportに転記されない"

# ---------------------------------------------------------------------------
# F. output size limit: oversized stdout is bounded, not silently accepted.
# ---------------------------------------------------------------------------
report_b="$TMP/report_b.json"
bash "$RUNNER" run --event-id evtT --snapshot-dir "$SNAP_T" \
	--script "$ROOT/tests/fixtures/redacted_diag_bigoutput_probe.py" \
	--report-out "$report_b" --evidence-ref chat_worker_log_tail \
	--work-root "$TMP/runs_b" --max-output-bytes 4096 >/dev/null 2>&1
status_b=$(python3 -c "import json; print(json.load(open('$report_b'))['status'])" 2>/dev/null)
report_b_size=$(wc -c <"$report_b" | tr -d ' ')
check "[ \"$status_b\" = \"failed\" ]" "output-limit: 上限を超える出力はfailureになる (実測: $status_b)"
check "[ \"$report_b_size\" -lt 100000 ]" "output-limit: 最終reportファイル自体は肥大化しない (実測バイト数: $report_b_size)"

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
