#!/usr/bin/env bash
# Legacy enabled flags cannot revive an agent; fixtures never load host .env.
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp "$ROOT/codex_bug_dispatcher.sh" "$TMP/dispatcher.sh"
printf 'ELOOP_LIB_DIR="%s"\n' "$TMP" >"$TMP/eloop_lib.sh"
mkdir -p "$TMP/bin"
cat >"$TMP/bin/codex" <<'SPY'
#!/bin/sh
touch "$SPY_MARKER"
exit 99
SPY
chmod +x "$TMP/bin/codex"
SENTINEL=SYNTHETIC_SECRET_NEVER_IN_ARTIFACT
for mode in run kick; do
  for enabled in 0 1; do
    fixture="$TMP/$mode-$enabled"
    mkdir -p "$fixture/queue"
    python3 - "$fixture/queue/report.json" "$SENTINEL" <<'PAYLOAD'
import json,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"category":"stream_bug_report","comment":sys.argv[2],"user":"PRIVATE_VIEWER"}))
PAYLOAD
    before=$(shasum -a 256 "$fixture/queue/report.json" | cut -d' ' -f1)
    env -i PATH="$TMP/bin:$PATH" HOME="$TMP" SPY_MARKER="$TMP/executed" \
      SOREN_TEST_TOKEN="$SENTINEL" CODEX_BUG_DISPATCH_ENABLED="$enabled" \
      CODEX_BUG_DISPATCH_CODEX_CMD="$TMP/bin/codex" \
      CODEX_BUG_QUEUE_DIR="$fixture/queue" CODEX_BUG_DISPATCH_LOG_DIR="$fixture/log" \
      CODEX_BUG_QUARANTINE_NOTICE_FILE="$fixture/notice" \
      bash "$TMP/dispatcher.sh" "$mode" >"$fixture/stdout" 2>"$fixture/stderr"
    test ! -e "$TMP/executed"
    test -f "$fixture/queue/quarantined/report.json"
    after=$(shasum -a 256 "$fixture/queue/quarantined/report.json" | cut -d' ' -f1)
    test "$before" = "$after"
    ! grep -q "$SENTINEL\|PRIVATE_VIEWER" "$fixture/notice" "$fixture/stdout" "$fixture/stderr"
    test -z "$(find "$fixture/log" -name 'prompt_*' -o -name 'last_*' -o -name 'run_*' 2>/dev/null)"
    printf 'ok - %s enabled=%s never spawns; original report preserved; no private artifacts\n' "$mode" "$enabled"
  done
done
