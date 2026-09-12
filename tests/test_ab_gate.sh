#!/usr/bin/env bash
# strategy/ab_gate.sh の単体テスト: 候補出力 → 境界での A/B 開始 (dry-run / 実行) → 逐次判定 → finish A/B。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_gate_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tools" "$WORK/lib" "$WORK/strategy_helpers" "$WORK/strategy_versions/by_hash" "$WORK/strategy_versions_archive/by_hash"
cp "$ROOT/extract_decide_hash.py" "$ROOT/set_toggle.sh" "$WORK/"
cp "$ROOT/tools/ab_report.py" "$ROOT/tools/ab_decide.py" "$WORK/tools/"
cp "$ROOT/lib/eval_stats.py" "$WORK/lib/" 2>/dev/null || true
cp "$ROOT/strategy.py" "$WORK/strategy.py"
cp "$ROOT/strategy_helpers/"*.py "$WORK/strategy_helpers/" 2>/dev/null || true
python3 - "$ROOT/strategy.py" "$WORK/alt.py" <<'PY'
import sys
s=open(sys.argv[1],encoding="utf-8").read(); old="best_x = 0.0905"; assert s.count(old)==1
open(sys.argv[2],"w",encoding="utf-8").write(s.replace(old,"best_x = 0.0906"))
PY
cd "$WORK"
echo 'REGRESSION_DISABLED=0' > .env
echo 'AB_GATE_ENABLED=1' >> .env
echo 'AB_GATE_DRY_RUN=1' >> .env
echo 7 > game_count.txt
A=$(python3 extract_decide_hash.py strategy.py); B=$(python3 extract_decide_hash.py alt.py)
LOGS=""
log() { LOGS="$LOGS
$*"; }
TMP_STATE_DIR=tmp/state; STRATEGY_FILE=strategy.py; IMPROVE_LOCK_FILE=tmp/improve.lock; GAME_COUNT_FILE=game_count.txt
REJECTED_HASHES_FILE=tmp/state/rejected_hashes.txt
source "$ROOT/strategy/ab_interleave.sh"
source "$ROOT/strategy/ab_gate.sh"
pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }
lastlog() { printf '%s' "$LOGS" | tail -n 1; }

# 1) emit: harvest dir → candidate dir + meta (root 不変)
mkdir -p harvest/logs harvest/strategy_helpers; cp alt.py harvest/strategy.py.staging; echo "changed X" > harvest/logs/change_log.txt; cp strategy_helpers/board_stats.py harvest/strategy_helpers/ 2>/dev/null || true
_ab_gate_emit_candidate harvest "$A" 12 "100 200" && ok || ng "emit"
_ab_gate_candidate_pending && ok || ng "pending after emit"
[ "$(_ab_meta_get cand_hash)" = "$B" ] && [ "$(_ab_meta_get base_hash)" = "$A" ] && ok || ng "meta hashes"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$A" ] && ok || ng "root untouched by emit"
cp strategy.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" >/dev/null 2>&1 && ng "same-hash candidate must not emit" || ok
# candidate_ready_since
_ab_gate_candidate_ready_since 0 "$A" && ok || ng "ready_since base ok"
_ab_gate_candidate_ready_since 0 "deadbeef" && ng "ready_since wrong base" || ok
_ab_gate_candidate_ready_since $(( $(date +%s) + 100 )) "$A" && ng "ready_since future start" || ok

# 2) before_game (dry-run): would start once, no state
_ab_gate_before_game; lastlog | grep -q "would start" && ok || ng "dry-run would start ($(lastlog))"
[ ! -f tmp/state/ab_state.json ] && ok || ng "dry-run must not create state"
n1=$(printf '%s' "$LOGS" | grep -c "would start"); _ab_gate_before_game; n2=$(printf '%s' "$LOGS" | grep -c "would start"); [ "$n1" = "$n2" ] && ok || ng "dry-run logs once ($n1 -> $n2)"

# 3) stale base → discarded
python3 - <<'PY'
import json; p="tmp/state/ab_candidate/meta.json"; d=json.load(open(p)); d["base_hash"]="000000000000"; json.dump(d,open(p,"w"))
PY
_ab_gate_before_game; [ ! -d tmp/state/ab_candidate ] && ok || ng "stale base discarded"

# 4) real start via before_game (dry-run off)
sed -i.bak 's/^AB_GATE_DRY_RUN=.*/AB_GATE_DRY_RUN=0/' .env
cp alt.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" 12 "100 200" >/dev/null
_acquire_spawn_lock() { return 1; }
_ab_gate_before_game
_ab_gate_candidate_pending && ok || ng "busy spawn mutex must preserve candidate"
unset -f _acquire_spawn_lock
_ab_gate_before_game
[ -f tmp/state/ab_state.json ] && ok || ng "start created state ($(lastlog))"
grep -q "^REGRESSION_DISABLED=1" .env && grep -q "^SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py" .env && ok || ng "toggles set: $(grep -E '^(REGRESSION|SOREN_AB)' .env | tr '\n' ' ')"
[ "$(_ab_state_get b_hash)" = "$B" ] && [ "$(_ab_state_get a_hash)" = "$A" ] && ok || ng "state hashes"
[ "$(_ab_state_get primary)" = "eval" ] && ok || ng "state must record the canonical primary (got $(_ab_state_get primary))"
python3 - <<'PY' && ok || ng "state must freeze calibrated decision rule"
import json
st=json.load(open("tmp/state/ab_state.json"))
r=st["decision_rule"]
assert st["decision_rule_version"] == 2, st
assert r["version"] == 2, r
assert r["harm_min_blocks"] == 10, r
assert abs(r["harm_z"] - 2.3263) < 1e-9, r
assert r["futility_k"] == 12, r
assert abs(r["futility_z"] - 1.2816) < 1e-9, r
PY
[ "$(_ab_state_get regression_disabled_before)" = "0" ] && ok || ng "regression_disabled_before recorded"
[ "$(python3 extract_decide_hash.py tmp/revert_strategy.py)" = "$A" ] && ok || ng "revert point = A"
[ ! -d tmp/state/ab_candidate ] && ok || ng "candidate consumed"
export SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py
_ab_active >/dev/null 2>&1 && ok || ng "active after start: $(_ab_active 2>&1)"

# 5) after_game: synthetic games with strong harm → finish A (rejected, files moved, toggles restored)
# 新規 rule は k>=10 / UCB99 なので、10 完全ブロックまで入れて明確な害を検出する。
# 改善プロンプトが読む change_log に A/B の決着が残ること (焼き直し防止の唯一の材料)。
# 本番のゲームループ (eloop.sh) では CHANGE_LOG_FILE_HOST も CHANGE_LOG_FILE も
# 未設定 — 定義しているのは eloop_improve.sh (別プロセス) だけ。2026-09-10 に VM で
# `source ./eloop_lib.sh` 後も両方とも未設定であることを実測済み。ここも同じ条件で
# 回し、既定パスへ自力で書けることを検証する (変数に依存すると本番で空振りする)。
unset CHANGE_LOG_FILE_HOST CHANGE_LOG_FILE
DEFAULT_CHANGE_LOG=logs/change_log.txt
rm -rf logs
ACCUMULATED_GAMES_FILE=tmp/state/accumulated_games.json
echo '{"count":48,"hash":"A"}' > "$ACCUMULATED_GAMES_FILE"
cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE"
_clear_accumulated_data() { rm -f "$ACCUMULATED_GAMES_FILE"; }
mkdir -p "$AB_CANDIDATE_DIR"; echo queued > "$AB_CANDIDATE_DIR/keep"
python3 - "$A" "$B" <<'PY'
import json,sys,random
a,b=sys.argv[1:3]; rng=random.Random(1); rows=[]; idx=0
for k in range(10):
    base=rng.gauss(1600,150)
    for ch in "ABBA":
        v=base+rng.gauss(0,80)-(600 if ch=="B" else 0)
        rows.append({"idx":idx,"arm":ch,"hash":b if ch=="B" else a,"score":v,"eval":v+5000,"turns":90,"tainted":False}); idx+=1
open("tmp/state/ab_games.jsonl","w").write("".join(json.dumps(r)+"\n" for r in rows))
st=json.load(open("tmp/state/ab_state.json")); st["games_recorded"]=len(rows); json.dump(st,open("tmp/state/ab_state.json","w"))
PY
_ab_gate_after_game
printf '%s' "$LOGS" | grep -q "verdict=REJECT_HARM" && ok || ng "verdict logged ($(printf '%s' "$LOGS" | grep AB-GATE | tail -2))"
printf '%s' "$LOGS" | grep -q "harm_ucb(z=2.3263)" && ok || ng "calibrated harm bound must be logged"
[ ! -f tmp/state/ab_state.json ] && ok || ng "finish A removed state"
grep -qx "$B" tmp/state/rejected_hashes.txt && ok || ng "B rejected recorded"
grep -q "^REGRESSION_DISABLED=0" .env && grep -q "^SOREN_AB_ALT_STRATEGY=$" .env && ok || ng "toggles restored: $(grep -E '^(REGRESSION|SOREN_AB_ALT)' .env | tr '\n' ' ')"
ls tmp/history/ab_*_games.jsonl >/dev/null 2>&1 && ls tmp/history/ab_*_state.json >/dev/null 2>&1 && ok || ng "history files"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$A" ] && ok || ng "root still A after reject"
[ -f "$ACCUMULATED_GAMES_FILE" ] && [ -f "$IMPROVE_LOCK_FILE" ] && ok || ng "A verdict must preserve pending 48-game batch"
[ -f "$AB_CANDIDATE_DIR/keep" ] && ok || ng "finish must preserve later queued candidate"
# 棄却も change_log に残る: 判定・警告・A→B の実差分。これが無いと改善側は
# 「その方針は試して駄目だった」を知りようがなく、同じ方針を再提案しうる。
[ -f "$DEFAULT_CHANGE_LOG" ] && ok || ng "must create the default change_log path with no env var set"
grep -q "A/B REJECTED" "$DEFAULT_CHANGE_LOG" && ok || ng "reject must be written to change_log"
grep -q "REJECT_HARM" "$DEFAULT_CHANGE_LOG" && ok || ng "reject verdict must be in change_log"
grep -q "焼き直しを避ける" "$DEFAULT_CHANGE_LOG" && ok || ng "reject must warn against rehashing"
grep -q "^+.*best_x = 0.0906" "$DEFAULT_CHANGE_LOG" && ok || ng "reject must record the A→B diff: $(head -6 "$DEFAULT_CHANGE_LOG" | tr '\n' '|')"
grep -q "base=${A:0:12} cand=${B:0:12}" "$DEFAULT_CHANGE_LOG" && ok || ng "reject must record both hashes"
# rejected candidate is discarded at the boundary
cp alt.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" >/dev/null; _ab_gate_before_game; [ ! -d tmp/state/ab_candidate ] && [ ! -f tmp/state/ab_state.json ] && ok || ng "rejected hash discarded"

# 6) finish B adopts: root becomes B, revert = A
# CHANGE_LOG_FILE_HOST が設定されていればそちらを優先する (改善プロセス経由の呼び出し)。
CHANGE_LOG_FILE_HOST=logs/custom_change_log.txt
rm -f tmp/state/rejected_hashes.txt
python3 - "$ROOT/strategy.py" "$WORK/alt2.py" <<'PY'
import sys
s=open(sys.argv[1],encoding="utf-8").read(); old="best_x = 0.0905"; open(sys.argv[2],"w",encoding="utf-8").write(s.replace(old,"best_x = 0.0907"))
PY
C=$(python3 extract_decide_hash.py alt2.py)
cp alt2.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$A" >/dev/null; _ab_gate_before_game
[ -f tmp/state/ab_state.json ] && ok || ng "second start"
echo '{"count":48,"hash":"A"}' > "$ACCUMULATED_GAMES_FILE"
cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE"
cp "$ACCUMULATED_GAMES_FILE" tmp/state/improve_retry_batch.json
# apply failure must not be committed to the improvement memory as ADOPTED.
strategy_runtime_atomic_apply() { return 1; }
_ab_finish B "forced apply failure" >/dev/null 2>&1 && ng "forced apply failure must fail" || ok
! grep -q "A/B ADOPTED" "$CHANGE_LOG_FILE_HOST" 2>/dev/null && ok || ng "failed apply must not write ADOPTED"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$A" ] && ok || ng "failed apply must keep root A"
[ -f tmp/state/ab_state.json ] && ok || ng "failed apply must keep AB state for recovery"
unset -f strategy_runtime_atomic_apply
_ab_finish B "test adopt" >/dev/null 2>&1 && ok || ng "finish B rc"
[ ! -e "$IMPROVE_LOCK_FILE" ] && [ ! -e tmp/state/improve_retry_batch.json ] && ok || ng "B verdict must retire old A locks"
find tmp/history -name '*batch*' | grep -q . && ok || ng "B verdict must archive old metadata"
[ "$(python3 extract_decide_hash.py strategy.py)" = "$C" ] && ok || ng "root adopted C"
[ "$(python3 extract_decide_hash.py tmp/revert_strategy.py)" = "$A" ] && ok || ng "revert = previous root"
[ ! -f tmp/state/ab_state.json ] && grep -q "^SOREN_AB_ALT_STRATEGY=$" .env && ok || ng "state cleared after adopt"
python3 -c "import json;rows=[json.loads(l) for l in open('tmp/history/ab_history.jsonl')];assert rows[-1]['winner']=='B' and rows[-2]['winner']=='A',rows" && ok || ng "ab_history entries"
# 採用も change_log に残る。ただし apply/hash 検証が成功してから初めて ADOPTED を確定する。
grep -q "A/B ADOPTED" "$CHANGE_LOG_FILE_HOST" && ok || ng "adopt must be written to change_log"
[ "$(grep -c 'A/B ADOPTED' "$CHANGE_LOG_FILE_HOST")" = "1" ] && ok || ng "only successful adopt may write ADOPTED"
grep -q "^+.*best_x = 0.0907" "$CHANGE_LOG_FILE_HOST" && ok || ng "adopt must record the A→B diff"
grep -q "base=${A:0:12} cand=${C:0:12}" "$CHANGE_LOG_FILE_HOST" && ok || ng "adopt must record both hashes"
# 差分の向きは apply 前に保存した A スナップショット → B。
grep -q "^-.*best_x = 0.0905" "$CHANGE_LOG_FILE_HOST" && ok || ng "diff direction must be base→candidate"
# 既存の追記側と同じ 200 行キャップ
[ "$(wc -l <"$CHANGE_LOG_FILE_HOST")" -le 200 ] && ok || ng "change_log must stay within the 200-line cap ($(wc -l <"$CHANGE_LOG_FILE_HOST"))"
! grep -q "A/B ADOPTED" "$DEFAULT_CHANGE_LOG" && ok || ng "CHANGE_LOG_FILE_HOST must take precedence over the default path"

# 7) gate off → before/after are no-ops
sed -i.bak 's/^AB_GATE_ENABLED=.*/AB_GATE_ENABLED=0/' .env
cp alt.py harvest/strategy.py.staging; _ab_gate_emit_candidate harvest "$C" >/dev/null; _ab_gate_before_game; [ -d tmp/state/ab_candidate ] && [ ! -f tmp/state/ab_state.json ] && ok || ng "gate off no-op"

echo "test_ab_gate: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
