#!/bin/bash
# soviet_now#869: 失敗した改善ループの一時ファイル回収（古いものだけ・無関係は残す）。
set -euo pipefail
cd "$(dirname "$0")/.."
source ./lib/reclaim_improve_temps.sh

tmp=$(mktemp -d "${TMPDIR:-/tmp}/reclaim_improve_temps.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

touch "$tmp/eloop_improve_runtime.abc.sh" "$tmp/host_integrity_before.xyz" "$tmp/keep.txt"
touch -t 202001010000 "$tmp/eloop_improve_runtime.abc.sh" "$tmp/host_integrity_before.xyz" "$tmp/keep.txt"
removed=$(reclaim_improve_temps "$tmp")
[ "$removed" = "2" ] || { echo "expected 2 removed, got $removed"; exit 1; }
[ ! -e "$tmp/eloop_improve_runtime.abc.sh" ] || { echo "runtime snapshot left"; exit 1; }
[ ! -e "$tmp/host_integrity_before.xyz" ] || { echo "integrity snapshot left"; exit 1; }
[ -e "$tmp/keep.txt" ] || { echo "unrelated file removed"; exit 1; }

touch "$tmp/eloop_improve_runtime.new.sh"
removed=$(reclaim_improve_temps "$tmp")
[ "$removed" = "0" ] || { echo "fresh file removed: $removed"; exit 1; }
[ -e "$tmp/eloop_improve_runtime.new.sh" ] || { echo "fresh file missing"; exit 1; }
echo "ok"
