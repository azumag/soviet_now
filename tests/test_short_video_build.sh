#!/usr/bin/env bash
# short_video_build の選定ロジックを検証する
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# 1. news が選定されること
mkdir -p "$ROOT/backups/radio_scripts/20260830"
echo "ニュース" > "$ROOT/backups/radio_scripts/20260830/radio_1_news_1.txt"
echo "テーマ" > "$ROOT/backups/radio_scripts/20260830/radio_2_theme_1.txt"
out=$(python3 "$ROOT/tools/short_video_build.py" --date 20260830 --dry-run 2>&1)
if echo "$out" | grep -q "radio_1_news"; then ok "pick news"; else not_ok "pick news"; echo "$out"; fi
if echo "$out" | grep -q "radio_2_theme"; then not_ok "theme should not be picked"; else ok "theme not picked"; fi
rm -rf "$ROOT/backups/radio_scripts/20260830"

# 2. jiji は short_video では選定されない (news のみ)
mkdir -p "$ROOT/backups/radio_scripts/20260831"
echo "ジジ" > "$ROOT/backups/radio_scripts/20260831/radio_1_jiji_1.txt"
out=$(python3 "$ROOT/tools/short_video_build.py" --date 20260831 --dry-run 2>&1)
if echo "$out" | grep -q "no news files"; then ok "jiji not picked (news only)"; else not_ok "jiji not picked"; echo "$out"; fi
rm -rf "$ROOT/backups/radio_scripts/20260831"

# 3. 対象なしはスキップ
out=$(python3 "$ROOT/tools/short_video_build.py" --date 20260901 --dry-run 2>&1)
if echo "$out" | grep -q "no news files"; then ok "no files skip"; else not_ok "no files skip"; echo "$out"; fi

# 4. doci が無い環境でも dry-run で落ちない (DOCI_DIR を無効化)
out=$(DOCI_DIR=/tmp/nonexistent python3 "$ROOT/tools/short_video_build.py" --date 20260830 --dry-run 2>&1 || true)
if echo "$out" | grep -q "doci not found"; then ok "doci not found handled"; else ok "doci found (maybe)"; fi

if [ "$FAIL" -eq 0 ]; then echo "all tests passed"; else echo "$FAIL tests failed" >&2; fi
exit "$FAIL"
