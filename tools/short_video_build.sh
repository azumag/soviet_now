#!/bin/zsh
# tools/short_video_build.sh - Short動画生成のラッパー (Mac用 launchd から呼ぶ)
# doci の cron_generate.sh と同様に PATH/HOME を整え、VOICEVOX を起動してから
# tools/short_video_build.py を呼ぶ。引数はそのまま透過する。
# バッティング回避: 既存の com.azumag.doci.generate (3時間毎, --all-channels) とは別に
# com.azumag.soren-news.generate (06:00 JST 日次) として登録する。

export HOME="/Users/azumag"
PROJ="${0:A:h:h}"
# doci と同様に内蔵バイナリを優先 (外付けボリュームの dyld 固まり回避)
nvm_node_bins=(/Users/azumag/.nvm/versions/node/*/bin(N/n[-1]))
NVM_NODE_BIN="${nvm_node_bins[1]}"
export PATH="$PROJ/tools/ffbin:${NVM_NODE_BIN:-/Users/azumag/.nvm/versions/node/v24.18.0/bin}:/Users/azumag/.local/bin:/Users/azumag/.opencode/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"

# ログ
LOG="$PROJ/output/soren_news_cron.log"
mkdir -p "$PROJ/output"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
echo "[$(ts)] ===== soren-news short video cron start $* =====" >> "$LOG"

# 多重起動防止
LOCK="$PROJ/output/.cron_soren_news.lock"
if [ -e "$LOCK" ]; then
  pid=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "[$(ts)] 前回の実行(pid=$pid)が継続中。スキップ。" >> "$LOG"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# VOICEVOX 起動待ち
/usr/local/bin/orb start >> "$LOG" 2>&1
ok=0
for i in $(seq 1 30); do
  if curl -s --max-time 3 http://127.0.0.1:50021/version >/dev/null 2>&1; then ok=1; break; fi
  sleep 5
done
if [ "$ok" != "1" ]; then
  echo "[$(ts)] VOICEVOX 未到達。中止。" >> "$LOG"
  exit 1
fi

# soren-radio-archive を最新化 (Macのクローン)
if [ -d "$HOME/soren-radio-archive" ]; then
  echo "[$(ts)] git pull soren-radio-archive" >> "$LOG"
  git -C "$HOME/soren-radio-archive" pull --ff-only >> "$LOG" 2>&1 || echo "[$(ts)] pull failed, continuing" >> "$LOG"
fi

# doci の soren_news チャンネルが最新か確認 (soren-news-channel branch)
DOCI_DIR="/Users/azumag/azumag/work/doci/repo"
if [ -d "$DOCI_DIR" ]; then
  echo "[$(ts)] git pull doci (soren-news-channel)" >> "$LOG"
  git -C "$DOCI_DIR" fetch origin soren-news-channel >> "$LOG" 2>&1 || true
  # ローカルが soren-news-channel なら pull (upstream が無くても明示的に origin/branch を指定)
  if git -C "$DOCI_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null | grep -q "soren-news-channel"; then
    git -C "$DOCI_DIR" pull --ff-only origin soren-news-channel >> "$LOG" 2>&1 || echo "[$(ts)] doci pull failed, continuing" >> "$LOG"
  fi
fi

PY="$PROJ/../doci/repo/.venv-cron/bin/python"
if [ ! -x "$PY" ]; then
  # doci の venv が無い場合はシステム python でフォールバック (soviet_now の .venv)
  PY="$PROJ/.venv/bin/python"
  [ -x "$PY" ] || PY="$(command -v python3)"
fi

cd "$PROJ" || exit 1
# 既定は --pick-one (当日1本)
"$PY" ./tools/short_video_build.py "$@" >> "$LOG" 2>&1
rc=$?
echo "[$(ts)] ===== soren-news cron end rc=$rc =====" >> "$LOG"
exit $rc
