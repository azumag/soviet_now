#!/usr/bin/env bash
# podcast_gc.sh の保持日数ロジックを検証する (実ファイルは触らない: 一時ディレクトリのみ)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GC="$ROOT/tools/podcast_gc.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# N 日前の日付 (BSD/GNU 両対応)
days_ago() { date -v-"$1"d "+%Y-%m-%d" 2>/dev/null || date -d "$1 days ago" "+%Y-%m-%d"; }
# ファイルの mtime を N 日前にする
age_file() { touch -t "$(date -v-"$2"d "+%Y%m%d0000" 2>/dev/null || date -d "$2 days ago" "+%Y%m%d0000")" "$1"; }

OUT="$TMP/podcast"
mkdir -p "$OUT"

mkep() { # mkep <days_ago> <published:0|1>
  local iso; iso="$(days_ago "$1")"
  head -c 1024 /dev/zero > "$OUT/$iso.mp4"
  head -c 512 /dev/zero > "$OUT/$iso.mp3"
  echo "dummy" > "$OUT/$iso.script.txt"
  [ "$2" = "1" ] && echo '{"video_id":"x"}' > "$OUT/$iso.publish.json"
  age_file "$OUT/$iso.mp4" "$1"
  age_file "$OUT/$iso.mp3" "$1"
  echo "$iso"
}

TODAY=$(mkep 0 1)
D2=$(mkep 2 1)
D3=$(mkep 3 1)
D5=$(mkep 5 1)
D10=$(mkep 10 1)
D6_UNPUB=$(mkep 6 0)   # 公開済みと日付が重ならないようずらす
D12_UNPUB=$(mkep 12 0)

# 1. dry-run は消さない
out=$(zsh "$GC" --out-dir "$OUT" --dry-run 2>&1)
if [ -f "$OUT/$D10.mp4" ]; then ok "dry-run does not delete"; else not_ok "dry-run does not delete"; fi
if echo "$out" | grep -q "\[dry-run\] 削除対象: $D10.mp4"; then ok "dry-run lists old mp4"; else not_ok "dry-run lists old mp4"; echo "$out"; fi

# 2. 既定 (3日) で 3日以内は残り、それより古い公開済みは消える
out=$(zsh "$GC" --out-dir "$OUT" 2>&1)
for keep in "$TODAY" "$D2" "$D3"; do
  if [ -f "$OUT/$keep.mp4" ]; then ok "keeps $keep.mp4 (<= 3 days)"; else not_ok "keeps $keep.mp4"; echo "$out"; fi
done
for gone in "$D5" "$D10"; do
  if [ -f "$OUT/$gone.mp4" ]; then not_ok "deletes $gone.mp4 (> 3 days, published)"; echo "$out"; else ok "deletes $gone.mp4 (> 3 days, published)"; fi
done

# 3. 未公開は 7 日まで残す / 7 日を過ぎたら消える
if [ -f "$OUT/$D6_UNPUB.mp4" ]; then ok "keeps unpublished at 6 days (<= 7)"; else not_ok "keeps unpublished at 6 days"; echo "$out"; fi
if [ -f "$OUT/$D12_UNPUB.mp4" ]; then not_ok "deletes unpublished at 12 days (> 7)"; else ok "deletes unpublished at 12 days (> 7)"; fi

# 4. mp3 と台本は既定では消さない
if [ -f "$OUT/$D10.mp3" ]; then ok "keeps mp3 by default"; else not_ok "keeps mp3 by default"; fi
if [ -f "$OUT/$D10.script.txt" ]; then ok "keeps script.txt"; else not_ok "keeps script.txt"; fi

# 5. PODCAST_GC_SUFFIXES で mp3 も対象にできる
out=$(PODCAST_GC_SUFFIXES=".mp4 .mp3" zsh "$GC" --out-dir "$OUT" 2>&1)
if [ -f "$OUT/$D10.mp3" ]; then not_ok "deletes mp3 when suffix given"; echo "$out"; else ok "deletes mp3 when suffix given"; fi
if [ -f "$OUT/$D2.mp3" ]; then ok "keeps recent mp3 when suffix given"; else not_ok "keeps recent mp3 when suffix given"; fi

# 6. --days で保持日数を変えられる
out=$(zsh "$GC" --out-dir "$OUT" --days 1 2>&1)
if [ -f "$OUT/$D2.mp4" ]; then not_ok "--days 1 deletes 2-day-old"; echo "$out"; else ok "--days 1 deletes 2-day-old"; fi
if [ -f "$OUT/$TODAY.mp4" ]; then ok "--days 1 keeps today"; else not_ok "--days 1 keeps today"; fi

# 7. 最近作り直した回は日付が古くても消さない (mtime ガード)
OLD_ISO="$(days_ago 30)"
head -c 1024 /dev/zero > "$OUT/$OLD_ISO.mp4"
echo '{"video_id":"x"}' > "$OUT/$OLD_ISO.publish.json"   # mtime は今 = 作り直した直後
out=$(zsh "$GC" --out-dir "$OUT" 2>&1)
if [ -f "$OUT/$OLD_ISO.mp4" ]; then ok "keeps freshly re-rendered old-dated mp4 (mtime guard)"; else not_ok "keeps freshly re-rendered old-dated mp4"; echo "$out"; fi

# 8. 出力ディレクトリが無くても落ちない
zsh "$GC" --out-dir "$TMP/nonexistent" >/dev/null 2>&1
if [ $? -eq 0 ]; then ok "missing out-dir exits 0"; else not_ok "missing out-dir exits 0"; fi

if [ "$FAIL" = "0" ]; then echo "ALL PASS"; else echo "FAILED"; fi
exit "$FAIL"
