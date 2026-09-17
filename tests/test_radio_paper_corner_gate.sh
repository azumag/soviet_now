#!/usr/bin/env bash
# PAPER番組枠中の新規ラジオ生成抑止を検証する。
# 実コードをそのまま抽出して source する（scheduler.sh の抑止判定の実装をテスト対象にする）。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHED_SRC="$ROOT/broadcast/scheduler.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_paper_corner_active()/,/^}/p' "$SCHED_SRC" > "$TMP/fn_gate.sh"
[ -s "$TMP/fn_gate.sh" ] || { not_ok "extract _paper_corner_active"; exit 1; }

TMP_MARKERS_DIR="$TMP/markers"
mkdir -p "$TMP_MARKERS_DIR" "$TMP/tmp"

log() { printf 'LOG: %s\n' "$*" >> "$TMP/log.txt"; }
. "$TMP/fn_gate.sh"

# フラグは soren root 相対 tmp/.paper_corner_active を見る
cd "$TMP" || exit 1

NOW=$(date +%s)

# --- フラグ無し → 許可 ---
rm -f tmp/.paper_corner_active "$TMP_MARKERS_DIR/.radio_paper_corner_gate_active"
if _paper_corner_active; then
	not_ok "no flag: should allow"
else
	ok "no flag: allow"
fi

# --- 未来の ends_at → ブロック + マーカー + ログ1回 ---
python3 -c "import json; json.dump({'date':'2026-09-17','ends_at':$((NOW + 1800))}, open('tmp/.paper_corner_active','w'))"
rm -f "$TMP_MARKERS_DIR/.radio_paper_corner_gate_active" "$TMP/log.txt"
if _paper_corner_active; then
	ok "active flag: blocked"
else
	not_ok "active flag: blocked"
fi
[ -f "$TMP_MARKERS_DIR/.radio_paper_corner_gate_active" ] && ok "active flag: marker created" || not_ok "active flag: marker created"
[ "$(grep -c 'PAPERコーナー番組枠' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ] && ok "active flag: logged once" || not_ok "active flag: logged once"
if _paper_corner_active; then
	[ "$(grep -c 'PAPERコーナー番組枠' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ] && ok "active flag: log not repeated" || not_ok "active flag: log not repeated"
else
	not_ok "active flag: still blocked on repeat"
fi

# --- 過去の ends_at → 許可 + マーカー掃除 ---
python3 -c "import json; json.dump({'date':'2026-09-17','ends_at':$((NOW - 60))}, open('tmp/.paper_corner_active','w'))"
touch "$TMP_MARKERS_DIR/.radio_paper_corner_gate_active"
if _paper_corner_active; then
	not_ok "expired flag: should allow"
else
	ok "expired flag: allow"
fi
[ ! -f "$TMP_MARKERS_DIR/.radio_paper_corner_gate_active" ] && ok "expired flag: marker cleaned" || not_ok "expired flag: marker cleaned"

# --- 破損フラグ → 許可 (fail-open) ---
printf 'not-json{{{' > tmp/.paper_corner_active
if _paper_corner_active; then
	not_ok "corrupt flag: should allow"
else
	ok "corrupt flag: allow (fail-open)"
fi

# --- ends_at 欠落 → 許可 (fail-open) ---
printf '{"date":"2026-09-17"}' > tmp/.paper_corner_active
if _paper_corner_active; then
	not_ok "missing ends_at: should allow"
else
	ok "missing ends_at: allow (fail-open)"
fi

if [ "$FAIL" = "0" ]; then
	echo "PASS: paper corner radio gate"
else
	echo "FAIL: paper corner radio gate"
fi
exit "$FAIL"
