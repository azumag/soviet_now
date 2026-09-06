#!/usr/bin/env bash
# strategy/isolated_runner/verify_on_linux.sh - issue #35 Linux実機検証スクリプト
#
# **このスクリプトは開発者/運用者が Linux (本番相当: Ubuntu 24.04 arm64 を想定) の
# 上で手動で実行するためのものである。macOS では動かない (bwrap/unshare が無い)。
# このリポジトリのCI/agentが自動実行するものではなく、実行してもいない。**
#
# 目的 (issue #35 受入条件のうち、macOSでは検証できなかった項目):
#   - env読取・host file読書き・外部接続・subprocess・fork bombの各fixtureが
#     失敗しhost副作用0であること
#   - Linux production相当環境で100回実行し、孤児process/mount/tmp artifactが
#     残らないこと
#
# 使い方:
#   1. 本番VM (games/soviet_now の稼働checkoutと共有しない、専用の使い捨て
#      checkout/VM上で!) で `sudo apt-get install -y bubblewrap` を実行する
#      (これが _strategy_isolated_runner_available() を真にする一番簡単な方法)。
#   2. このリポジトリをclone (または `git worktree add`) し、このブランチを
#      checkoutする。
#   3. `bash strategy/isolated_runner/verify_on_linux.sh` を実行する。
#   4. 出力される PASS/FAIL サマリと、末尾の孤児process/mount/tmp差分を確認する。
#
# 本番VMへの反映やproduction checkoutへの影響は一切行わない
# (読み取り専用の検証と、実行専用ディレクトリ内の一時ファイルの作成/削除のみ)。

set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PASS=0
FAIL=0
_ok() { echo "[PASS] $*"; PASS=$((PASS + 1)); }
_ng() { echo "[FAIL] $*"; FAIL=$((FAIL + 1)); }

_run_test_file() {
	local test_file="$1"
	if python3 -c 'import pytest' >/dev/null 2>&1; then
		python3 -m pytest "$test_file" -v
	else
		python3 "$test_file" -v
	fi
}

echo "=== issue #35 rootless isolated runner: Linux実機検証 ==="
echo "REPO_ROOT=$REPO_ROOT"
uname -a
echo

# --- 0. 前提バイナリの確認 ---
if command -v bwrap >/dev/null 2>&1; then
	echo "backend candidate: bwrap ($(bwrap --version 2>&1))"
elif command -v unshare >/dev/null 2>&1 && command -v setpriv >/dev/null 2>&1; then
	echo "backend candidate: unshare+setpriv (bwrap未検出。本番では bubblewrap の導入を推奨)"
else
	_ng "bwrap も unshare+setpriv も見つからない。'sudo apt-get install -y bubblewrap' を実行してから再実行すること"
	echo
	echo "=== SUMMARY: PASS=$PASS FAIL=$FAIL ==="
	exit 1
fi
echo

# --- 1. probe: 実際にサンドボックスが機能するか ---
echo "--- 1. run_isolated.py probe ---"
probe_out=$(python3 strategy/isolated_runner/run_isolated.py probe 2>&1)
probe_rc=$?
echo "$probe_out"
if [ "$probe_rc" -eq 0 ]; then
	_ok "probe succeeded (uid dropped / network blocked / read-only root / /output writable)"
else
	_ng "probe failed (rc=$probe_rc) — 以降のテストは無意味なので中止する"
	echo
	echo "=== SUMMARY: PASS=$PASS FAIL=$FAIL ==="
	exit 1
fi
echo

# --- 2. OS非依存部分 + Linux限定部分 ---
echo "--- 2. tests/test_isolated_runner_rootless.py ---"
if _run_test_file tests/test_isolated_runner_rootless.py; then
	_ok "tests/test_isolated_runner_rootless.py 成功 (pytestが無ければunittest直実行)"
else
	_ng "tests/test_isolated_runner_rootless.py に失敗あり"
fi
echo

# --- 3. #34 の既存回帰テストが今も通ること ---
echo "--- 3. tests/test_strategy_sandbox_no_host_exec.py (issue #34 回帰) ---"
if _run_test_file tests/test_strategy_sandbox_no_host_exec.py; then
	_ok "issue #34 の回帰テスト成功"
else
	_ng "issue #34 の回帰テストが失敗した"
fi
echo

# --- 4. fork bomb fixture: 個別に強調して確認する ---
echo "--- 4. fork bomb fixture ---"
FORKBOMB_DIR=$(mktemp -d)
cat >"$FORKBOMB_DIR/strategy.py" <<'PYEOF'
import os


def decide(game_state, analysis):
    def _bomb():
        while True:
            try:
                os.fork()
            except Exception:
                break
    _bomb()
    return {"x": 0.0, "reason": "forkbomb-fixture"}
PYEOF
mkdir -p "$FORKBOMB_DIR/runner-tmp"
receipt_out="$FORKBOMB_DIR/receipt.json"
ISOLATED_RUNNER_TMP_BASE="$FORKBOMB_DIR/runner-tmp" timeout 60 python3 strategy/isolated_runner/run_isolated.py evaluate \
	--target "$FORKBOMB_DIR/strategy.py" --helpers "nonexistent_helpers" \
	--receipt-out "$receipt_out" --mode shadow >"$FORKBOMB_DIR/stdout.log" 2>&1
sleep 2
fork_process_refs=$(ps -eo args= | grep -F "$FORKBOMB_DIR" | grep -v grep | wc -l | tr -d ' ')
fork_tmp_entries=$(find "$FORKBOMB_DIR/runner-tmp" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
echo "fork bomb containment residual processes=$fork_process_refs tmp_entries=$fork_tmp_entries"
if [ -f "$receipt_out" ]; then
	gate=$(python3 -c "import json; print(json.load(open('$receipt_out')).get('gate'))" 2>/dev/null)
	echo "gate=$gate"
	case "$gate" in
	pass|fail) _ok "fork bomb containment produced a valid isolated receipt (gate=$gate; apply policy is tested separately)" ;;
	*) _ng "fork bomb containment receipt has invalid gate: $gate" ;;
	esac
else
	_ng "fork bomb containment produced no receipt"
fi
if [ "$fork_process_refs" -eq 0 ] && [ "$fork_tmp_entries" -eq 0 ]; then
	_ok "fork bomb containment left no runner-scoped process/tmp artifact"
else
	_ng "fork bomb containment leaked runner-scoped state (processes=$fork_process_refs tmp=$fork_tmp_entries)"
fi
rm -rf "$FORKBOMB_DIR"
echo

# --- 5. 100回実行して孤児process/mount/tmp artifactが残らないことを確認 ---
echo "--- 5. 100回実行ループ (orphan process / mount / tmp artifact diff) ---"
N=${VERIFY_ISOLATED_RUNNER_ITERATIONS:-100}
LOOP_DIR=$(mktemp -d)
mkdir -p "$LOOP_DIR/runner-tmp"
mounts_before=$(mount | grep -F "$LOOP_DIR" | wc -l | tr -d ' ')
procs_before=$(ps -eo args= | grep -F "$LOOP_DIR" | grep -v grep | wc -l | tr -d ' ')
tmp_before=$(find "$LOOP_DIR/runner-tmp" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
cat >"$LOOP_DIR/strategy.py" <<'PYEOF'
import math


def decide(game_state, analysis):
    pieces = game_state.get("pieces", []) if isinstance(game_state, dict) else []
    return {"x": round(math.log(len(pieces) + 1), 4), "reason": "loop-iteration-fixture"}
PYEOF

loop_passes=0
for i in $(seq 1 "$N"); do
	rc_out="$LOOP_DIR/receipt_$i.json"
	ISOLATED_RUNNER_TMP_BASE="$LOOP_DIR/runner-tmp" timeout 60 python3 strategy/isolated_runner/run_isolated.py evaluate \
		--target "$LOOP_DIR/strategy.py" --helpers "strategy_helpers" \
		--receipt-out "$rc_out" --mode shadow >/dev/null 2>&1 || true
	gate=$(python3 -c "import json; print(json.load(open('$rc_out')).get('gate',''))" 2>/dev/null)
	if [ "$gate" = "pass" ]; then
		loop_passes=$((loop_passes + 1))
	fi
	rm -f "$rc_out"
done

# receipt の gate=pass は「隔離評価に成功」を表す。shadow rollout で自動applyを
# 拒否する責務は一段上の shell wrapper にあるため、別々に検証する。
if (
	log() { :; }
	source strategy/sandbox.sh
	mkdir -p "$LOOP_DIR/state"
	TMP_STATE_DIR="$LOOP_DIR/state"
	ISOLATED_RUNNER_TMP_BASE="$LOOP_DIR/runner-tmp"
	SOREN_ISOLATED_RUNNER_MODE=shadow
	_strategy_isolated_runner_evaluate "$LOOP_DIR/strategy.py" "strategy_helpers"
); then
	_ng "shadow mode unexpectedly allowed automatic apply"
else
	_ok "shadow mode rejects automatic apply after successful isolated receipt"
fi

sleep 2
mounts_after=$(mount | grep -F "$LOOP_DIR" | wc -l | tr -d ' ')
procs_after=$(ps -eo args= | grep -F "$LOOP_DIR" | grep -v grep | wc -l | tr -d ' ')
tmp_after=$(find "$LOOP_DIR/runner-tmp" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')

echo "iterations=$N isolated gate=pass count=$loop_passes ($N と一致すれば全隔離評価成功)"
echo "runner-scoped mounts before=$mounts_before after=$mounts_after"
echo "runner-scoped processes before=$procs_before after=$procs_after"
echo "runner-scoped tmp entries before=$tmp_before after=$tmp_after"

if [ "$loop_passes" -eq "$N" ]; then
	_ok "$N 回全て隔離評価receiptがpass"
else
	_ng "隔離評価receiptがpassにならない反復あり ($loop_passes/$N)"
fi
if [ "$mounts_after" -eq "$mounts_before" ]; then
	_ok "runner-scoped mount leakなし (before=$mounts_before after=$mounts_after)"
else
	_ng "runner-scoped mount leak疑い (before=$mounts_before after=$mounts_after)"
fi
if [ "$procs_after" -eq "$procs_before" ]; then
	_ok "runner-scoped orphan process無し (before=$procs_before after=$procs_after)"
else
	_ng "runner-scoped orphan process疑い (before=$procs_before after=$procs_after)"
fi
if [ "$tmp_after" -eq "$tmp_before" ]; then
	_ok "runner-scoped tmp artifact leakなし (before=$tmp_before after=$tmp_after)"
else
	_ng "runner-scoped tmp artifact leak疑い (before=$tmp_before after=$tmp_after)"
fi
rm -rf "$LOOP_DIR"
echo

echo "=== SUMMARY: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
