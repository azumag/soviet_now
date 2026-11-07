#!/usr/bin/env bash
# docich#3712 (i)-A: 半熟コーナー(active)中の prepass bypass 判定を検証する。
# 既定 OFF / unreadable / corrupt / 非 hanjuku / stale はすべて fail-open
# (prepass を通す = 1) で、prepass を半永久的に止めないこと。fresh な hanjuku
# active のときだけ bypass (0) になること。radio_engine 側の配線（既存の
# main 直行フォールバックへ流れること）を確認する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_SRC="$ROOT/broadcast/radio_state.sh"
ENGINE_SRC="$ROOT/broadcast/radio_engine.sh"
CONFIG_SRC="$ROOT/core/config.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_radio_prepass_should_bypass_for_corner()/,/^}/p' "$STATE_SRC" > "$TMP/fn_bypass.sh"
[ -s "$TMP/fn_bypass.sh" ] || { not_ok "extract helper"; exit 1; }

log() { :; }
. "$TMP/fn_bypass.sh"

CTX="$TMP/game_switch.json"
export SOREN_ACTIVE_GAME_CONTEXT_FILE="$CTX"
export RADIO_PREPASS_CORNER_FRESH_SEC=7200

_write_ctx() {
	# $1 phase, $2 game, $3 started_at
	python3 - "$1" "$2" "$3" "$CTX" <<'PY'
import json, sys
phase, game, started_at, path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
data = {"schema_version": 2, "revision": 1, "phase": phase,
        "active": {"game": game, "started_at": started_at} if game else None,
        "updated_at": started_at}
json.dump(data, open(path, "w"))
PY
}

_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
_iso_ago() { date -u -d "$1 ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-"$1"S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "$(_now_iso)"; }

# --- 既定 OFF (env 未設定) で fresh hanjuku でも allow ---
RADIO_PREPASS_CORNER_BYPASS_ENABLED=0 _write_ctx ready hanjuku-hero "$(_now_iso)"
if RADIO_PREPASS_CORNER_BYPASS_ENABLED=0 _radio_prepass_should_bypass_for_corner; then
	not_ok "default OFF: hanjuku active should not bypass"
else
	ok "default OFF: allow (no bypass)"
fi

# --- ON + unreadable -> allow (fail-open) ---
RADIO_PREPASS_CORNER_BYPASS_ENABLED=1
rm -f "$CTX"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "unreadable should fail-open (allow)"
else
	ok "unreadable: fail-open (allow)"
fi

# --- ON + corrupt json -> allow ---
RADIO_PREPASS_CORNER_BYPASS_ENABLED=1
printf 'not-json{{{' > "$CTX"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "corrupt should fail-open (allow)"
else
	ok "corrupt: fail-open (allow)"
fi

# --- ON + phase 非 ready -> allow ---
_write_ctx switching hanjuku-hero "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "phase=switching should allow"
else
	ok "phase switching: allow"
fi

# --- ON + active 非 hanjuku -> allow ---
_write_ctx ready sorengame "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "active=sorengame should allow"
else
	ok "active sorengame: allow"
fi

# --- ON + fresh hanjuku active -> bypass (0) ---
_write_ctx ready hanjuku-hero "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	ok "fresh hanjuku active: bypass"
else
	not_ok "fresh hanjuku active should bypass"
fi

# --- ON + stale hanjuku (started_at が鮮度上限超) -> allow ---
_write_ctx ready hanjuku-hero "$(_iso_ago '3 hours')"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "stale started_at should fail-open (allow)"
else
	ok "stale started_at: fail-open (allow)"
fi

# --- ON + mtime stale -> allow ---
_write_ctx ready hanjuku-hero "$(_now_iso)"
python3 -c "import os,time; os.utime('$CTX', (time.time()-10800, time.time()-10800))"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "stale mtime should fail-open (allow)"
else
	ok "stale mtime: fail-open (allow)"
fi

# --- config 既定値が OFF であること ---
if grep -q 'RADIO_PREPASS_CORNER_BYPASS_ENABLED:-0' "$CONFIG_SRC"; then
	ok "config default is OFF"
else
	not_ok "config default is OFF"
fi

# --- radio_engine 配線: bypass ログが既存 main 直行経路の前段にあること ---
ENGINE_FN="$(sed -n '/^_radio_generate_and_play()/,/^}/p' "$ENGINE_SRC")"
if printf '%s\n' "$ENGINE_FN" | grep -q '_radio_prepass_should_bypass_for_corner'; then
	ok "engine calls bypass helper"
else
	not_ok "engine calls bypass helper"
fi
if printf '%s\n' "$ENGINE_FN" | grep -q 'prepass empty/failed -> direct'; then
	ok "engine keeps existing direct-main fallback"
else
	not_ok "engine keeps existing direct-main fallback"
fi

if [ "$FAIL" = "0" ]; then
	echo "PASS: prepass corner bypass"
else
	echo "FAIL: prepass corner bypass"
fi
exit "$FAIL"
