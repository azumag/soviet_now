#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Extract only the source-change gate so the test never starts the real watcher.
sed -n '/^_refresh_viewer_chat_monitor_if_changed() {/,/^}/p'   "$REPO_ROOT/generate_soren_overlay.sh" >"$TMP/refresh.sh"
# shellcheck disable=SC1090
source "$TMP/refresh.sh"

mkdir -p "$TMP/tmp/.viewer_chat" "$TMP/tmp/state"
cat >"$TMP/viewer_chat_monitor.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'call\n' >> calls
mkdir -p "$(dirname "$VIEWER_CHAT_MONITOR_FILE")"
printf '{"epoch":1,"latest":"x","recent":["x"],"count":1}\n' >"$VIEWER_CHAT_MONITOR_FILE"
EOF
chmod +x "$TMP/viewer_chat_monitor.sh"

cd "$TMP"
export VIEWER_CHAT_MONITOR_SOURCE="$TMP/tmp/.viewer_chat/comment_context_history.log"
export VIEWER_CHAT_MONITOR_FILE="$TMP/tmp/state/viewer_chat_monitor.json"

# No source: no work.
_refresh_viewer_chat_monitor_if_changed
[ ! -e calls ]

# First source creates the monitor exactly once.
printf 'alice: hello\n' >"$VIEWER_CHAT_MONITOR_SOURCE"
_refresh_viewer_chat_monitor_if_changed
[ "$(wc -l <calls | tr -d ' ')" = 1 ]
[ -f "$VIEWER_CHAT_MONITOR_FILE" ]

# Unchanged/older source: no repeat scan.
_refresh_viewer_chat_monitor_if_changed
[ "$(wc -l <calls | tr -d ' ')" = 1 ]

# A newer chat history triggers one more refresh.
sleep 1
printf 'bob: hi\n' >>"$VIEWER_CHAT_MONITOR_SOURCE"
_refresh_viewer_chat_monitor_if_changed
[ "$(wc -l <calls | tr -d ' ')" = 2 ]

echo "soren overlay viewer chat refresh test: PASS"
