#!/usr/bin/env bash
# strategy/regression.sh check_regression の段階到達率ゲート (lost_turkmenistan/ukraine/kazakhstan_gate)
# が統計的に有意な後退でのみ発火することを検証する。
# - 2026-08-25 02:26 再現: v727 n=13, comp 優位, breach 0, T14 1/13 vs anchor 6/25, russia 0 vs 1 → 粛清しない
# - 2026-08-25 05:06 再現: n=100, comp -57 (breach 0), T11 41/43 vs 25/25, russia 2 vs 1 → 粛清しない
# - 真の劣化 (T14 0/40 vs 12/25, breach 0) → 従来どおり REGRESSION
# - anchor ソ連到達済み・current 未到達 (lost_soviet_path) → grace 対象外で REGRESSION
# - STAGE_GATE_STAT_ENABLED=0 / STAGE_GATE_NONINFERIOR_GRACE=0 で旧挙動 (粛清) が再現する
# check_regression の python heredoc を抽出して fixture 上で直接実行する (VM 状態に依存しない)。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }
assert_contains() {
  local needle="$1" haystack="$2" label="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then ok "$label"; else not_ok "$label (missing: $needle | got: $(printf '%s' "$haystack" | head -c 160))"; fi
}
assert_not_contains() {
  local needle="$1" haystack="$2" label="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then not_ok "$label (unexpected: $needle | got: $(printf '%s' "$haystack" | head -c 160))"; else ok "$label"; fi
}

# --- check_regression の heredoc python を抽出 ---
PYFILE="$TMP/check_regression.py"
awk '
  /^check_regression\(\) \{/ { infn = 1 }
  infn && !inpy && /<<'"'"'PY'"'"'$/ { inpy = 1; next }
  inpy && /^PY$/ { exit }
  inpy { print }
' "$ROOT/strategy/regression.sh" > "$PYFILE"
[ -s "$PYFILE" ] || { echo "not ok - heredoc extraction failed"; exit 1; }
python3 -W ignore -m py_compile "$PYFILE" || { echo "not ok - extracted python does not compile"; exit 1; }
ok "check_regression heredoc extracted ($(wc -l < "$PYFILE") lines)"

# --- fixture builder ---
# mk_fixture <dir> <cur_hash> <anchor_hash> key=value...
#   cur_scores / anc_scores: "base,n,spread" で決定的に生成 (score_i = base + (i*37 % spread))
#   cur_mt / anc_mt: "13x22,14x7,10x2" 形式の max_types
#   cur_russia anc_russia cur_soviet anc_soviet pad_top
mk_fixture() {
  local dir="$1" cur="$2" anc="$3"; shift 3
  python3 - "$dir" "$cur" "$anc" "$@" <<'PY'
import json, os, sys
d, cur, anc = sys.argv[1:4]
kv = dict(a.split("=", 1) for a in sys.argv[4:])
def scores(spec):
    base, n, spread = [int(x) for x in spec.split(",")]
    return [base + (i * 37) % spread for i in range(n)]
def mt(spec):
    out = []
    for chunk in spec.split(","):
        t, c = chunk.split("x"); out += [int(t)] * int(c)
    return out
def entry(prefix):
    sc = scores(kv[prefix + "_scores"]); m = mt(kv[prefix + "_mt"])
    r = int(kv.get(prefix + "_russia", "0")); s = int(kv.get(prefix + "_soviet", "0"))
    best = max(m + [0]); best = max(best, 15 if r > 0 else 0, 16 if s > 0 else 0)
    best = int(kv.get(prefix + "_best", best))  # sticky lifetime max を模擬する override
    return {"scores": sc, "max_types": m, "russia_count": r, "soviet_count": s, "best_max_type": best, "games_total": len(sc)}
rolling = {cur: entry("cur"), anc: entry("anc")}
os.makedirs(os.path.join(d, "tmp/state"), exist_ok=True)
for sub in ("strategy_versions/by_hash", "strategy_versions_archive/by_hash"):
    os.makedirs(os.path.join(d, sub), exist_ok=True)
open(os.path.join(d, "strategy_versions_archive/by_hash", anc + ".py"), "a").close()
open(os.path.join(d, "strategy_versions_archive/by_hash", cur + ".py"), "a").close()
for k in range(int(kv.get("pad_top", "0"))):  # current より上位の復元可能 hash (rank>7 を作る)
    h = "pad%09d" % k
    rolling[h] = {"scores": [20000 + 50 * k + (j % 5) * 100 for j in range(20)], "max_types": [13] * 20, "russia_count": 0, "soviet_count": 0, "best_max_type": 13, "games_total": 20}
    open(os.path.join(d, "strategy_versions_archive/by_hash", h + ".py"), "a").close()
json.dump(rolling, open(os.path.join(d, "tmp/state/rolling_scores.json"), "w"))
run = dict(rolling[cur]); run["hash"] = cur
json.dump(run, open(os.path.join(d, "tmp/state/current_strategy_run.json"), "w"))
import math
def metrics(xs):
    xs = sorted(xs); n = len(xs); mean = sum(xs) / n
    def q(p):
        pos = p * (n - 1); lo = int(math.floor(pos)); hi = min(lo + 1, n - 1); return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
    p25, p50 = q(0.25), q(0.5); std = math.sqrt(sum((x - mean) ** 2 for x in xs) / n) if n > 1 else 0.0
    lcb = mean - 1.28 * std / math.sqrt(n); return dict(comp=0.55 * p50 + 0.30 * p25 + 0.15 * lcb, p50=p50, p25=p25, lcb=lcb, n=n)
am = metrics(rolling[anc]["scores"]); cm = metrics(rolling[cur]["scores"])
a = rolling[anc]
json.dump(dict(hash=anc, comp=am["comp"], p50=am["p50"], p25=am["p25"], lcb=am["lcb"], n=am["n"], best_max_type=a["best_max_type"], russia_count=a["russia_count"], soviet_count=a["soviet_count"], updated_at=1787600000), open(os.path.join(d, "tmp/state/best_strategy_anchor.json"), "w"))
print("fixture cur comp=%.1f n=%d | anchor comp=%.1f n=%d" % (cm["comp"], cm["n"], am["comp"], am["n"]))
PY
}

run_check() {  # run_check <fixture_dir> <cur_hash>  (env で toggle を渡す)
  local dir="$1" cur="$2"
  ( cd "$dir" && ELOOP_LIB_DIR="$ROOT" PYTHONPATH="$ROOT" python3 -W ignore "$PYFILE" \
      tmp/state/rolling_scores.json tmp/state/current_strategy_run.json tmp/state/active_branch.json tmp/state/best_strategy_anchor.json \
      "$cur" 12 strategy_versions/by_hash 1000 800 1600 2 4 400 3 2800 2200 3300 2 \
      tmp/state/stagnation_counter.json tmp/state/wildcard_origin.json tmp/state/wildcard_attempt_state.json tmp/state/wildcard_outcomes.jsonl \
      tmp/state/annealing_candidates.jsonl 1 1800 0.85 0 4 15 1 4 7 eval_score_history.txt 1 50 50 0 \
      tmp/state/archive_restart_cooldown.json 1 0 12 0.85 0 4 0.80 12,13,14,15 strategy_versions_archive/by_hash 2>/dev/null \
    | grep -v '^STATGATE:' )
}

run_check_nolib() {  # eval_stats を import できない環境を模擬 (ローカル fallback 経路)
  local dir="$1" cur="$2"
  ( cd "$dir" && ELOOP_LIB_DIR="$dir" PYTHONPATH="$dir" python3 -W ignore "$PYFILE" \
      tmp/state/rolling_scores.json tmp/state/current_strategy_run.json tmp/state/active_branch.json tmp/state/best_strategy_anchor.json \
      "$cur" 12 strategy_versions/by_hash 1000 800 1600 2 4 400 3 2800 2200 3300 2 \
      tmp/state/stagnation_counter.json tmp/state/wildcard_origin.json tmp/state/wildcard_attempt_state.json tmp/state/wildcard_outcomes.jsonl \
      tmp/state/annealing_candidates.jsonl 1 1800 0.85 0 4 15 1 4 7 eval_score_history.txt 1 50 50 0 \
      tmp/state/archive_restart_cooldown.json 1 0 12 0.85 0 4 0.80 12,13,14,15 strategy_versions_archive/by_hash 2>/dev/null \
    | grep -v '^STATGATE:' )
}

CUR=cur0000000000; ANC=anc0000000000
# 共通 anchor: n=100 comp≈9.5k, max_types 25件 (T11 25/25, T13 16/25, T14 6/25), russia 1 (best 15)
ANC_ARGS="anc_scores=8000,100,4400 anc_mt=14x6,13x10,12x7,11x2 anc_russia=1"

# --- case A: 2026-08-25 02:26 v727 粛清の再現 (n=13, comp 優位, breach 0, T14 1/13 vs 6/25, russia 0 vs 1) ---
A="$TMP/caseA"; mk_fixture "$A" $CUR $ANC $ANC_ARGS cur_scores=9400,13,4400 cur_mt=14x1,13x11,12x1 cur_russia=0 pad_top=8
outA=$(run_check "$A" $CUR)
assert_not_contains "REGRESSION:" "$outA" "caseA(v727 n=13, breach0, T14 1/13 vs 6/25): no purge"
assert_contains "t14=1/13 vs 6/25 p=0.2205" "$outA" "caseA STAGEGATE observation carries Fisher p=0.2205"
assert_contains "fired=0" "$outA" "caseA STAGEGATE fired=0"
assert_contains "graced=0" "$outA" "caseA is saved by the stat gate alone (grace not applicable: best 14<15)"
outA_off=$(STAGE_GATE_STAT_ENABLED=0 run_check "$A" $CUR)
assert_contains "lost_kazakhstan_gate" "$outA_off" "caseA with STAGE_GATE_STAT_ENABLED=0: legacy purge reproduces"
outA_knob=$(STAGE_GATE_MIN_RATE_GAP=0.0 STAGE_GATE_STAT_ALPHA=1.0 run_check "$A" $CUR)
assert_contains "lost_kazakhstan_gate" "$outA_knob" "caseA with gap floor 0 + alpha 1.0: legacy verdict restored (knobs independent)"
assert_contains "stagestat=type14/cur1of13/anc6of25/p0.2205" "$outA_knob" "caseA fired reason carries stagestat detail"

# --- case B: 2026-08-25 05:06 e5b671 粛清の再現 (n=100, comp -100 breach0, T11 41/43 vs 25/25, russia 2 vs 1) ---
B="$TMP/caseB"; mk_fixture "$B" $CUR $ANC $ANC_ARGS cur_scores=7900,100,4400 cur_mt=14x7,13x22,12x12,10x2 cur_russia=2 pad_top=8
outB=$(run_check "$B" $CUR)
assert_not_contains "REGRESSION:" "$outB" "caseB(n=100, comp within noise, T11 41/43, russia 2>=1): no purge"
assert_contains "graced=1" "$outB" "caseB noninferior grace applies without comp>=anchor clause"
outB_nograce=$(STAGE_GATE_NONINFERIOR_GRACE=0 run_check "$B" $CUR)
assert_not_contains "REGRESSION:" "$outB_nograce" "caseB with grace off: stat gate alone suppresses T11 (gap 0.047)"
assert_contains "t11=41/43 vs 25/25 p=0.3964 gap=0.047 fired=0" "$outB_nograce" "caseB STAGEGATE observation t11"
outB_off=$(STAGE_GATE_STAT_ENABLED=0 STAGE_GATE_NONINFERIOR_GRACE=0 run_check "$B" $CUR)
assert_contains "lost_turkmenistan_gate" "$outB_off" "caseB with both toggles off: legacy purge reproduces"

# --- case C: 真の劣化 (n=40, T13 4/40 vs 16/25, breach 0) → REGRESSION ---
C="$TMP/caseC"; mk_fixture "$C" $CUR $ANC $ANC_ARGS cur_scores=8000,40,4400 cur_mt=13x4,12x30,11x6 cur_russia=0 pad_top=8
outC=$(run_check "$C" $CUR)
assert_contains "REGRESSION:mode=objective_regression" "$outC" "caseC(true stage regression T13 4/40 vs 16/25): purge"
assert_contains "lost_ukraine_gate+stagestat=type13/cur4of40/anc16of25/p0.0000" "$outC" "caseC reason=lost_ukraine_gate with evidence"

# --- case D: anchor ソ連到達済み, current 未到達 → lost_soviet_path (grace 対象外) ---
D="$TMP/caseD"; mk_fixture "$D" $CUR $ANC anc_scores=8000,100,4400 anc_mt=14x6,13x10,12x7,11x2 anc_russia=2 anc_soviet=1 cur_scores=9400,13,4400 cur_mt=14x1,13x11,12x1 cur_russia=0 pad_top=8
outD=$(run_check "$D" $CUR)
assert_contains "lost_soviet_path" "$outD" "caseD(anchor soviet=1, current soviet=0): purge regardless of grace"

# --- case H: rank 免除 (pad 2 → rank<=7) は従来どおり ---
H="$TMP/caseH"; mk_fixture "$H" $CUR $ANC $ANC_ARGS cur_scores=8000,40,4400 cur_mt=13x4,12x30,11x6 cur_russia=0 pad_top=2
outH=$(run_check "$H" $CUR)
assert_not_contains "REGRESSION:" "$outH" "caseH(rank<=7): rank grace still exempts"
assert_contains "skipped=rank_grace" "$outH" "caseH STAGEGATE says skipped=rank_grace"

# --- case I: russia_noninferior の標本数考慮 (stat off で grace だけを分離) ---
# current 13件 russia 1 (best 15), anchor 25件 russia 2: 期待値 2/25*13=1.04 >= 1 → Fisher p(1/13 vs 2/25) > alpha → 非劣後 → grace
I="$TMP/caseI"; mk_fixture "$I" $CUR $ANC anc_scores=8000,100,4400 anc_mt=14x6,13x10,12x7,11x2 anc_russia=2 cur_scores=9400,13,4400 cur_mt=15x1,13x10,12x1,10x1 cur_russia=1 pad_top=8
outI=$(STAGE_GATE_STAT_ENABLED=0 run_check "$I" $CUR)
assert_not_contains "REGRESSION:" "$outI" "caseI(stat off, russia 1/13 vs 2/100): grace via russia_noninferior"
outI_strict=$(STAGE_GATE_STAT_ENABLED=0 STAGE_GATE_RUSSIA_MIN_EXPECTED=0 STAGE_GATE_STAT_ALPHA=1.0 run_check "$I" $CUR)
assert_contains "REGRESSION:" "$outI_strict" "caseI with RUSSIA_MIN_EXPECTED=0 + alpha 1.0: grace withdrawn"
# caseI2: 期待値 < 1 の経路 (anchor 窓 50件 russia 2 → 2/50*13=0.52) — RUSSIA_MIN_EXPECTED=0 でも Fisher で非劣後
I2="$TMP/caseI2"; mk_fixture "$I2" $CUR $ANC anc_scores=8000,100,4400 anc_mt=14x12,13x20,12x14,11x4 anc_russia=2 cur_scores=9400,13,4400 cur_mt=15x1,13x10,12x1,10x1 cur_russia=1 pad_top=8
outI2=$(STAGE_GATE_STAT_ENABLED=0 run_check "$I2" $CUR)
assert_not_contains "REGRESSION:" "$outI2" "caseI2(stat off, russia 1/13 vs 2/50, expected 0.52<1): grace"
outI2_fisher=$(STAGE_GATE_STAT_ENABLED=0 STAGE_GATE_RUSSIA_MIN_EXPECTED=0 run_check "$I2" $CUR)
assert_not_contains "REGRESSION:" "$outI2_fisher" "caseI2 with RUSSIA_MIN_EXPECTED=0: Fisher p>alpha still noninferior"

# --- case L: sticky best_max_type だけが違う (窓内到達率は同じ) → 統計モードでは発火しない (意図的仕様) ---
L="$TMP/caseL"; mk_fixture "$L" $CUR $ANC anc_scores=8000,100,4400 anc_mt=13x25 anc_best=14 cur_scores=8000,40,4400 cur_mt=13x40 pad_top=8
outL=$(run_check "$L" $CUR)
assert_not_contains "REGRESSION:" "$outL" "caseL(anchor best 14 sticky, windows 0/25 vs 0/40): no purge in stat mode"
assert_contains "t14=0/40 vs 0/25 p=1.0000 gap=0.000 fired=0" "$outL" "caseL STAGEGATE shows the best-clause evaluation"
outL_off=$(STAGE_GATE_STAT_ENABLED=0 run_check "$L" $CUR)
assert_contains "lost_kazakhstan_gate" "$outL_off" "caseL legacy mode: best clause fires as before"

# --- case M: comp 許容 (grace) — comp 差が ratio*min_comp_gap を超えると grace は外れる ---
# caseB fixture (comp -100): ratio 0.005 → 許容 5 → grace 外れ → stat gate だけで判定 (fired=0 のまま no purge)
outM=$(STAGE_GATE_GRACE_COMP_GAP_RATIO=0.005 run_check "$B" $CUR)
assert_contains "graced=0" "$outM" "caseM(comp tolerance 5 < gap 100): grace withdrawn"
assert_not_contains "REGRESSION:" "$outM" "caseM: stat gate still suppresses insignificant T11 shortfall"

# --- case N: bash 側の STAGEGATE 除去ブロックを実コードから抽出して検証 ---
STRIP="$TMP/strip.sh"
awk '/^\tlocal _stagegate_line$/{f=1} f{print} f && /^\tfi$/{exit}' "$ROOT/strategy/regression.sh" > "$STRIP"
[ -s "$STRIP" ] || not_ok "caseN strip block extraction"
outN=$(bash -c '
  set -euo pipefail
  log() { printf "LOG:%s\n" "$*"; }
  f() { local result; result=$(printf "STAGEGATE:graced=0 rank=9 t14=1/13 vs 6/25 p=0.2205 gap=0.163 fired=0\nPROMOTE:anchor_hash=a,current_hash=b\n"); source "$1"; printf "RESULT:%s\n" "$result"; }
  f "$1"; g() { local result; result="OK"; source "$1"; printf "RESULT:%s\n" "$result"; }; g "$1"; h() { local result; result=""; source "$1"; printf "RESULT:[%s]\n" "$result"; }; h "$1"; echo EXIT_OK
' _ "$STRIP")
assert_contains "LOG:[STAGEGATE] graced=0 rank=9" "$outN" "caseN bash strip logs the STAGEGATE line"
assert_contains "RESULT:PROMOTE:anchor_hash=a,current_hash=b" "$outN" "caseN bash strip leaves only the verdict in \$result"
assert_not_contains "RESULT:STAGEGATE" "$outN" "caseN no STAGEGATE survives in \$result"
assert_contains "RESULT:OK" "$outN" "caseN plain OK passes through"
assert_contains "EXIT_OK" "$outN" "caseN strip block is safe under set -euo pipefail (empty result too)"

# --- case J: Fisher 配線 (lib/eval_stats と同一) ---
pj=$(cd "$ROOT" && python3 -c "
import sys; sys.path.insert(0,'lib'); import eval_stats as e
def pr(h1,n1,h2,n2): return e.fisher_one_sided(n1-h1,n1,n2-h2,n2)
print('%.4f %.4f %.2e %.2e %.4f' % (pr(1,13,6,25), pr(41,43,25,25), pr(0,40,12,25), pr(0,13,13,13), pr(0,13,1,100)))")
assert_contains "0.2205 0.3964 1.29e-06 9.61e-08 0.8850" "$pj" "caseJ fisher_one_sided (miss form) matches reference values"

# --- case K: eval_stats 不在時のローカル fallback でも同じ判定 ---
outA_fb=$(ELOOP_LIB_DIR="$TMP" run_check_nolib "$A" $CUR)
assert_contains "t14=1/13 vs 6/25 p=0.2205" "$outA_fb" "caseK local Fisher fallback gives identical p"

if [ "${BASELINE_PRINT:-0}" = "1" ]; then
  for c in A B C D H I I2 L M; do eval "echo \"[$c] \$(printf '%s' \"\$out$c\" | grep -o '^[A-Z]*:mode=[a-z_]*\|reasons=[^ ]*\|^PROMOTE:\|^OK' | tr '\n' ' ')\""; done
fi
exit $FAIL
