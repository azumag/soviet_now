#!/usr/bin/env bash
# A/B の腕ごと環境変数 (a_env / b_env → AB_EXTRA_ENV) の検証。
# 既定 (state に a_env/b_env なし) では AB_EXTRA_ENV が空 = 従来と同じ挙動であることも固定する。
set -u
cd "$(dirname "$0")/.." || exit 1
fail=0
t() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: expected [$3] got [$2]"; fail=1; fi; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export TMP_STATE_DIR="$tmp"
mkdir -p "$tmp"
log() { :; }
AB_STATE_FILE="$tmp/ab_state.json"
AB_GAMES_FILE="$tmp/ab_games.jsonl"
AB_ABORT_FILE="$tmp/ab_abort"
AB_ALT_FILE="$tmp/alt.py"
STRATEGY_FILE="strategy.py"
# shellcheck source=/dev/null
. strategy/ab_interleave.sh

mk_state() {
  python3 - "$AB_STATE_FILE" "$1" "$2" <<'PY'
import json,sys
st={"a_hash":"aaaaaaaaaaaa","b_hash":"bbbbbbbbbbbb","pattern":"ABBA","games_recorded":0}
if sys.argv[2]: st["a_env"]=sys.argv[2]
if sys.argv[3]: st["b_env"]=sys.argv[3]
json.dump(st,open(sys.argv[1],"w"))
PY
}

mk_state "" ""
AB_EXTRA_ENV="preset"; AB_ARM="A"
AB_EXTRA_ENV=$(_ab_state_get a_env); case "$AB_EXTRA_ENV" in null|none) AB_EXTRA_ENV="" ;; esac
t "a_env 未設定なら空" "$AB_EXTRA_ENV" ""

mk_state "ANALYZE_BOARD_LANDING_ARC=3" "ANALYZE_BOARD_LANDING_ARC=0"
v=$(_ab_state_get a_env); t "a_env を読める" "$v" "ANALYZE_BOARD_LANDING_ARC=3"
v=$(_ab_state_get b_env); t "b_env を読める" "$v" "ANALYZE_BOARD_LANDING_ARC=0"

# 形式チェック: 不正な値は使わない
bad='rm -rf /'
if printf '%s' "$bad" | grep -Eq '^([A-Z][A-Z0-9_]*=[A-Za-z0-9_.,:/-]*)( [A-Z][A-Z0-9_]*=[A-Za-z0-9_.,:/-]*)*$'; then r=accept; else r=reject; fi
t "不正な env 文字列は拒否" "$r" "reject"
good='ANALYZE_BOARD_LANDING_ARC=3 FOO_BAR=1'
if printf '%s' "$good" | grep -Eq '^([A-Z][A-Z0-9_]*=[A-Za-z0-9_.,:/-]*)( [A-Z][A-Z0-9_]*=[A-Za-z0-9_.,:/-]*)*$'; then r=accept; else r=reject; fi
t "正しい env 文字列は許可" "$r" "accept"

exit $fail
