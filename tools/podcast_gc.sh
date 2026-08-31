#!/bin/zsh
# tools/podcast_gc.sh - ポッドキャストの生成物を N 日で捨てる (docich#10)
#
# 1 本の動画が 300MB 前後 (2026-08-25 分の実測 299MB) あり、毎日積むと月 9GB になる。
# YouTube へ上げた後のローカル mp4 は原稿から再生成できるので、既定 3 日で消す。
# (ショート動画側は doci の output_cleanup.py が「投稿成功したら媒体を消す」で処理している。
#  podcast は公開後も動画を手元で確認したいことがあるので、日数ベースにする)
#
# 対象は output/podcast/<YYYY-MM-DD><suffix>。既定の suffix は .mp4 のみ
# (PODCAST_GC_SUFFIXES=".mp4 .mp3" のように増やせる)。
# 未公開 (<日付>.publish.json が無い) の回は手で公開できるよう
# PODCAST_GC_UNPUBLISHED_DAYS (既定 7 日) まで残す。
# 台本 (.script.txt) / メタ (.meta.json / .chapters.json / .segments.json) / feed.xml は
# 小さく再生成の入力になるので消さない。
#
# 使い方:
#   ./tools/podcast_gc.sh                        # 既定 (3 日を過ぎた公開済み mp4 を削除)
#   ./tools/podcast_gc.sh --dry-run              # 消さずに一覧だけ
#   ./tools/podcast_gc.sh --days 1               # 保持日数を変える
#   PODCAST_GC_SUFFIXES=".mp4 .mp3" ./tools/podcast_gc.sh

PROJ="${0:A:h:h}"            # games/soviet_now

DAYS="${PODCAST_RETENTION_DAYS:-3}"
UNPUB_DAYS="${PODCAST_GC_UNPUBLISHED_DAYS:-7}"
SUFFIXES="${PODCAST_GC_SUFFIXES:-.mp4}"
OUT_DIR="${PODCAST_OUTPUT_DIR:-output/podcast}"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --days) DAYS="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 相対パスは games/soviet_now 起点に解決する (launchd から cwd 不定で呼ばれる)
case "$OUT_DIR" in
  /*) ;;
  *) OUT_DIR="$PROJ/$OUT_DIR" ;;
esac

ts() { date "+%Y-%m-%d %H:%M:%S"; }
say() { echo "[$(ts)] [podcast_gc] $*"; }

# YYYY-MM-DD -> epoch (ローカル 0 時)。macOS(BSD) と Linux(GNU) の両方で動かす。
iso_epoch() {
  date -j -f "%Y-%m-%d %H:%M:%S" "$1 00:00:00" "+%s" 2>/dev/null \
    || date -d "$1 00:00:00" "+%s" 2>/dev/null
}

if [ ! -d "$OUT_DIR" ]; then
  say "出力ディレクトリが無い ($OUT_DIR)。何もしない。"
  exit 0
fi

TODAY_EPOCH="$(iso_epoch "$(date "+%Y-%m-%d")")"
if [ -z "$TODAY_EPOCH" ]; then
  say "日付の計算に失敗した。中止。"
  exit 1
fi

deleted=0
freed=0
kept_unpub=0

for suffix in ${=SUFFIXES}; do
  for f in "$OUT_DIR"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"$suffix"(N); do
    base="${f:t}"
    iso="${base%$suffix}"
    e="$(iso_epoch "$iso")"
    [ -n "$e" ] || continue
    age=$(( (TODAY_EPOCH - e) / 86400 ))

    # 作り直した回を日付だけで消さないよう、更新時刻も見る
    mtime="$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null)"
    [ -n "$mtime" ] || continue
    mtime_age=$(( ($(date "+%s") - mtime) / 86400 ))

    if [ -f "$OUT_DIR/$iso.publish.json" ]; then
      limit="$DAYS"; state="公開済み"
    else
      limit="$UNPUB_DAYS"; state="未公開"
    fi

    if [ "$age" -le "$limit" ] || [ "$mtime_age" -le "$limit" ]; then
      if [ "$state" = "未公開" ] && [ "$age" -gt "$DAYS" ]; then
        say "保持: $base ($state, ${age}日前 / 未公開は ${UNPUB_DAYS}日まで残す)"
        kept_unpub=$((kept_unpub + 1))
      fi
      continue
    fi

    size="$(stat -f %z "$f" 2>/dev/null || stat -c %s "$f" 2>/dev/null || echo 0)"
    if [ "$DRY_RUN" = "1" ]; then
      say "[dry-run] 削除対象: $base ($state, ${age}日前, $((size / 1024 / 1024))MB)"
    else
      if rm -f "$f"; then
        say "削除: $base ($state, ${age}日前, $((size / 1024 / 1024))MB)"
      else
        say "削除に失敗: $base"
        continue
      fi
    fi
    deleted=$((deleted + 1))
    freed=$((freed + size))
  done
done

if [ "$deleted" -eq 0 ]; then
  say "削除対象なし (保持 ${DAYS}日 / 未公開 ${UNPUB_DAYS}日, 対象 ${SUFFIXES}, $OUT_DIR)"
else
  verb="削除"; [ "$DRY_RUN" = "1" ] && verb="削除予定"
  say "$verb ${deleted}件 / $((freed / 1024 / 1024))MB (保持 ${DAYS}日, $OUT_DIR)"
fi
[ "$kept_unpub" -gt 0 ] && say "未公開のため保持: ${kept_unpub}件"
exit 0
