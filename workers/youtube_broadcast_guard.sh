#!/bin/bash
# Supervised wrapper for the fail-closed YouTube broadcast guard.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
exec python3 lib/youtube_broadcast_guard.py run
