#!/usr/bin/env bash
# issue #132 P0-1: A/B の永続記録に建国・段階到達・終了理由が入ること。
# game_history は剪定されるため、実験 ledger 側に持たないと後から辿れない。
set -u
cd "$(dirname "$0")/.." || exit 1
fail=0
t() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: expected [$3] got [$2]"; fail=1; fi; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export TMP_STATE_DIR="$tmp"
log() { :; }
AB_STATE_FILE="$tmp/ab_state.json"; AB_GAMES_FILE="$tmp/ab_games.jsonl"; AB_ABORT_FILE="$tmp/ab_abort"
AB_ALT_FILE="$tmp/alt.py"; STRATEGY_FILE="strategy.py"
printf '{"a_hash":"aaaaaaaaaaaa","b_hash":"bbbbbbbbbbbb","pattern":"ABBA","games_recorded":0}' > "$AB_STATE_FILE"
# shellcheck source=/dev/null
. strategy/ab_interleave.sh

arc="$tmp/archive.jsonl"
python3 - "$arc" <<'PY'
import json,sys
rows=[]
for turn in range(1,6):
    pieces=[{"id":1,"type":3,"x":0.0,"y":-4.0}]
    if turn>=3: pieces.append({"id":2,"type":15,"x":1.0,"y":-3.0})
    if turn>=5: pieces.append({"id":3,"type":16,"x":-1.0,"y":-3.0})
    rows.append({"turn":turn,"piece_count":len(pieces),"strategy_hash":"bbbbbbbbbbbb",
                 "state_snapshot":{"pieces":pieces},"deadline_crossed":False,
                 "analyzer_modes":{"wall_clamp":1,"landing_arc":0}})
open(sys.argv[1],"w").write("\n".join(json.dumps(r) for r in rows))
PY

AB_ARM="B"; AB_IDX=1; AB_HASH="bbbbbbbbbbbb"
_ab_record_game 1234 5678 5 "$arc" "true" "true"
read -r line < "$AB_GAMES_FILE"
get() { printf '%s' "$line" | python3 -c "import json,sys;print(json.load(sys.stdin).get('$1'))"; }
t "soviet_created を記録" "$(get soviet_created)" "True"
t "russia_created を記録" "$(get russia_created)" "True"
t "T15 初到達ターン" "$(get first_turn_t15)" "3"
t "T16 初到達ターン" "$(get first_turn_t16)" "5"
t "終了理由=soviet" "$(get end_reason)" "soviet"
t "終了時の駒数" "$(get final_piece_count)" "3"
t "analyzer_modes を保持" "$(printf '%s' "$line" | python3 -c "import json,sys;print((json.load(sys.stdin).get('analyzer_modes') or {}).get('landing_arc'))")" "0"

# 建国していない場合
: > "$AB_GAMES_FILE"
AB_IDX=2
_ab_record_game 100 200 5 "$arc" "false" "false"
read -r line < "$AB_GAMES_FILE"
t "非建国では soviet_created=False" "$(get soviet_created)" "False"
t "非建国の終了理由" "$(get end_reason)" "board_full_or_other"

# 旧シグネチャ (引数 4 つ) でも壊れない
: > "$AB_GAMES_FILE"
AB_IDX=3
_ab_record_game 100 200 5 "$arc"
read -r line < "$AB_GAMES_FILE"
t "旧シグネチャ互換 (soviet は None)" "$(get soviet_created)" "None"
t "旧シグネチャでも段階到達は入る" "$(get first_turn_t15)" "3"

exit $fail
