#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

sed -n '/^_speaking_leave()/,/^}/p' "$ROOT/say_enqueue.sh" > "$TMP/speaking_leave.sh"
source "$TMP/speaking_leave.sh"

cd "$TMP"
mkdir -p tmp/state tmp/.say_queue tmp/.comment_queue
SPEAKING_STATE_FILE=tmp/state/speaking.json
export SPEAKING_GRACE_SEC=0
export SPEAKING_POLL_PID=
export COMMENT_QUEUE_DIR=tmp/.comment_queue
MY_CONTENT=tmp/.say_queue/content_self.txt
printf 'already played\n' > "$MY_CONTENT"

printf '{"speaking":true}\n' > "$SPEAKING_STATE_FILE"
_speaking_leave
test ! -e "$SPEAKING_STATE_FILE"
test -e "$MY_CONTENT"

printf '{"speaking":true}\n' > "$SPEAKING_STATE_FILE"
printf 'next item\n' > tmp/.say_queue/content_next.txt
_speaking_leave
test -e "$SPEAKING_STATE_FILE"
rm tmp/.say_queue/content_next.txt

printf '{"speaking":true}\n' > "$SPEAKING_STATE_FILE"
printf 'queued comment\n' > tmp/.comment_queue/comment_next.txt
_speaking_leave
test -e "$SPEAKING_STATE_FILE"
rm tmp/.comment_queue/comment_next.txt

printf '{"speaking":true}\n' > "$SPEAKING_STATE_FILE"
rm "$MY_CONTENT"
_speaking_leave
test ! -e "$SPEAKING_STATE_FILE"

echo 'ok - speaking marker follows other pending audio, not own played text'
