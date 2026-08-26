#!/bin/zsh
# tools/podcast_daily.sh - ポッドキャストの日次パイプライン (Mac用 launchd から呼ぶ / docich#10)
#
#   前日分の原稿 (VM が 04:00 に push したミラー)
#     -> 0. 掃除      podcast_gc.sh           3日を過ぎた mp4 を削除 (1本 約300MB)
#     -> 1. 音声      podcast_build.sh        編成 + 合成 + BGM  (約13分)
#     -> 2. 動画      podcast_video_build.py  素材 + 字幕 + 波形  (約6分)
#     -> 3. 公開      podcast_publish.py      YouTube + 再生リスト (約1分)
#     -> 4. 告知      bluesky_post.py         Bluesky へ投稿 (数秒)
#
# 各段は独立して実行できる。前段が失敗したらそこで止める (壊れた音声で動画を作らない)。
# 段ごとに PODCAST_SKIP_* / PODCAST_AUTO_PUBLISH で止められる。
# 告知は「公開が済んで publish.json がある」ことが条件 (未公開の URL は流さない)。
#
# 使い方:
#   ./tools/podcast_daily.sh                 # 前日分を通しで
#   ./tools/podcast_daily.sh --date 20260825 # 日付を指定
#   PODCAST_AUTO_PUBLISH=0 ./tools/podcast_daily.sh   # 公開せず動画まで
#   PODCAST_SKIP_GC=1 ./tools/podcast_daily.sh        # 古い動画を消さない
#   PODCAST_BLUESKY_ENABLED=0 ./tools/podcast_daily.sh # Bluesky へ投稿しない

export HOME="/Users/azumag"
PROJ="${0:A:h:h}"
DOCICH_ROOT="${PROJ:h:h}"

nvm_node_bins=(/Users/azumag/.nvm/versions/node/*/bin(N/n[-1]))
NVM_NODE_BIN="${nvm_node_bins[1]}"
export PATH="$PROJ/tools/ffbin:${NVM_NODE_BIN:-/Users/azumag/.nvm/versions/node/v24.18.0/bin}:/Users/azumag/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"

export DOCICH_BIN="${DOCICH_BIN:-$DOCICH_ROOT/bin/docich}"
export SOREN_RADIO_ARCHIVE="${SOREN_RADIO_ARCHIVE:-$HOME/soren-radio-archive}"
# Google の増分認可で要求より多いスコープが返り oauthlib が例外にするのを防ぐ
export OAUTHLIB_RELAX_TOKEN_SCOPE=1

DOCI_DIR="${DOCI_DIR:-/Users/azumag/azumag/work/doci/repo}"
# 動画・公開は doci の実装 (Pillow / google-api-client) を使うのでその venv で動かす
PY="$DOCI_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

LOG="$PROJ/output/podcast_daily.log"
mkdir -p "$PROJ/output"
ts() { date "+%Y-%m-%d %H:%M:%S"; }
say() { echo "[$(ts)] $*" | tee -a "$LOG"; }

# 多重起動防止 (音声だけで13分かかるので重なると VOICEVOX を奪い合う)
LOCK="$PROJ/output/.cron_podcast_daily.lock"
if [ -e "$LOCK" ]; then
  pid=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    say "前回の実行(pid=$pid)が継続中。スキップ。"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

DATE_ARG=()
if [ "${1:-}" = "--date" ] && [ -n "${2:-}" ]; then
  DATE_ARG=(--date "$2")
  TARGET="$2"
else
  TARGET="$(date -v-1d "+%Y%m%d")"
  DATE_ARG=(--date "$TARGET")
fi
ISO="$(date -j -f "%Y%m%d" "$TARGET" "+%Y-%m-%d" 2>/dev/null || echo "$TARGET")"

say "===== podcast daily start ($TARGET) ====="
START_ALL=$(date +%s)
cd "$PROJ" || exit 1

# --- 0. 古い生成物の掃除 (動画 1 本が約 300MB) ---
if [ "${PODCAST_SKIP_GC:-0}" = "1" ]; then
  say "[0/4] 掃除: スキップ (PODCAST_SKIP_GC=1)"
else
  ./tools/podcast_gc.sh 2>&1 | tee -a "$LOG"
fi

# --- 1. 音声 ---
if [ "${PODCAST_SKIP_AUDIO:-0}" = "1" ]; then
  say "[1/4] 音声: スキップ (PODCAST_SKIP_AUDIO=1)"
else
  say "[1/4] 音声を生成"
  t0=$(date +%s)
  if ! ./tools/podcast_build.sh "${DATE_ARG[@]}"; then
    say "[1/4] 音声の生成に失敗。ここで中止する。"
    exit 2
  fi
  say "[1/4] 音声 done ($(( $(date +%s) - t0 ))s)"
fi
if [ ! -f "output/podcast/$ISO.mp3" ]; then
  say "音声が無い (output/podcast/$ISO.mp3)。中止。"
  exit 2
fi

# --- 2. 動画 ---
if [ "${PODCAST_SKIP_VIDEO:-0}" = "1" ]; then
  say "[2/4] 動画: スキップ (PODCAST_SKIP_VIDEO=1)"
else
  say "[2/4] 動画を生成"
  t0=$(date +%s)
  if ! "$PY" ./tools/podcast_video_build.py "${DATE_ARG[@]}" >>"$LOG" 2>&1; then
    say "[2/4] 動画の生成に失敗。公開はしない。"
    exit 2
  fi
  say "[2/4] 動画 done ($(( $(date +%s) - t0 ))s)"
fi

# --- 3. 公開 ---
if [ "${PODCAST_AUTO_PUBLISH:-1}" != "1" ]; then
  say "[3/4] 公開: スキップ (PODCAST_AUTO_PUBLISH!=1)"
  say "  手動で出す場合: $PY ./tools/podcast_publish.py --date $TARGET"
elif [ ! -f "output/podcast/$ISO.mp4" ]; then
  say "[3/4] 動画が無いので公開しない"
else
  say "[3/4] YouTube へ公開"
  t0=$(date +%s)
  if ! "$PY" ./tools/podcast_publish.py "${DATE_ARG[@]}" >>"$LOG" 2>&1; then
    say "[3/4] 公開に失敗 (動画は output/podcast/$ISO.mp4 に残っている)"
    exit 2
  fi
  say "[3/4] 公開 done ($(( $(date +%s) - t0 ))s)"
  [ -f "output/podcast/$ISO.publish.json" ] && say "  $(grep -o 'https://[^"]*' "output/podcast/$ISO.publish.json" | head -1)"
fi

# --- 4. 告知 (Bluesky) ---
# 公開できた回だけ流す。認証情報が無ければ黙って飛ばす (rc=4)。
if [ "${PODCAST_BLUESKY_ENABLED:-1}" != "1" ]; then
  say "[4/4] Bluesky: スキップ (PODCAST_BLUESKY_ENABLED!=1)"
elif [ ! -f "output/podcast/$ISO.publish.json" ]; then
  say "[4/4] Bluesky: 公開情報 (output/podcast/$ISO.publish.json) が無いのでスキップ"
else
  "$PY" ./tools/bluesky_post.py --podcast "${DATE_ARG[@]}" >>"$LOG" 2>&1
  bs_rc=$?
  case "$bs_rc" in
    0) say "[4/4] Bluesky done $( [ -f "output/podcast/$ISO.bluesky.json" ] && grep -o 'https://bsky.app/[^"]*' "output/podcast/$ISO.bluesky.json" | head -1 )" ;;
    4) say "[4/4] Bluesky: 認証情報が無いのでスキップ (~/.config/soren/bluesky.json か BLUESKY_HANDLE/BLUESKY_APP_PASSWORD)" ;;
    *) say "[4/4] Bluesky 投稿に失敗 (rc=$bs_rc)。動画は公開済み。詳細は $LOG" ;;
  esac
fi

say "===== podcast daily end ($(( $(date +%s) - START_ALL ))s) ====="
