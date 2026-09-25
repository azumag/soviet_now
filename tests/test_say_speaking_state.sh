#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

sed -n '/^_speaking_enter()/,/^}/p' "$ROOT/say_enqueue.sh" > "$TMP/speaking_state.sh"
sed -n '/^_speaking_leave()/,/^}/p' "$ROOT/say_enqueue.sh" >> "$TMP/speaking_state.sh"
source "$TMP/speaking_state.sh"

cd "$TMP"
mkdir -p tmp/state tmp/.say_queue tmp/.comment_queue
SPEAKING_STATE_FILE=tmp/state/speaking.json
SPEAKING_GRACE_SEC=0
TWITCH_SNOOZE_POLL_SEC=0
SPEAKING_POLL_PID=
SPEAKING_ENTERED=0
MY_TOKEN=first
MY_CONTENT=tmp/.say_queue/content_first.txt
printf 'already played\n' > "$MY_CONTENT"
printf 'waiting next item\n' > tmp/.say_queue/content_next.txt
printf 'waiting comment\n' > tmp/.comment_queue/comment_next.txt

_speaking_enter test
test -e "$SPEAKING_STATE_FILE"
python3 - "$SPEAKING_STATE_FILE" <<'PY'
import json, sys
assert json.load(open(sys.argv[1], encoding="utf-8"))["token"] == "first"
PY
_speaking_leave
test ! -e "$SPEAKING_STATE_FILE"
test -e "$MY_CONTENT"
test -e tmp/.say_queue/content_next.txt
test -e tmp/.comment_queue/comment_next.txt

# An older cleanup cannot erase a newer playback's marker.
MY_TOKEN=older
_speaking_enter test
older_poll=$SPEAKING_POLL_PID
MY_TOKEN=newer
_speaking_enter test
newer_poll=$SPEAKING_POLL_PID
MY_TOKEN=older
SPEAKING_POLL_PID=$older_poll
_speaking_leave
python3 - "$SPEAKING_STATE_FILE" <<'PY'
import json, sys
assert json.load(open(sys.argv[1], encoding="utf-8"))["token"] == "newer"
PY
MY_TOKEN=newer
SPEAKING_POLL_PID=$newer_poll
_speaking_leave
test ! -e "$SPEAKING_STATE_FILE"

# Render-only and pre-playback exits do not own another playback's marker.
printf '{"token":"other"}\n' > "$SPEAKING_STATE_FILE"
MY_TOKEN=render_only
SPEAKING_ENTERED=0
_speaking_leave
test -e "$SPEAKING_STATE_FILE"

echo 'ok - speaking marker follows the active owner, not queued files'
