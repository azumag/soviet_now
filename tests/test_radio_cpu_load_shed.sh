#!/usr/bin/env bash
# CPU 負荷シェディングの振る舞いを検証する (docich#2825)。
#  - CPU >= 95% で新規ラジオ生成をブロック
#  - ヒステリシス: ブロック中は 90% 以上で継続、90% 未満で自動再開
#  - fail-open: 取得値が数値でなければ生成を止めない
#  - 無効化スイッチで常に生成を許可
#  - 抑止は生成ゲート (schedule_nonessential_audio_jobs) だけに掛かる
# 実コードをそのまま抽出して source する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHED_SRC="$ROOT/broadcast/scheduler.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# 外部から注入される可能性のある CPU 系の環境変数は全て解除する
for _v in $(env | sed -n 's/^\(CPU_[A-Z_]*\|HIGH_CPU_[A-Z_]*\|RADIO_CPU_[A-Z_]*\|HOST_CPU_[A-Z_]*\)=.*/\1/p'); do
	unset "$_v"
done

# --- 実関数を抽出して source ---
sed -n '/^_radio_generation_blocked_by_cpu_load()/,/^}/p' "$SCHED_SRC" >"$TMP/fn_cpu.sh"

TMP_MARKERS_DIR="$TMP/markers"
mkdir -p "$TMP_MARKERS_DIR"
log() { printf 'LOG: %s\n' "$*" >>"$TMP/log.txt"; }
: >"$TMP/log.txt"
. "$TMP/fn_cpu.sh"

MARKER="$TMP_MARKERS_DIR/.radio_cpu_load_shed_active"
reset() { rm -f "$TMP_MARKERS_DIR"/.radio_* "$TMP/log.txt"; : >"$TMP/log.txt"; }

# 判定を 1 回実行する。引数: 注入する CPU 使用率
# 戻り値は関数の戻り値 (0 = ブロック, 1 = 許可)
check() { _radio_generation_blocked_by_cpu_load "$1"; }

# ============================================================
# 開始しきい値: 95% 以上でブロック、94% 以下は許可
# ============================================================
reset
if check 95; then ok "start: 95% (boundary) blocks generation"; else not_ok "start: 95% should block"; fi
[ -f "$MARKER" ] && ok "start: marker set while blocked" || not_ok "start: marker missing while blocked"
grep -q '新規ラジオ生成を抑制' "$TMP/log.txt" && ok "start: skip is logged (visible, not silent)" || not_ok "start: skip not logged"

reset
if check 94; then not_ok "no-start: 94% must not block"; else ok "no-start: 94% allows generation"; fi
[ -f "$MARKER" ] && not_ok "no-start: marker set at 94%" || ok "no-start: no marker at 94%"

# ============================================================
# ヒステリシス: ブロック中は 90% 以上で継続、90% 未満で再開
# ============================================================
reset
check 96 >/dev/null
if check 92; then ok "hysteresis: 92% keeps blocking while active"; else not_ok "hysteresis: 92% should keep blocking"; fi
if check 90; then ok "hysteresis: 90% (boundary) keeps blocking while active"; else not_ok "hysteresis: 90% should keep blocking"; fi
if check 89; then not_ok "resume: 89% should resume"; else ok "resume: 89% resumes generation"; fi
[ -f "$MARKER" ] && not_ok "resume: marker not cleared" || ok "resume: marker cleared"
grep -q '新規ラジオ生成を再開\|ラジオ生成を再開' "$TMP/log.txt" && ok "resume: resume is logged" || not_ok "resume: resume not logged"

reset
if check 92; then not_ok "no-hysteresis: 92% without prior block must allow"; else ok "no-hysteresis: 92% allows when not already blocked"; fi

# ============================================================
# fail-open: 数値でない値は CPU 0 扱いで生成を止めない
# ============================================================
reset
if check "not-a-number"; then not_ok "fail-open: non-numeric value blocked generation"; else ok "fail-open: non-numeric value allows generation"; fi

# ============================================================
# 無効化スイッチ: 明示 0 なら 99% でも許可
# ============================================================
reset
if CPU_LOAD_SHED_ENABLED=0 _radio_generation_blocked_by_cpu_load 99; then
	not_ok "disable: explicit 0 must bypass the gate"
else
	ok "disable: CPU_LOAD_SHED_ENABLED=0 allows generation at 99%"
fi

# ============================================================
# 生成のみ抑止: ゲートは schedule_nonessential_audio_jobs の中だけにあり、
# 再生側の関数からは呼ばれていない
# ============================================================
sed -n '/^schedule_nonessential_audio_jobs()/,/^}/p' "$SCHED_SRC" >"$TMP/fn_sched.sh"
if grep -q '_radio_generation_blocked_by_cpu_load' "$TMP/fn_sched.sh"; then
	ok "scope: gate is wired into schedule_nonessential_audio_jobs"
else
	not_ok "scope: gate not wired into schedule_nonessential_audio_jobs"
fi
if grep -n '_radio_generation_blocked_by_cpu_load' "$SCHED_SRC" | grep -v -e '^[0-9]*:_radio_generation_blocked_by_cpu_load()' -e 'if _radio_generation_blocked_by_cpu_load' >/dev/null; then
	not_ok "scope: gate referenced outside the generation gate call site"
else
	ok "scope: gate has exactly one call site (generation only)"
fi

exit "$FAIL"
