# lib/supervisor_improve_gate.sh - soren_loop respawn gate vs improvement exclusion.
#
# 概念 (Issue #253):
# 「新しい改善ジョブを開始してはいけない」と「gameplay loop を実行しては
# いけない」は別概念である。improve.lock の存在だけを gameplay 停止の根拠に
# してはならない。
#
# gameplay_blocked_by_improvement は、gameplay/runtime mutation と競合する
# active な improvement が現在実行中のときだけ rc=0 (block) を返す。
# failed_no_apply 後の retry/backoff 待機 (running=false) では rc=1 (allow) し、
# supervisor は soren_loop を respawn できる。soren_loop 自体は backoff 存在下で
# プレイを継続する (lock+backoffなし のときだけ PAUSE するため)。
#
# fail-safe: state 破損・判定不能 → block。live な改善プロセスの有無が主眼で、
# stale な running 宣言だけが残っていても gameplay を永久停止しない
# (鮮度は IMPROVE_GATE_FRESH_SEC、既定1800秒)。
#
# 依存: strategy/improve.sh の _wildcard_parallel_active / _is_live_improve_pid
# を使う。未 source の場合は command -v で検出して安全側 (block) に倒す。
# pause marker (operator/lifecycle) はここでは見ない。pause 抑止は
# start_all.sh の _worker_paused が別契約として扱う。

# gameplay_blocked_by_improvement: 標準出力に "block <reason>" / "allow <reason>"。
gameplay_blocked_by_improvement() {
	local lock="${IMPROVE_LOCK_FILE:-tmp/improve.lock}"
	local state="${IMPROVE_STATE_FILE:-tmp/state/improve_state.json}"
	local wildcard="${WILDCARD_PARALLEL_STATUS_FILE:-${TMP_STATE_DIR:-tmp/state}/wildcard_parallel_status.json}"
	local fresh_sec="${IMPROVE_GATE_FRESH_SEC:-1800}"
	[ -f "$lock" ] || { echo "allow no_improve_lock"; return 1; }
	if _improve_gate_wildcard_active "$wildcard"; then
		echo "block wildcard_parallel_active"
		return 0
	fi
	IMPROVE_GATE_STATE="$state" IMPROVE_GATE_FRESH_SEC="$fresh_sec" \
		python3 - "$state" <<'PY' 2>/dev/null
import json
import os
import re
import subprocess
import sys
import time

IMPROVE_CMD_RE = r"eloop_improve(_runtime\.[^ ]+)?\.sh"

try:
    fresh_sec = int(os.environ.get("IMPROVE_GATE_FRESH_SEC", "1800") or "1800")
except Exception:
    fresh_sec = 1800


def _ps_table():
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,command="],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    rows = []
    for line in (out.stdout or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return rows


def _cmd_matches_improve(cmd):
    try:
        return re.search(IMPROVE_CMD_RE, cmd or "") is not None
    except Exception:
        return False


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except Exception:
        return False
    return True


path = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
except Exception as exc:
    print("block malformed_improve_state")
    raise SystemExit(0)

status = state.get("status")
phase = state.get("phase") or ""
pid = state.get("pid")
try:
    stamps = [int(float(state.get(k, 0) or 0)) for k in ("updated_at", "started_at")]
    stamps = [t for t in stamps if t > 0]
except Exception:
    stamps = []
fresh = bool(stamps) and (time.time() - max(stamps)) <= max(1, fresh_sec)

recorded_live = False
if pid not in (None, "", 0, "0"):
    try:
        alive = _pid_alive(pid)
    except Exception:
        alive = True
    if alive:
        matched = None
        for _pid, cmd in _ps_table():
            if _pid == int(pid):
                matched = _cmd_matches_improve(cmd)
                break
        # ps に載らないが kill 通過 / コマンド不明: 何者か分からないため安全側に倒す。
        recorded_live = matched if matched is not None else True
    else:
        recorded_live = False

live = recorded_live

if status in ("running", "manual"):
    if live:
        print("block active_improvement_running")
        raise SystemExit(0)
    if not phase:
        print("block running_phase_unknown")
        raise SystemExit(0)
    if fresh:
        print("block fresh_running_claim")
        raise SystemExit(0)
    print("allow stale_dead_running_claim")
    raise SystemExit(1)

if live:
    print("block unrecorded_live_improver")
    raise SystemExit(0)
print("allow improvement_idle")
raise SystemExit(1)
PY
}

_improve_gate_wildcard_active() {
	local status_file="${1:-}"
	[ -n "$status_file" ] && [ -f "$status_file" ] || return 1
	if command -v _wildcard_parallel_active >/dev/null 2>&1; then
		_wildcard_parallel_active
		return $?
	fi
	# strategy/improve.sh 未 source のフォールバック: 存在自体を安全側に倒す。
	return 0
}
