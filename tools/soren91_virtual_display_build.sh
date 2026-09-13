#!/usr/bin/env bash
# Builds tools/macos/soren91_virtual_display.swift into
# tools/macos/bin/soren91_virtual_display, only when missing or stale.
# Prints the resulting binary path on stdout.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/macos/soren91_virtual_display.swift"
out_dir="$here/macos/bin"
out="$out_dir/soren91_virtual_display"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "soren91_virtual_display_build.sh must run on macOS" >&2
  exit 1
fi

if [[ ! -x "$out" || "$src" -nt "$out" ]]; then
  mkdir -p "$out_dir"
  echo "building $out from $src" >&2
  swiftc -O -parse-as-library "$src" -o "$out"
fi

echo "$out"
