#!/bin/zsh
# tools/podcast_build.sh - 日次ポッドキャスト生成のラッパー (Mac用 launchd から呼ぶ / docich#10)
#
# 経緯: VM 上で 05:30 に走らせていた podcast.timer は、配信本体と VOICEVOX を奪い合って
# 39本中37本が voicevox timeout で失敗し、1回の実行に1時間47分かかっていた (2026-08-25 実測)。
# 収益化前に VM をもう1台建てるのは割に合わないため、Short 動画 (short_video_build.sh) と
# 同様に生成を Mac へ寄せる。VM 側の podcast.timer は disable 済み。
#
# 前提: OrbStack 上の VOICEVOX、~/soren-radio-archive (原稿の VM 外ミラー)、ffmpeg/ffprobe。
# 引数はそのまま podcast_build.py へ透過する (例: --date 20260825 / --dry-run)。

export HOME="/Users/azumag"
PROJ="${0:A:h:h}"            # games/soviet_now
DOCICH_ROOT="${PROJ:h:h}"    # docich リポジトリルート

# doci と同様に内蔵バイナリを優先 (外付けボリュームの dyld 固まり回避)
nvm_node_bins=(/Users/azumag/.nvm/versions/node/*/bin(N/n[-1]))
NVM_NODE_BIN="${nvm_node_bins[1]}"
export PATH="$PROJ/tools/ffbin:${NVM_NODE_BIN:-/Users/azumag/.nvm/versions/node/v24.18.0/bin}:/Users/azumag/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"

# voicevox_tts.sh は docich CLI へ委譲する薄いブリッジなので、その実体を明示する
export DOCICH_BIN="${DOCICH_BIN:-$DOCICH_ROOT/bin/docich}"
# 原稿は VM ではなく Mac のミラーから読む
export SOREN_RADIO_ARCHIVE="${SOREN_RADIO_ARCHIVE:-$HOME/soren-radio-archive}"

LOG="$PROJ/output/podcast_cron.log"
mkdir -p "$PROJ/output"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
echo "[$(ts)] ===== podcast build start $* =====" >> "$LOG"

# 多重起動防止
LOCK="$PROJ/output/.cron_podcast.lock"
if [ -e "$LOCK" ]; then
  pid=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "[$(ts)] 前回の実行(pid=$pid)が継続中。スキップ。" >> "$LOG"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# docich (config.py) は tomllib を使うので Python 3.11+ が必須。
# bin/docich は PATH の python3 を exec するため、tomllib を持つ python を PATH 先頭に置く。
# ここを誤ると全 synth が ModuleNotFoundError で落ちる (2026-08-26 に /usr/bin/python3=3.9 で発生)。
#
# /opt/homebrew は外付けボリュームを指すことがあり、ボリューム不調時は Python の起動自体が
# 固まる。候補ごとに期限を設け、内蔵ディスク上の doci/uv Python を先に試す。
PY311=""
PYTHON_PROBE_TIMEOUT_SEC="${PODCAST_PYTHON_PROBE_TIMEOUT_SEC:-5}"
if [[ "$PYTHON_PROBE_TIMEOUT_SEC" != <-> ]] || [ "$PYTHON_PROBE_TIMEOUT_SEC" -le 0 ]; then
  PYTHON_PROBE_TIMEOUT_SEC=5
fi
python_candidates=(
  "${PODCAST_PYTHON:-}"
  "/Users/azumag/azumag/work/doci/repo/.venv/bin/python"
  "$HOME/.local/bin/python3.11"
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "$(command -v python3)"
)
for cand in "${python_candidates[@]}"; do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  if /usr/bin/perl -e 'alarm shift @ARGV; exec @ARGV' \
      "$PYTHON_PROBE_TIMEOUT_SEC" "$cand" -c "import tomllib" >/dev/null 2>&1; then
    PY311="$cand"
    break
  else
    rc=$?
  fi
  if [ "$rc" -eq 142 ]; then
    echo "[$(ts)] Python候補の確認が ${PYTHON_PROBE_TIMEOUT_SEC}s でタイムアウト: $cand" >> "$LOG"
  else
    echo "[$(ts)] tomllib を使えない Python候補をスキップ: $cand (rc=$rc)" >> "$LOG"
  fi
done

if [ -z "$PY311" ]; then
  echo "[$(ts)] tomllib を持つ python3 (3.11+) が見つからない。docich が起動できないため中止。" >> "$LOG"
  exit 1
fi
export PATH="${PY311:h}:$PATH"
PY="$PY311"
echo "[$(ts)] PY=$PY ($($PY --version 2>&1)) DOCICH_BIN=$DOCICH_BIN" >> "$LOG"

# VOICEVOX 起動待ち
if [ -x /usr/local/bin/orb ]; then
  /usr/local/bin/orb start >> "$LOG" 2>&1
fi
ok=0
for i in $(seq 1 30); do
  if curl -s --max-time 3 "${VOICEVOX_URL:-http://127.0.0.1:50021}/version" >/dev/null 2>&1; then ok=1; break; fi
  sleep 5
done
if [ "$ok" != "1" ]; then
  echo "[$(ts)] VOICEVOX 未到達。中止。" >> "$LOG"
  exit 1
fi

# 原稿ミラーを最新化 (VM が 04:00 に push したものを取り込む)
if [ -d "$SOREN_RADIO_ARCHIVE/.git" ]; then
  echo "[$(ts)] git pull soren-radio-archive" >> "$LOG"
  git -C "$SOREN_RADIO_ARCHIVE" pull --ff-only >> "$LOG" 2>&1 || echo "[$(ts)] pull failed, continuing" >> "$LOG"
else
  echo "[$(ts)] warning: $SOREN_RADIO_ARCHIVE が無い。VM ローカルの backups へフォールバックする" >> "$LOG"
fi

cd "$PROJ" || exit 1
"$PY" ./tools/podcast_build.py "$@" >> "$LOG" 2>&1
rc=$?
echo "[$(ts)] ===== podcast build end rc=$rc =====" >> "$LOG"
exit $rc
