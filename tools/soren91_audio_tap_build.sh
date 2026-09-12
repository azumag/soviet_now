#!/usr/bin/env bash
# Builds tools/macos/soren91_audio_tap.swift into
# tools/macos/bin/soren91_audio_tap, only when missing or stale.
# Prints the resulting binary path on stdout.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/macos/soren91_audio_tap.swift"
out_dir="$here/macos/bin"
out="$out_dir/soren91_audio_tap"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "soren91_audio_tap_build.sh must run on macOS" >&2
  exit 1
fi

if [[ ! -x "$out" || "$src" -nt "$out" ]]; then
  mkdir -p "$out_dir"
  echo "building $out from $src" >&2
  # Core Audio process taps are macOS 14.2+. Pin the deployment target so
  # swiftc does not type-check these APIs against the older default target
  # used by GitHub's macOS runner/toolchain. The runtime host is required to
  # be at least 14.2 for this helper by design.
  swiftc -O -parse-as-library -target "$(uname -m)-apple-macosx14.2" "$src" -o "$out"
fi

echo "$out"
