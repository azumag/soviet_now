#!/usr/bin/env bash
set -euo pipefail

DISPLAY="${DISPLAY:-:0}"
SESSION_SEC="${SOREN91_POC_SESSION_SEC:-300}"
XORG_LOG="${SOREN91_POC_XORG_LOG:-/tmp/soren91-xorg.log}"
XORG_PID=""
WM_PID=""

# shellcheck disable=SC2317  # EXIT/INT/TERM trap callback
cleanup() {
	local rc=$?
	[ -z "$WM_PID" ] || kill -TERM "$WM_PID" 2>/dev/null || true
	[ -z "$XORG_PID" ] || kill -TERM "$XORG_PID" 2>/dev/null || true
	exit "$rc"
}
trap cleanup EXIT INT TERM

case "$SESSION_SEC" in
''|*[!0-9]*) echo "SOREN91_POC_SESSION_SEC must be an integer" >&2; exit 2 ;;
esac
if [ "$SESSION_SEC" -lt 60 ] || [ "$SESSION_SEC" -gt 300 ]; then
	echo "SOREN91_POC_SESSION_SEC must be between 60 and 300" >&2
	exit 2
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
	echo 'SOREN91_POC_RESULT={"pass":false,"reason":"nvidia-smi unavailable"}'
	exit 1
fi
nvidia-smi -L

if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q 'h264_nvenc'; then
	echo 'SOREN91_POC_RESULT={"pass":false,"reason":"h264_nvenc unavailable"}'
	exit 1
fi

mkdir -p /tmp/.pulse /tmp/soren91-profile
pulseaudio --start --exit-idle-time=-1 --log-target=stderr || true
pactl load-module module-null-sink sink_name=soren91_poc_sink \
	sink_properties=device.description=soren91_poc >/dev/null 2>&1 || true
pactl set-default-sink soren91_poc_sink >/dev/null 2>&1 || true

cat >/tmp/soren91-xorg.conf <<'EOF'
Section "ServerLayout"
  Identifier "Layout0"
  Screen 0 "Screen0"
EndSection
Section "Device"
  Identifier "Device0"
  Driver "nvidia"
  VendorName "NVIDIA Corporation"
  Option "AllowEmptyInitialConfiguration" "True"
EndSection
Section "Screen"
  Identifier "Screen0"
  Device "Device0"
  DefaultDepth 24
  SubSection "Display"
    Depth 24
    Virtual 1280 720
  EndSubSection
EndSection
EOF

Xorg "$DISPLAY" -noreset -nolisten tcp -config /tmp/soren91-xorg.conf \
	-logfile "$XORG_LOG" +extension GLX +extension RANDR +extension RENDER &
XORG_PID=$!
for _ in $(seq 1 60); do
	if DISPLAY="$DISPLAY" xdpyinfo >/dev/null 2>&1; then
		break
	fi
	if ! kill -0 "$XORG_PID" 2>/dev/null; then
		tail -100 "$XORG_LOG" >&2 || true
		echo 'SOREN91_POC_RESULT={"pass":false,"reason":"Xorg exited"}'
		exit 1
	fi
	sleep 0.5
done
if ! DISPLAY="$DISPLAY" xdpyinfo >/dev/null 2>&1; then
	tail -100 "$XORG_LOG" >&2 || true
	echo 'SOREN91_POC_RESULT={"pass":false,"reason":"Xorg not ready"}'
	exit 1
fi

DISPLAY="$DISPLAY" openbox >/tmp/soren91-openbox.log 2>&1 &
WM_PID=$!

export DISPLAY PULSE_SINK=soren91_poc_sink
set +e
timeout --signal=TERM --kill-after=10s "${SESSION_SEC}s" \
	node /opt/soren91/tools/soren91_gpu_runner.mjs
runner_rc=$?
set -e
if [ "$runner_rc" -eq 124 ]; then
	echo 'SOREN91_POC_RESULT={"pass":false,"reason":"runner session timeout"}'
fi
exit "$runner_rc"
