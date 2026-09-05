#!/usr/bin/env bash
# fact-check 候補ごとのタイムアウト解決を検証する。
# 2026-09-05 実測: opencode-go:omen-alpha は共通上限 120s で本番 3/3 タイムアウト
# (隔離実測 22-95s、muse 同条件 35-50s)。omen 専用上限 (既定 360s) への解決と、
# 他候補が共通上限をそのまま受けることをテスト対象にする。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/radio_factcheck.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_radio_factcheck_timeout_for_model()/,/^}/p' "$SRC" > "$TMP/fn_timeout.sh"
[ -s "$TMP/fn_timeout.sh" ] || { echo "not ok - 関数が抽出できない"; exit 1; }
. "$TMP/fn_timeout.sh"

# --- omen は専用上限 (既定 360s) ---
t=$(_radio_factcheck_timeout_for_model "opencode-go:omen-alpha" 120)
[ "$t" = "360" ] && ok "omen default 360" || not_ok "omen default 360 (got $t)"

# --- .env / 環境での上書き ---
t=$(RADIO_FACT_CHECK_OMEN_TIMEOUT_SEC=420 _radio_factcheck_timeout_for_model "opencode-go:omen-alpha" 120)
[ "$t" = "420" ] && ok "omen env override 420" || not_ok "omen env override 420 (got $t)"

# --- 他候補は共通上限をそのまま使う ---
for m in "opencode:muse-spark-1.3-contributor-free" "minimax-api:MiniMax-M3" "codex:minimax-m3" "opencode-go:muse-spark-1.3-contributor"; do
	t=$(_radio_factcheck_timeout_for_model "$m" 120)
	[ "$t" = "120" ] && ok "passthrough $m" || not_ok "passthrough $m (got $t)"
done

# --- 不明エージェントも共通上限 ---
t=$(_radio_factcheck_timeout_for_model "unknown:agent" 90)
[ "$t" = "90" ] && ok "unknown passthrough" || not_ok "unknown passthrough (got $t)"

if [ "$FAIL" = "0" ]; then
	echo "all ok"
else
	echo "FAILED"
	exit 1
fi
