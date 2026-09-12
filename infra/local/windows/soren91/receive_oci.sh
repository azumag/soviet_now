#!/usr/bin/env bash
set -euo pipefail

: "${SOREN91_OCI_TAILSCALE_IP:?set SOREN91_OCI_TAILSCALE_IP to the OCI Tailscale IPv4 address}"
PORT="${SOREN91_OCI_SRT_PORT:-19192}"
OUT="${SOREN91_OCI_POC_OUT:-/tmp/soren91-local-poc.ts}"
DURATION="${SOREN91_OCI_POC_SEC:-90}"

url="srt://${SOREN91_OCI_TAILSCALE_IP}:${PORT}?mode=listener&transtype=live&latency=200000"

echo "Waiting for Soren91 local renderer on ${SOREN91_OCI_TAILSCALE_IP}:${PORT} (Tailscale only)" >&2
ffmpeg -hide_banner -loglevel warning -y \
  -i "$url" \
  -t "$DURATION" \
  -c copy "$OUT"

ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,avg_frame_rate \
  -of json "$OUT"
