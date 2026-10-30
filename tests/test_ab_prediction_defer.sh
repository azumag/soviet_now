#!/usr/bin/env bash
# soviet_now#641 回帰テスト: 未確定の予想ラウンド/未確認の作成予約がある間、
# _ab_gate_before_game は A/B 開始を副作用なしで defer し候補を保持する。
# また pending 中の A/B 候補は新規予想 create をブロックする (修正契約 1-4)。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_pred_defer_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tools" "$WORK/lib" "$WORK/strategy_helpers" "$WORK/strategy_versions/by_hash" "$WORK/strategy_versions_archive/by_hash"
cp "$ROOT/extract_decide_hash.py" "$ROOT/set_toggle.sh" "$WORK/"
cp "$ROOT/tools/ab_report.py" "$ROOT/tools/ab_decide.py" "$WORK/tools/"
cp "$ROOT/lib/eval_stats.py" "$WORK/lib/" 2>/dev/null || true
cp "$ROOT/lib/prediction_round.py" "$ROOT/lib/outbound_queue.sh" "$WORK/lib/" 2>/dev/null || true
cp "$ROOT/strategy.py" "$WORK/strategy.py"
cp "$ROOT/strategy_helpers/"*.py "$WORK/strategy_helpers/" 2>/dev/null || true
cp "$ROOT/twitch_predictions.sh" "$WORK/"
python3 - "$ROOT/strategy.py" "$WORK/alt.py" <<'PY'
import sys
s=open(sys.argv[1],encoding="utf-8").read(); old="best_x = 0.0905"; assert s.count(old)==1
open(sys.argv[2],"w",encoding="utf-8").write(s.replace(old,"best_x = 0.0906"))
PY
cd "$WORK"
cat > .env <<'EOF'
REGRESSION_DISABLED=0
AB_GATE_ENABLED=1
AB_GATE_DRY_RUN=0
MIN_GAMES_BEFORE_IMPROVE=48
EOF
echo 7 > game_count.txt
A=$(python3 extract_decide_hash.py strategy.py); B=$(python3 extract_decide_hash.py alt.py)
LOGS=""
log() { LOGS="$LOGS
$*"; }
TMP_STATE_DIR=tmp/state; STRATEGY_FILE=strategy.py; IMPROVE_LOCK_FILE=tmp/improve.lock; GAME_COUNT_FILE=game_count.txt
REJECTED_HASHES_FILE=tmp/state/rejected_hashes.txt
source "$ROOT/strategy/ab_interleave.sh"
source "$ROOT/strategy/ab_gate.sh"
source "$ROOT/core/strategy_runtime.sh"
IMPROVE_LIVE_APPLY_POLICY=off
pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }
lastlog() { printf '%s' "$LOGS" | tail -n 1; }

emit_candidate() {
	mkdir -p harvest/logs harvest/strategy_helpers
	cp alt.py harvest/strategy.py.staging
	echo "changed X" > harvest/logs/change_log.txt
	_ab_gate_emit_candidate harvest "$A" 12 "100 200" >/dev/null
}

write_prediction_state() { # $1=games_completed
	python3 - "$1" <<'PY'
import json, sys
state = {"prediction_id": "test-pred", "outcome_ids": ["a","b","c","d"],
         "game_num": 100, "created_at": 1, "russia_created": False,
         "round_version": 2, "max_games": 48,
         "games_completed": int(sys.argv[1]), "best_outcome": 0}
open("tmp/state/current_prediction.json","w").write(json.dumps(state))
PY
}

expect_defer() { # $1=case label
	local label="$1"
	_ab_gate_before_game
	_ab_gate_candidate_pending || ng "$label: candidate must be kept"
	[ -f tmp/state/ab_state.json ] && ng "$label: A/B side effects forbidden (ab_state.json)"
	[ -f tmp/state/ab_alt_strategy.py ] && ng "$label: A/B side effects forbidden (alt strategy)"
	! grep -q "^SOREN_AB_ALT_STRATEGY=" .env || ng "$label: A/B side effects forbidden (env toggle)"
	lastlog | grep -q "予想未確定" && ok || ng "$label: defer log ($(lastlog))"
	rm -f tmp/state/ab_state.json
}

# === 1) 決定的再現: 未完了 12/48 の予想 + candidate ready → A/B 副作用ゼロ ===
emit_candidate
write_prediction_state 12
expect_defer "unresolved 12/48"

# === 2) 48/48 でも resolve retry 未確認 → defer (試合数到達は確定ではない) ===
write_prediction_state 48
mkdir -p tmp/state/prediction_retry
echo '{"recorded_at":1,"delay":60}' > tmp/state/prediction_retry/resolve.json
expect_defer "48/48 + resolve retry"

# === 3) legacy state (生 id 行) → defer ===
rm -f tmp/state/prediction_retry/resolve.json
printf 'legacy-prediction-id\n' > tmp/state/current_prediction.json
expect_defer "legacy state"

# === 4) 不正 state (壊れた JSON) → defer (invalid は確定ではない) ===
printf '{broken json' > tmp/state/current_prediction.json
expect_defer "invalid state"

# === 5) 作成予約 (create HTTP 途中/crash 残存) → defer ===
rm -f tmp/state/current_prediction.json
: > tmp/state/prediction_retry/create_inflight
expect_defer "create_inflight reservation"
rm -f tmp/state/prediction_retry/create_inflight

# === 6) create retry record (結果未確認) → defer ===
echo '{"recorded_at":1,"delay":60}' > tmp/state/prediction_retry/create.json
expect_defer "create retry record"
rm -f tmp/state/prediction_retry/create.json

# === 7) hot-streak 作成 intent 残存 → defer ===
: > tmp/state/hot_streak_prediction_pending
expect_defer "hot-streak pending"
rm -f tmp/state/hot_streak_prediction_pending

# === 8) 全て確定 → A/B 開始に進む (副作用発生) ===
_ab_gate_before_game
[ -f tmp/state/ab_state.json ] && ok || ng "resolved round must let A/B start ($(lastlog))"
[ "$(_ab_state_get b_hash)" = "$B" ] && ok || ng "state b_hash"

# === 9) A/B 候補待機中は新規予想 create を作らない (修正契約 3) ===
# 実スクリプトを WORK へコピー済み。context_allowed が候補を検出して curl 前に
# SKIP することを確認する (HTTP は呼ばれない)。
rm -rf tmp/state/ab_state.json tmp/state/ab_alt_strategy.py
sed -i.bak 's/^SOREN_AB_ALT_STRATEGY=.*//;s/^REGRESSION_DISABLED=1/REGRESSION_DISABLED=0/' .env 2>/dev/null || true
emit_candidate
TWITCH_PREDICTIONS_ENABLED=1 TWITCH_PREDICTIONS_TOKEN=dummy TWITCH_CLIENT_ID=dummy \
	TWITCH_BROADCASTER_ID=dummy TMP_STATE_DIR=tmp/state \
	bash ./twitch_predictions.sh create 100 >/dev/null 2>create.log
grep -q "A/B candidate is pending" create.log && ok || ng "create must be blocked by pending candidate ($(tail -1 create.log))"
[ ! -f tmp/state/current_prediction.json ] && ok || ng "blocked create must not publish state"

# === 10) create のロック内再判定: ab_state 存在は create を拒否 ===
rm -rf tmp/state/ab_candidate
echo '{"a_hash":"x","b_hash":"y"}' > tmp/state/ab_state.json
TWITCH_PREDICTIONS_ENABLED=1 TWITCH_PREDICTIONS_TOKEN=dummy TWITCH_CLIENT_ID=dummy \
	TWITCH_BROADCASTER_ID=dummy TMP_STATE_DIR=tmp/state \
	bash ./twitch_predictions.sh create 100 >/dev/null 2>create2.log
grep -q "A/B test state exists" create2.log && ok || ng "create must be blocked by running A/B ($(tail -1 create2.log))"

echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
