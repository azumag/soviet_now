#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_SRC="$ROOT/broadcast/radio_state.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }
log() { :; }

sed -n '/^_radio_cleanup_orphan_render_artifacts()/,/^}/p' "$STATE_SRC" > "$TMP/fn_cleanup.sh"
if [ ! -s "$TMP/fn_cleanup.sh" ]; then
  not_ok "cleanup helper exists"
  exit "$FAIL"
fi
. "$TMP/fn_cleanup.sh"

if grep -A3 "^_play_deferred_radio_queue_once()" "$STATE_SRC" | grep -q "_radio_cleanup_orphan_render_artifacts"; then
  ok "deferred playback invokes orphan cleanup"
else
  not_ok "deferred playback invokes orphan cleanup"
fi

QUEUE="$TMP/queue"
mkdir -p "$QUEUE"
export RADIO_DEFERRED_QUEUE_DIR="$QUEUE"
export RADIO_ORPHAN_RENDER_TTL_SEC=3600

old_epoch=$(( $(date +%s) - 7200 ))
fresh_epoch=$(( $(date +%s) - 60 ))
set_mtime() {
  local epoch="$1" path="$2"
  python3 - "$epoch" "$path" <<'PY'
import os, sys
stamp = int(sys.argv[1])
os.utime(sys.argv[2], (stamp, stamp))
PY
}

# old orphan: no .txt or .playing => rendered audio cache is disposable
base="$QUEUE/radio_1000_1_news_1"
printf 'RIFF' > "$base.ready.wav"
printf 'hash 1\n' > "$base.render_meta"
mkdir -p "$base.ready.wav.bundle"
printf 'x' > "$base.ready.wav.bundle/playlist.txt"
set_mtime "$old_epoch" "$base.ready.wav"
set_mtime "$old_epoch" "$base.render_meta"

# active queued item: must be retained even when old
queued="$QUEUE/radio_1001_1_news_2"
echo body > "$queued.txt"
printf 'RIFF' > "$queued.ready.wav"
set_mtime "$old_epoch" "$queued.ready.wav"

# active playback: must also be retained
playing="$QUEUE/radio_1002_1_news_3"
echo body > "$playing.playing"
printf 'RIFF' > "$playing.ready.wav"
set_mtime "$old_epoch" "$playing.ready.wav"

# fresh orphan: grace period protects just-finished render/rename races
fresh="$QUEUE/radio_1003_1_news_4"
printf 'RIFF' > "$fresh.ready.wav"
set_mtime "$fresh_epoch" "$fresh.ready.wav"

_radio_cleanup_orphan_render_artifacts

if [ ! -e "$base.ready.wav" ] && [ ! -e "$base.render_meta" ] && [ ! -e "$base.ready.wav.bundle" ]; then
  ok "old orphan render artifacts removed"
else
  not_ok "old orphan render artifacts removed"
fi
if [ -e "$queued.ready.wav" ]; then
  ok "queued render preserved"
else
  not_ok "queued render preserved"
fi
if [ -e "$playing.ready.wav" ]; then
  ok "playing render preserved"
else
  not_ok "playing render preserved"
fi
if [ -e "$fresh.ready.wav" ]; then
  ok "fresh orphan preserved during grace period"
else
  not_ok "fresh orphan preserved during grace period"
fi

exit "$FAIL"
