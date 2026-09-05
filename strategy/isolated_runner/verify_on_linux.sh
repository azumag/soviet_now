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

# --- 2. pytest: OS非依存部分 + Linux限定部分 (probeが通ったのでskipされないはず) ---
echo "--- 2. pytest tests/test_isolated_runner_rootless.py ---"
if python3 -m pytest tests/test_isolated_runner_rootless.py -v; then
	_ok "pytest tests/test_isolated_runner_rootless.py 全件成功 (Linux限定テストも実行されたはず。上のログでSKIPPEDが無いことを目視確認すること)"
else
	_ng "pytest tests/test_isolated_runner_rootless.py に失敗あり"
fi
echo

# --- 3. #34 の既存回帰テストが今も通ること ---
echo "--- 3. pytest tests/test_strategy_sandbox_no_host_exec.py (issue #34 回帰) ---"
if python3 -m pytest tests/test_strategy_sandbox_no_host_exec.py -v; then
	_ok "issue #34 の13件が今も成功"
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
before_ps_count=$(ps -e | wc -l)
receipt_out="$FORKBOMB_DIR/receipt.json"
timeout 60 python3 strategy/isolated_runner/run_isolated.py evaluate \
	--target "$FORKBOMB_DIR/strategy.py" --helpers "nonexistent_helpers" \
	--receipt-out "$receipt_out" --mode shadow >"$FORKBOMB_DIR/stdout.log" 2>&1
sleep 2
after_ps_count=$(ps -e | wc -l)
echo "process count before=$before_ps_count after=$after_ps_count (2秒後)"
if [ -f "$receipt_out" ]; then
	gate=$(python3 -c "import json; print(json.load(open('$receipt_out')).get('gate'))" 2>/dev/null)
	echo "gate=$gate"
	if [ "$gate" != "pass" ]; then
		_ok "fork bomb candidate は自動適用されなかった (gate=$gate)"
	else
		_ng "fork bomb candidate が pass 判定になった (異常)"
	fi
else
	_ng "fork bomb評価でreceiptが生成されなかった"
fi
if [ "$after_ps_count" -le $((before_ps_count + 5)) ]; then
	_ok "fork bomb後もhostのprocess数が急増していない (孤児process無しの弱い確認。詳細は手順5参照)"
else
	_ng "fork bomb後にhostのprocess数が明らかに増えている (孤児process疑い): before=$before_ps_count after=$after_ps_count"
fi
rm -rf "$FORKBOMB_DIR"
echo

# --- 5. 100回実行して孤児process/mount/tmp artifactが残らないことを確認 ---
echo "--- 5. 100回実行ループ (orphan process / mount / tmp artifact diff) ---"
N=${VERIFY_ISOLATED_RUNNER_ITERATIONS:-100}
mounts_before=$(mount | wc -l)
procs_before=$(ps -e | wc -l)
tmp_before=$(find /tmp -maxdepth 1 2>/dev/null | wc -l)

LOOP_DIR=$(mktemp -d)
cat >"$LOOP_DIR/strategy.py" <<'PYEOF'
import math


def decide(game_state, analysis):
    pieces = game_state.get("pieces", []) if isinstance(game_state, dict) else []
    return {"x": round(math.log(len(pieces) + 1), 4), "reason": "loop-iteration-fixture"}
PYEOF

loop_failures=0
for i in $(seq 1 "$N"); do
	rc_out="$LOOP_DIR/receipt_$i.json"
	if ! timeout 60 python3 strategy/isolated_runner/run_isolated.py evaluate \
		--target "$LOOP_DIR/strategy.py" --helpers "strategy_helpers" \
		--receipt-out "$rc_out" --mode shadow >/dev/null 2>&1; then
		: # shadow modeなのでgate自体はfailで正常。ここでの失敗はrcのUnix exit statusのみ見る
	fi
	gate=$(python3 -c "import json; print(json.load(open('$rc_out')).get('gate',''))" 2>/dev/null)
	if [ "$gate" != "pass" ]; then
		loop_failures=$((loop_failures + 1))
	fi
	rm -f "$rc_out"
done
rm -rf "$LOOP_DIR"

sleep 2
mounts_after=$(mount | wc -l)
procs_after=$(ps -e | wc -l)
tmp_after=$(find /tmp -maxdepth 1 2>/dev/null | wc -l)

echo "iterations=$N gate!=pass count=$loop_failures (shadow modeなので全件fail-closedのはず。$N と一致していれば正常)"
echo "mounts before=$mounts_before after=$mounts_after"
echo "processes before=$procs_before after=$procs_after"
echo "tmp entries before=$tmp_before after=$tmp_after"

if [ "$loop_failures" -eq "$N" ]; then
	_ok "$N 回全てshadow modeでfail-closedのまま (期待通り。enforce切替前の既定挙動)"
else
	_ng "shadow modeなのに一部が pass 扱いになった、またはreceipt読み取りに失敗した ($loop_failures/$N)"
fi
if [ "$mounts_after" -le "$mounts_before" ]; then
	_ok "mount leakなし (before=$mounts_before after=$mounts_after)"
else
	_ng "mount数が増加している可能性 (before=$mounts_before after=$mounts_after) — 手動で 'mount' の差分を確認すること"
fi
if [ "$procs_after" -le $((procs_before + 5)) ]; then
	_ok "orphan process無し (before=$procs_before after=$procs_after)"
else
	_ng "process数が増加している可能性 (before=$procs_before after=$procs_after) — 'ps -ef' で残存プロセスを確認すること"
fi
if [ "$tmp_after" -le "$tmp_before" ]; then
	_ok "/tmp 直下のエントリ数が増えていない (before=$tmp_before after=$tmp_after)"
else
	_ng "/tmp 直下のエントリが増えている可能性 (before=$tmp_before after=$tmp_after) — 手動で新規ファイルを確認すること"
fi
echo

echo "=== SUMMARY: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
