#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

# eloop.sh contains function definitions only; source it so this exercises the
# same follower helper used by play_one_game without starting a real game.
source ./eloop.sh

tmpfile=$(mktemp /tmp/eloop_tail_test.XXXXXX)
cleanup() {
  rm -f "$tmpfile"
  [ -n "${runner_pid:-}" ] && kill "$runner_pid" 2>/dev/null || true
  [ -n "${tail_pid:-}" ] && kill "$tail_pid" 2>/dev/null || true
}
trap cleanup EXIT

# On GNU/Linux, the production path must terminate itself after its bound
# runner exits, even when nobody reaches the normal explicit kill/wait block.
if tail --help 2>&1 | grep -q -- '--pid'; then
  sleep 0.2 &
  runner_pid=$!
  _follow_runner_output "$runner_pid" "$tmpfile" >/dev/null 2>&1 &
  tail_pid=$!
  wait "$runner_pid"
  runner_pid=''
  for _ in $(seq 1 30); do
    if ! kill -0 "$tail_pid" 2>/dev/null; then
      wait "$tail_pid" 2>/dev/null || true
      tail_pid=''
      echo "ok: runner follower exited with runner"
      exit 0
    fi
    sleep 0.1
  done
  echo "runner follower remained alive after runner exit" >&2
  exit 1
fi

# BSD tail lacks --pid; retain the existing explicit-cleanup fallback there.
type _follow_runner_output >/dev/null 2>&1
echo "ok: fallback helper available"
