#!/usr/bin/env bash
# Builds the ScreenCaptureKit helper and its shared frame-normalization policy.
# Prints the resulting binary path on stdout; no screen capture is started.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/macos/soren91_window_capture.swift"
frames="$here/macos/soren91_capture_frames.swift"
out_dir="$here/macos/bin"
out="$out_dir/soren91_window_capture"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "soren91_window_capture_build.sh must run on macOS" >&2
  exit 1
fi
if [[ ! -x "$out" || "$src" -nt "$out" || "$frames" -nt "$out" || "${BASH_SOURCE[0]}" -nt "$out" ]]; then
  mkdir -p "$out_dir"
  echo "building $out from $src and $frames" >&2
  swiftc -O -parse-as-library "$frames" "$src" -o "$out"
fi
echo "$out"
