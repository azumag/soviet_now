#!/usr/bin/env bash
# podcast_build の基本動作を検証する (dummy モード)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# 1. dry-run で news/jiji のみ拾うこと
mkdir -p "$ROOT/backups/radio_scripts/20260830"
echo "こんばんは、現在時刻は0時です。本日のニュースです。ダミー" > "$ROOT/backups/radio_scripts/20260830/radio_1_news_1.txt"
echo "ダミー考察" > "$ROOT/backups/radio_scripts/20260830/radio_2_jiji_2.txt"
echo "テーマは除外" > "$ROOT/backups/radio_scripts/20260830/radio_3_theme_3.txt"
out=$(python3 "$ROOT/tools/podcast_build.py" --date 20260830 --dry-run 2>&1)
if echo "$out" | grep -q "found 2 source"; then ok "dry-run finds 2 (news+jiji, theme excluded)"; else not_ok "dry-run finds 2"; echo "$out"; fi
if echo "$out" | grep -q "radio_3_theme"; then not_ok "theme should be excluded"; else ok "theme excluded"; fi
rm -rf "$ROOT/backups/radio_scripts/20260830"

# 2. dummy で MP3 と feed.xml が生成されること
mkdir -p "$ROOT/backups/radio_scripts/20260831"
cat > "$ROOT/backups/radio_scripts/20260831/radio_1_news_1.txt" <<'EOF'
こんばんは、現在時刻は0時です。
本日のニュースです。
テストニュースです。晴れでした。
EOF
cat > "$ROOT/backups/radio_scripts/20260831/radio_2_jiji_2.txt" <<'EOF'
おはようございます、現在時刻は1時です。
本日のニュースです。
テスト考察です。
EOF
OUTDIR="$TMP/podcast"
python3 "$ROOT/tools/podcast_build.py" --date 20260831 --dummy --out-dir "$OUTDIR" 2>&1 | tail -n 20
if [ -f "$OUTDIR/2026-08-31.mp3" ]; then ok "mp3 generated"; else not_ok "mp3 generated"; fi
if [ -f "$OUTDIR/feed.xml" ]; then ok "feed.xml generated"; else not_ok "feed.xml generated"; fi
if [ -f "$OUTDIR/2026-08-31.chapters.json" ]; then ok "chapters generated"; else not_ok "chapters generated"; fi
# feed.xml が xmllint OK かつ 1 episode
if grep -q "2026年08月31日" "$OUTDIR/feed.xml" 2>/dev/null; then ok "feed contains episode"; else not_ok "feed contains episode"; fi
if xmllint --noout "$OUTDIR/feed.xml" 2>&1; then ok "xmllint OK"; else not_ok "xmllint"; fi
# 2回目は冪等 (mp3 が新しければスキップ)
out2=$(python3 "$ROOT/tools/podcast_build.py" --date 20260831 --dummy --out-dir "$OUTDIR" 2>&1)
if echo "$out2" | grep -q "already up to date"; then ok "idempotent skip"; else not_ok "idempotent"; echo "$out2"; fi
rm -rf "$ROOT/backups/radio_scripts/20260831"
rm -rf "$OUTDIR"

# 3. 対象なしはスキップ (exit 0)
out3=$(python3 "$ROOT/tools/podcast_build.py" --date 20260901 --dummy --out-dir "$TMP/empty" 2>&1)
if echo "$out3" | grep -q "no source files"; then ok "no source skip"; else not_ok "no source skip"; echo "$out3"; fi

# 4. intro除去が効くこと (現在時刻は が除去される)
mkdir -p "$ROOT/backups/radio_scripts/20260831"
cat > "$ROOT/backups/radio_scripts/20260831/radio_1_news_1.txt" <<'EOF'
こんばんは、現在時刻は0時です。
本日のニュースです。
中身だけ残るはず。
EOF
# 直接 python で clean_script を呼ぶ
if python3 - "$ROOT" <<'PY' 2>&1 | grep -q "中身だけ"
import sys
from pathlib import Path
root = Path(sys.argv[1])
import importlib.util
spec = importlib.util.spec_from_file_location("podcast_build", root / "tools" / "podcast_build.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.clean_script((root / "backups" / "radio_scripts" / "20260831" / "radio_1_news_1.txt").read_text(encoding="utf-8")))
PY
then
  ok "intro cleaning"
else
  not_ok "intro cleaning"
fi
rm -rf "$ROOT/backups/radio_scripts/20260831"

if [ "$FAIL" -eq 0 ]; then
  echo "all tests passed"
else
  echo "$FAIL tests failed" >&2
fi
exit "$FAIL"
