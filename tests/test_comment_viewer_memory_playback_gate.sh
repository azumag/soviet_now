#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"
export ELOOP_LIB_DIR="$ROOT"
export TMP_STATE_DIR="$TMP/state"
export COMMENT_VIEWER_MEMORY_ENABLED=1
export COMMENT_VIEWER_MEMORY_FILE="$TMP/state/viewer_memory.json"
export COMMENT_VIEWER_MEMORY_EXCLUDED_USERS="dociai dociaich"
export COMMENT_SPOKEN_HISTORY_DIR="$TMP/spoken"
export COMMENT_SPOKEN_HISTORY_MAX_FILES=16
source broadcast/comment.sh
log() { :; }

pending="$TMP/pending.log"
batch="$TMP/comments.txt"
reply="$TMP/reply.txt"
queue="$TMP/comment_gate.txt"

printf 'id=msg-gate\tuser-id=viewer-gate\tlogin=alice\tdisplay=Alice\tflags=\tAlice: 前回の続きを話します\n' >"$pending"
python3 lib/comment_viewer_memory.py emit-batch --pending "$pending" --out "$batch" --source twitch >/dev/null
printf '%s\n' '前回の続きとして返答しました。' >"$reply"
cp "$reply" "$queue"

_stage_comment_viewer_memory "$queue" "$batch" "$reply" twitch main gate-batch
test -f "$TMP/comment_gate.viewer_memory.json"
test ! -f "$COMMENT_VIEWER_MEMORY_FILE"

_clean_comment_talk() { cat; }
_sanitize_onair_text() { cat; }
_broadcast_read_expected_mode() { printf '%s' main; }
_remember_spoken_comment "$queue"

test -f "$COMMENT_VIEWER_MEMORY_FILE"
python3 - "$COMMENT_VIEWER_MEMORY_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    state = json.load(stream)
assert len(state["users"]) == 1
user = next(iter(state["users"].values()))
assert user["stable_id"] == "viewer-gate"
assert user["exchanges"][0]["comment"] == "前回の続きを話します"
assert user["exchanges"][0]["reply"] == "前回の続きとして返答しました。"
PY

printf 'comment_viewer_memory_playback_gate: commit only after spoken-success hook passed\n'
