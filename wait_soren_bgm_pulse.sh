#!/bin/bash
# Fail-closed PulseAudio prerequisite for soren-bgm.service (docich #2008).
#
# This system service plays BGM into the SHARED PulseAudio server owned by the
# ubuntu user manager (pulseaudio.socket / pulseaudio.service). It must never
# start, restart, or autospawn an audio server of its own: a server spawned
# inside this unit's cgroup dies with the unit on every deploy restart, which
# is exactly how the shared stream audio was lost on 2026-10-11.
#
# Therefore this guard only verifies that the canonical server answers and that
# the shared BGM sink exists (creating the sink is the same idempotent
# module-null-sink load that wait_soren_runtime_prereqs.sh performs). When the
# canonical server is not reachable the unit fails closed and systemd retries
# it (Restart=on-failure); it never spawns a replacement server.

set -euo pipefail

PULSE_SERVER="${SOREN_BGM_PULSE_SERVER:-unix:/run/user/$UID/pulse/native}"
PULSE_SINK="${SOREN_BGM_SINK:-soren_null}"
WAIT_SEC="${SOREN_BGM_PULSE_WAIT_SEC:-60}"

case "$WAIT_SEC" in
''|*[!0-9]*)
	echo "soren-bgm pulse prerequisite timeout is invalid" >&2
	exit 2
	;;
esac
if [ "$WAIT_SEC" -lt 5 ] || [ "$WAIT_SEC" -gt 300 ]; then
	echo "soren-bgm pulse prerequisite timeout must be between 5 and 300 seconds" >&2
	exit 2
fi
case "$PULSE_SINK" in
''|*[!A-Za-z0-9_.-]*)
	echo "soren-bgm PulseAudio sink name is invalid" >&2
	exit 2
	;;
esac

command -v pactl >/dev/null 2>&1 || {
	echo "soren-bgm pulse prerequisite command is missing: pactl" >&2
	exit 2
}

export PULSE_SERVER
deadline=$(( $(date +%s) + WAIT_SEC ))

while ! pactl info >/dev/null 2>&1; do
	if [ "$(date +%s)" -ge "$deadline" ]; then
		echo "canonical PulseAudio (user service) did not become ready before the soren-bgm deadline" >&2
		echo "refusing to start BGM: this unit must not autospawn a private PulseAudio server" >&2
		exit 1
	fi
	sleep 1
done

if ! pactl list short sinks | awk -v sink="$PULSE_SINK" '$2 == sink { found=1 } END { exit !found }'; then
	pactl load-module module-null-sink \
		"sink_name=$PULSE_SINK" \
		"sink_properties=device.description=$PULSE_SINK" >/dev/null
fi

printf 'soren_bgm_pulse=ok pulse_server=%s pulse_sink=%s\n' "$PULSE_SERVER" "$PULSE_SINK"
