#!/usr/bin/env bash
# 番組枠コーナー（AI探偵ミステリー / PAPER）中のラジオ生成・再生抑止を検証する。
# 実コードをそのまま抽出して source する（radio_state.sh の共有判定と、
# scheduler.sh / 再生経路の呼び出しをテスト対象にする）。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_SRC="$ROOT/broadcast/radio_state.sh"
SCHED_SRC="$ROOT/broadcast/scheduler.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_program_corner_active()/,/^}/p' "$STATE_SRC" > "$TMP/fn_gate.sh"
[ -s "$TMP/fn_gate.sh" ] || { not_ok "extract _program_corner_active"; exit 1; }

TMP_MARKERS_DIR="$TMP/markers"
mkdir -p "$TMP_MARKERS_DIR" "$TMP/tmp"

log() { printf 'LOG: %s\n' "$*" >> "$TMP/log.txt"; }
. "$TMP/fn_gate.sh"

# フラグは soren root 相対 tmp/ を見る
cd "$TMP" || exit 1

NOW=$(date +%s)
_new_flag() { python3 -c "import json,sys; json.dump({'ends_at':$2}, open(sys.argv[1],'w'))" "$1" "$2"; }

# --- フラグ無し → 許可 ---
rm -f tmp/.program_corner_active tmp/.paper_corner_active
if _program_corner_active; then
	not_ok "no flag: should allow"
else
	ok "no flag: allow"
fi

# --- 共有フラグ（新）が有効 → ブロック ---
_new_flag tmp/.program_corner_active $((NOW + 1800))
if _program_corner_active; then
	ok "program flag active: blocked"
else
	not_ok "program flag active: blocked"
fi

# --- 旧 PAPER フラグのみ有効 → 後方互換でブロック ---
rm -f tmp/.program_corner_active
_new_flag tmp/.paper_corner_active $((NOW + 1800))
if _program_corner_active; then
	ok "legacy paper flag active: blocked"
else
	not_ok "legacy paper flag active: blocked"
fi

# --- 期限切れの新フラグ + 有効な旧フラグ → ブロック ---
_new_flag tmp/.program_corner_active $((NOW - 60))
_new_flag tmp/.paper_corner_active $((NOW + 1800))
if _program_corner_active; then
	ok "expired program + active paper: blocked"
else
	not_ok "expired program + active paper: blocked"
fi

# --- 有効な新フラグ + 期限切れの旧フラグ → ブロック ---
_new_flag tmp/.program_corner_active $((NOW + 1800))
_new_flag tmp/.paper_corner_active $((NOW - 60))
if _program_corner_active; then
	ok "active program + expired paper: blocked"
else
	not_ok "active program + expired paper: blocked"
fi

# --- 両方とも期限切れ → 許可 ---
_new_flag tmp/.program_corner_active $((NOW - 60))
if _program_corner_active; then
	not_ok "expired flags: should allow"
else
	ok "expired flags: allow"
fi

# --- 破損フラグ → 許可 (fail-open) ---
printf 'not-json{{{' > tmp/.program_corner_active
rm -f tmp/.paper_corner_active
if _program_corner_active; then
	not_ok "corrupt flag: should allow"
else
	ok "corrupt flag: allow (fail-open)"
fi

# --- ends_at 欠落 → 許可 (fail-open) ---
printf '{"date":"2026-09-17"}' > tmp/.program_corner_active
if _program_corner_active; then
	not_ok "missing ends_at: should allow"
else
	ok "missing ends_at: allow (fail-open)"
fi

# --- 生成側: scheduler が共有判定を使う ---
if grep -q '_program_corner_active' "$SCHED_SRC"; then
	ok "scheduler uses _program_corner_active"
else
	not_ok "scheduler uses _program_corner_active"
fi

# --- 再生側: deferred 再生の入口で同じ判定を先に見る ---
PLAY_FN="$(sed -n '/^_play_deferred_radio_queue_once()/,/^}/p' "$STATE_SRC")"
if printf '%s\n' "$PLAY_FN" | grep -q '_program_corner_active'; then
	ok "deferred playback is gated"
else
	not_ok "deferred playback is gated"
fi
if printf '%s\n' "$PLAY_FN" | grep -q 'say_enqueue'; then
	GATE_LINE=$(printf '%s\n' "$PLAY_FN" | grep -n '_program_corner_active' | head -n 1 | cut -d: -f1)
	PLAY_LINE=$(printf '%s\n' "$PLAY_FN" | grep -n 'say_enqueue' | head -n 1 | cut -d: -f1)
	if [ -n "$GATE_LINE" ] && [ -n "$PLAY_LINE" ] && [ "$GATE_LINE" -lt "$PLAY_LINE" ]; then
		ok "deferred gate runs before playback"
	else
		not_ok "deferred gate runs before playback"
	fi
fi

if [ "$FAIL" = "0" ]; then
	echo "PASS: program corner radio gate"
else
	echo "FAIL: program corner radio gate"
fi
exit "$FAIL"
