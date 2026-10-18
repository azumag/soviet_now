#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Exercise the real producer and the in-process gate, without a watcher or VM.
mkdir -p "$TMP/lib"
cp "$REPO_ROOT/lib/viewer_chat_cache.py" "$TMP/lib/"
cp "$REPO_ROOT/viewer_chat_monitor.sh" "$TMP/"
printf 'ELOOP_LIB_DIR="$PWD"\n' >"$TMP/eloop_lib.sh"
chmod +x "$TMP/viewer_chat_monitor.sh"
cd "$TMP"
python3 - <<'PY'
import json
from pathlib import Path

from lib.viewer_chat_cache import refresh_viewer_chat_monitor_if_changed as refresh

source = Path("history.log")
monitor = Path("monitor.json")
assert not refresh(source, monitor)
assert not monitor.exists()
source.write_text("alice: hello\n")
assert refresh(source, monitor)
assert json.loads(monitor.read_text())["latest"] == "alice: hello"
before = monitor.stat().st_mtime_ns
assert not refresh(source, monitor)
assert monitor.stat().st_mtime_ns == before
with source.open("a") as stream:
    stream.write("bob: hi\n")
assert refresh(source, monitor)
assert json.loads(monitor.read_text())["latest"] == "bob: hi"
print("soren overlay viewer chat refresh test: PASS")
PY
