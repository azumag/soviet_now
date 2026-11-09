#!/usr/bin/env bash
# docich#3712 (i)-A の汎用化: 外部ゲーム（エミュレータ/端末）コーナー中の
# prepass bypass 判定を検証する。既定 ON。unreadable / corrupt / phase 非 ready /
# 非外部アダプタ / stale はすべて fail-open（prepass を通す = 1）で、prepass を
# 半永久的に止めないこと。fresh な外部ゲームのときだけ bypass (0) になること。
# radio_engine 側の配線（既存の main 直行フォールバックへ流れること）を確認する。
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
unset RADIO_PREPASS_CORNER_BYPASS RADIO_PREPASS_EXTERNAL_ADAPTERS 2>/dev/null || true

_write_ctx() {
	# $1 phase, $2 game, $3 adapter, $4 started_at
	python3 - "$1" "$2" "$3" "$4" "$CTX" <<'PY'
import json, sys
phase, game, adapter, started_at, path = sys.argv[1:6]
active = {"game": game, "started_at": started_at}
if adapter:
    active["adapter"] = adapter
data = {"schema_version": 2, "revision": 1, "phase": phase,
        "active": active if game else None, "updated_at": started_at}
json.dump(data, open(path, "w"))
PY
}

_now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
_iso_ago() { date -u -d "$1 ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-"$1"S +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "$(_now_iso)"; }

# --- 既定 ON: fresh な外部ゲーム（retroarch）で bypass ---
_write_ctx ready hanjuku-hero retroarch "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	ok "default ON: retroarch corner bypasses prepass"
else
	not_ok "default ON: retroarch corner should bypass"
fi

# --- 既定 ON: cli も外部ゲームとして bypass ---
_write_ctx ready nsnake cli "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	ok "cli corner bypasses prepass"
else
	not_ok "cli corner should bypass"
fi

# --- Soren 本体は対象外 ---
_write_ctx ready sorengame game "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "sorengame should keep prepass"
else
	ok "sorengame: prepass runs"
fi

# --- docich のプログラムコーナーは対象外 ---
_write_ctx ready paper-view paper "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "paper corner should keep prepass"
else
	ok "paper corner: prepass runs"
fi

# --- adapter が無い active は対象外（fail-open） ---
_write_ctx ready hanjuku-hero "" "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "missing adapter should keep prepass"
else
	ok "missing adapter: prepass runs"
fi

# --- 緊急停止スイッチ ---
RADIO_PREPASS_CORNER_BYPASS=0
_write_ctx ready hanjuku-hero retroarch "$(_now_iso)"
if RADIO_PREPASS_CORNER_BYPASS=0 _radio_prepass_should_bypass_for_corner; then
	not_ok "emergency switch=0 should keep prepass"
else
	ok "emergency switch=0: prepass runs"
fi

# --- 対象アダプタの上書き ---
_write_ctx ready hanjuku-hero retroarch "$(_now_iso)"
if RADIO_PREPASS_EXTERNAL_ADAPTERS=cli _radio_prepass_should_bypass_for_corner; then
	not_ok "adapter override should exclude retroarch"
else
	ok "adapter override: retroarch excluded"
fi

# --- unreadable -> allow (fail-open) ---
rm -f "$CTX"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "unreadable should fail-open (allow)"
else
	ok "unreadable: fail-open (allow)"
fi

# --- corrupt json -> allow ---
printf 'not-json{{{' > "$CTX"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "corrupt should fail-open (allow)"
else
	ok "corrupt: fail-open (allow)"
fi

# --- phase 非 ready -> allow ---
_write_ctx switching hanjuku-hero retroarch "$(_now_iso)"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "phase=switching should allow"
else
	ok "phase switching: allow"
fi

# --- stale started_at -> allow ---
_write_ctx ready hanjuku-hero retroarch "$(_iso_ago '3 hours')"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "stale started_at should fail-open (allow)"
else
	ok "stale started_at: fail-open (allow)"
fi

# --- mtime stale -> allow ---
_write_ctx ready hanjuku-hero retroarch "$(_now_iso)"
python3 -c "import os,time; os.utime('$CTX', (time.time()-10800, time.time()-10800))"
if _radio_prepass_should_bypass_for_corner; then
	not_ok "stale mtime should fail-open (allow)"
else
	ok "stale mtime: fail-open (allow)"
fi

# --- config 既定値が ON であること ---
if grep -q 'RADIO_PREPASS_CORNER_BYPASS:-1' "$CONFIG_SRC"; then
	ok "config default is ON"
else
	not_ok "config default is ON"
fi
if grep -q 'RADIO_PREPASS_EXTERNAL_ADAPTERS:-retroarch cli' "$CONFIG_SRC"; then
	ok "external adapters default is retroarch cli"
else
	not_ok "external adapters default"
fi

# --- 半熟というゲーム名が判定コードに残っていないこと ---
if grep -q 'hanjuku-hero' "$STATE_SRC"; then
	not_ok "helper must not name hanjuku-hero"
else
	ok "helper has no game-specific name"
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
if printf '%s\n' "$ENGINE_FN" | grep -q 'external game corner'; then
	ok "engine log names an external game corner"
else
	not_ok "engine log names an external game corner"
fi

if [ "$FAIL" = "0" ]; then
	echo "PASS: prepass corner bypass"
else
	echo "FAIL: prepass corner bypass"
fi
exit "$FAIL"
