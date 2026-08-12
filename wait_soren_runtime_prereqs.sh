#!/bin/bash
# Prepare only the local display/audio prerequisites shared by OBS and FFmpeg.
# This script never reads stream destinations or any other credential.

set -euo pipefail

DISPLAY_NAME="${SOREN_RUNTIME_DISPLAY:-:99}"
DISPLAY_SIZE="${SOREN_RUNTIME_DISPLAY_SIZE:-1280x720}"
PULSE_SOCKET="${SOREN_RUNTIME_PULSE_SOCKET:-/run/user/1001/pulse/native}"
PULSE_SINK="${SOREN_RUNTIME_PULSE_SINK:-soren_null}"
WAIT_SEC="${SOREN_RUNTIME_PREREQ_WAIT_SEC:-90}"

case "$WAIT_SEC" in
''|*[!0-9]*)
	echo "runtime prerequisite timeout is invalid" >&2
	exit 2
	;;
esac
if [ "$WAIT_SEC" -lt 5 ] || [ "$WAIT_SEC" -gt 300 ]; then
	echo "runtime prerequisite timeout must be between 5 and 300 seconds" >&2
	exit 2
fi
case "$DISPLAY_SIZE" in
[1-9][0-9]*x[1-9][0-9]*) ;;
*)
	echo "runtime display size is invalid" >&2
	exit 2
	;;
esac
case "$PULSE_SINK" in
''|*[!A-Za-z0-9_.-]*)
	echo "runtime PulseAudio sink name is invalid" >&2
	exit 2
	;;
esac

for command_name in pactl xdpyinfo; do
	command -v "$command_name" >/dev/null 2>&1 || {
		echo "runtime prerequisite command is missing: $command_name" >&2
		exit 2
	}
done

export DISPLAY="$DISPLAY_NAME"
export PULSE_SERVER="unix:$PULSE_SOCKET"
deadline=$(( $(date +%s) + WAIT_SEC ))

while ! pactl info >/dev/null 2>&1; do
	if [ "$(date +%s)" -ge "$deadline" ]; then
		echo "PulseAudio did not become ready before the runtime deadline" >&2
		exit 1
	fi
	sleep 1
done

if ! pactl list short sinks | awk -v sink="$PULSE_SINK" '$2 == sink { found=1 } END { exit !found }'; then
	pactl load-module module-null-sink \
		"sink_name=$PULSE_SINK" \
		"sink_properties=device.description=$PULSE_SINK" >/dev/null
fi
pactl set-default-sink "$PULSE_SINK"
pactl set-default-source "$PULSE_SINK.monitor"

actual_sink=$(pactl get-default-sink)
actual_source=$(pactl get-default-source)
if [ "$actual_sink" != "$PULSE_SINK" ] || [ "$actual_source" != "$PULSE_SINK.monitor" ]; then
	echo "PulseAudio defaults could not be prepared" >&2
	exit 1
fi

while true; do
	actual_size=$(xdpyinfo -display "$DISPLAY_NAME" 2>/dev/null | awk '/dimensions:/ { print $2; exit }')
	if [ "$actual_size" = "$DISPLAY_SIZE" ]; then
		break
	fi
	if [ "$(date +%s)" -ge "$deadline" ]; then
		echo "X display did not reach the required dimensions before the runtime deadline" >&2
		exit 1
	fi
	sleep 1
done

printf 'runtime_prereqs=ok display=%s size=%s pulse_sink=%s\n' \
	"$DISPLAY_NAME" "$DISPLAY_SIZE" "$PULSE_SINK"
