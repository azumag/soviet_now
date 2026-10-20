#!/bin/bash
# Keep the shared broadcast PulseAudio endpoint owned by the runtime user's
# systemd service instead of an autospawned daemon.
#
# Background (docich#2008): a deploy restarted soren-bgm.service and killed a
# PulseAudio server that libpulse had autospawned into that unit's cgroup. The
# daemon came back through autospawn without XDG_RUNTIME_DIR, so its socket
# moved to /tmp/pulse-*/native, the encoder lost its capture input while video
# kept running, and the fixed path /run/user/1001/pulse/native disappeared.
#
# The reviewed fix is: the user service owns the daemon and the socket,
# client.conf disables autospawn and pins the fixed endpoint, and nginx-free
# system services never start a private server (see deploy/soren-bgm).
#
# This script is deliberately opt-in: restarting PulseAudio briefly interrupts
# broadcast audio, so --install requires --confirm-audio-session and is meant
# for an owner-approved maintenance window.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_UID="${SOREN_RUNTIME_UID:-1001}"
EXPECTED_USER="${SOREN_RUNTIME_USER:-ubuntu}"
PULSE_SOCKET="${SOREN_RUNTIME_PULSE_SOCKET:-/run/user/1001/pulse/native}"
PULSE_SINK="${SOREN_RUNTIME_PULSE_SINK:-soren_null}"
CLIENT_CONF="${SOREN_PULSE_CLIENT_CONF:-$HOME/.config/pulse/client.conf}"
WAIT_SEC="${SOREN_PULSE_SERVICE_WAIT_SEC:-30}"

usage() {
	echo "Usage: $0 --status | $0 --install --confirm-audio-session" >&2
	exit 2
}

fail() {
	echo "pulseaudio user service: $*" >&2
	exit 1
}

client_setting() {
	# Last assignment wins, matching PulseAudio's own precedence.
	[ -f "$CLIENT_CONF" ] || return 0
	sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CLIENT_CONF" | tail -n 1
}

systemctl_user() {
	systemctl --user "$@" 2>/dev/null || true
}

print_status() {
	printf 'runtime_user=%s\n' "$EXPECTED_USER"
	printf 'runtime_uid=%s\n' "$EXPECTED_UID"
	printf 'client_conf=%s\n' "$CLIENT_CONF"
	printf 'client_conf_autospawn=%s\n' "$(client_setting autospawn)"
	printf 'client_conf_default_server=%s\n' "$(client_setting default-server)"
	printf 'socket=%s\n' "$PULSE_SOCKET"
	if [ -S "$PULSE_SOCKET" ]; then
		printf 'socket_present=yes\n'
	else
		printf 'socket_present=no\n'
	fi
	printf 'socket_unit_enabled=%s\n' "$(systemctl_user is-enabled pulseaudio.socket)"
	printf 'socket_unit_active=%s\n' "$(systemctl_user is-active pulseaudio.socket)"
	printf 'server_unit_enabled=%s\n' "$(systemctl_user is-enabled pulseaudio.service)"
	printf 'server_unit_active=%s\n' "$(systemctl_user is-active pulseaudio.service)"
	printf 'server_unit_cgroup=%s\n' "$(systemctl_user show -p ControlGroup --value pulseaudio.service)"
	printf 'linger=%s\n' "$(loginctl show-user "$EXPECTED_USER" -p Linger --value 2>/dev/null || true)"
	if PULSE_SERVER="unix:$PULSE_SOCKET" pactl info >/dev/null 2>&1; then
		printf 'server_reachable=yes\n'
	else
		printf 'server_reachable=no\n'
	fi
}

require_identity() {
	[ "$(uname -s)" = "Linux" ] || fail "this operation is Linux-only"
	[ "$(id -u)" = "$EXPECTED_UID" ] || fail "run as the runtime user (uid $EXPECTED_UID)"
	[ "$(id -un)" = "$EXPECTED_USER" ] || fail "run as $EXPECTED_USER"
	[ "${XDG_RUNTIME_DIR:-}" = "/run/user/$EXPECTED_UID" ] ||
		fail "XDG_RUNTIME_DIR must be /run/user/$EXPECTED_UID"
	command -v systemctl >/dev/null 2>&1 || fail "systemctl is missing"
	command -v pactl >/dev/null 2>&1 || fail "pactl is missing"
	systemctl --user list-unit-files pulseaudio.service >/dev/null 2>&1 ||
		fail "the pulseaudio user service is not installed"
}

write_client_conf() {
	local directory backup temporary
	directory="$(dirname "$CLIENT_CONF")"
	mkdir -p "$directory"
	if [ -f "$CLIENT_CONF" ]; then
		if [ "$(client_setting autospawn)" = "no" ] &&
			[ "$(client_setting default-server)" = "unix:$PULSE_SOCKET" ] &&
			[ "$(grep -cE '^[[:space:]]*(autospawn|default-server)[[:space:]]*=' "$CLIENT_CONF")" -eq 2 ]; then
			printf 'client_conf=unchanged\n'
			return 0
		fi
		backup="$CLIENT_CONF.$(date '+%Y%m%d-%H%M%S').bak"
		[ -e "$backup" ] && fail "backup already exists: $backup"
		cp -p "$CLIENT_CONF" "$backup"
		printf 'client_conf_backup=%s\n' "$backup"
	fi
	temporary="$(mktemp "$directory/.client.conf.XXXXXX")"
	chmod 600 "$temporary"
	{
		if [ -f "$CLIENT_CONF" ]; then
			grep -vE '^[[:space:]]*(autospawn|default-server)[[:space:]]*=' "$CLIENT_CONF" || true
		fi
		printf 'autospawn = no\n'
		printf 'default-server = unix:%s\n' "$PULSE_SOCKET"
	} >"$temporary"
	mv "$temporary" "$CLIENT_CONF"
	printf 'client_conf=written\n'
}

install_service() {
	local deadline
	require_identity

	if [ "$(loginctl show-user "$EXPECTED_USER" -p Linger --value 2>/dev/null || true)" != "yes" ]; then
		sudo -n loginctl enable-linger "$EXPECTED_USER" >/dev/null 2>&1 ||
			fail "enable-linger needs an approved sudo path"
		printf 'linger=enabled\n'
	fi

	write_client_conf

	systemctl --user enable pulseaudio.socket pulseaudio.service >/dev/null
	printf 'units=enabled\n'

	# Only reached with --confirm-audio-session: active clients reconnect.
	systemctl --user restart pulseaudio.socket pulseaudio.service
	printf 'units=restarted\n'

	deadline=$(( $(date +%s) + WAIT_SEC ))
	while [ ! -S "$PULSE_SOCKET" ]; do
		if [ "$(date +%s)" -ge "$deadline" ]; then
			fail "socket did not appear at $PULSE_SOCKET"
		fi
		sleep 1
	done
	PULSE_SERVER="unix:$PULSE_SOCKET" pactl info >/dev/null ||
		fail "server does not answer on $PULSE_SOCKET"

	if ! PULSE_SERVER="unix:$PULSE_SOCKET" pactl list short sinks |
		awk -v sink="$PULSE_SINK" '$2 == sink { found = 1 } END { exit !found }'; then
		PULSE_SERVER="unix:$PULSE_SOCKET" pactl load-module module-null-sink \
			"sink_name=$PULSE_SINK" \
			"sink_properties=device.description=$PULSE_SINK" >/dev/null
		printf 'null_sink=loaded\n'
	fi

	print_status
}

case "$*" in
--status)
	print_status
	;;
--install\ --confirm-audio-session)
	install_service
	;;
*)
	usage
	;;
esac
