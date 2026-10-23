#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d /tmp/ab_confirmation_test.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/tmp/state" "$WORK/tmp/history"
cd "$WORK"

AB_STATE_FILE=tmp/state/ab_state.json
AB_GAMES_FILE=tmp/state/ab_games.jsonl
AB_HISTORY_DIR=tmp/history
AB_ALT_FILE=tmp/state/ab_alt_strategy.py
AB_ABORT_FILE=tmp/state/ab_abort
TMP_STATE_DIR=tmp/state
AB_CALLS=0
AB_LAST_WINNER=""
AB_LAST_REASON=""
AB_FINISH_RC=0

log() { :; }
_ab_state_get() {
	python3 - "$AB_STATE_FILE" "$1" <<'PY' 2>/dev/null || echo ""
import json,sys
try:
    v=json.load(open(sys.argv[1])).get(sys.argv[2],"")
    print("" if v is None else v)
except Exception:
    print("")
PY
}
_ab_start_from_bundle() {
	python3 - "$AB_STATE_FILE" <<'PY'
import json,sys
json.dump({"a_hash":"a"*64,"b_hash":"b"*64,"games_recorded":0},open(sys.argv[1],"w"))
PY
	: > "$AB_GAMES_FILE"
	return 0
}
_ab_finish() {
	AB_CALLS=$((AB_CALLS+1))
	AB_LAST_WINNER="$1"
	AB_LAST_REASON="${2:-}"
	if [ "$AB_FINISH_RC" -ne 0 ]; then
		return "$AB_FINISH_RC"
	fi
	return 0
}
export AB_CALLS AB_LAST_WINNER AB_LAST_REASON AB_FINISH_RC
source "$ROOT/strategy/ab_confirmation.sh"

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

# Legacy state (patch導入前) はそのまま finish B へ通す。
printf '%s\n' '{"a_hash":"aaaaaaaa","b_hash":"bbbbbbbb","games_recorded":4}' > "$AB_STATE_FILE"
printf '%s\n' '{"arm":"A"}' > "$AB_GAMES_FILE"
_ab_finish B "legacy adopt" && ok || ng "legacy finish rc"
[ "$AB_CALLS" -eq 1 ] && [ "$AB_LAST_WINNER" = B ] && ok || ng "legacy state must pass through"

# 新規startだけ confirmation contractを付与する。
AB_CALLS=0
_ab_start_from_bundle bundle ABBA && ok || ng "wrapped start"
python3 - "$AB_STATE_FILE" <<'PY' && ok || ng "new state confirmation fields"
import json,sys
st=json.load(open(sys.argv[1]))
assert st["confirmation_required"] is True
assert st["confirmation_version"] == 1
assert st["confirmation_phase"] == "selection"
assert st["verified_improvement"] is False
PY

# selection ADOPT は live finishへ進まず、標本を退避して独立confirmationへ。
python3 - "$AB_STATE_FILE" <<'PY'
import json,sys
p=sys.argv[1]; st=json.load(open(p)); st["games_recorded"]=4; json.dump(st,open(p,"w"))
PY
for arm in A B B A; do printf '{"arm":"%s","score":1000,"eval":6000,"tainted":false}\n' "$arm"; done > "$AB_GAMES_FILE"
_ab_finish B "ADOPT k=19 mean=200" && ok || ng "selection should transition"
[ "$AB_CALLS" -eq 0 ] && ok || ng "selection must not call underlying finish"
python3 - "$AB_STATE_FILE" "$AB_GAMES_FILE" <<'PY' && ok || ng "confirmation reset state"
import json,sys
st=json.load(open(sys.argv[1]))
assert st["confirmation_phase"] == "confirmation"
assert st["games_recorded"] == 0
assert st["selection_games"] == 4
assert st["verified_improvement"] is False
assert open(sys.argv[2]).read() == ""
PY
sel=$(find "$AB_HISTORY_DIR" -name 'ab_*_selection_games.jsonl' | head -1)
[ -n "$sel" ] && [ "$(grep -c . "$sel")" -eq 4 ] && ok || ng "selection sample archived"

# confirmation B勝利だけ verified=true として underlying finishへ進む。
printf '%s\n' '{"arm":"A"}' '{"arm":"B"}' > "$AB_GAMES_FILE"
python3 - "$AB_STATE_FILE" <<'PY'
import json,sys
p=sys.argv[1]; st=json.load(open(p)); st["games_recorded"]=2; json.dump(st,open(p,"w"))
PY
_ab_finish B "ADOPT k=19 mean=180" && ok || ng "confirmation finish B"
[ "$AB_CALLS" -eq 1 ] && [ "$AB_LAST_WINNER" = B ] && ok || ng "confirmation must call underlying B once"
printf '%s' "$AB_LAST_REASON" | grep -q '^CONFIRMED independent:' && ok || ng "confirmed reason prefix"
python3 - "$AB_STATE_FILE" <<'PY' && ok || ng "verified marker after confirmation"
import json,sys
st=json.load(open(sys.argv[1]))
assert st["verified_improvement"] is True
assert st.get("confirmation_passed_at")
PY

# underlying apply/finish失敗時は verified を取り消してstateを残す。
AB_FINISH_RC=42
AB_CALLS=0
python3 - "$AB_STATE_FILE" <<'PY'
import json,sys
p=sys.argv[1]; st=json.load(open(p)); st["verified_improvement"]=False; json.dump(st,open(p,"w"))
PY
_ab_finish B "ADOPT k=19 mean=170" >/dev/null 2>&1 && ng "failed underlying finish must fail" || ok
python3 - "$AB_STATE_FILE" <<'PY' && ok || ng "verified rolled back after failure"
import json,sys
st=json.load(open(sys.argv[1]))
assert st["verified_improvement"] is False
assert "confirmation_passed_at" not in st
PY

# A verdict は confirmation layer が改変せず既存finishへ渡す。
AB_FINISH_RC=0
AB_CALLS=0
_ab_finish A "REJECT_HARM" && ok || ng "A pass-through"
[ "$AB_CALLS" -eq 1 ] && [ "$AB_LAST_WINNER" = A ] && ok || ng "A verdict underlying call"

if [ "$fail" -ne 0 ]; then
	echo "FAILED: $fail failed, $pass passed"
	exit 1
fi
echo "OK: $pass tests"
