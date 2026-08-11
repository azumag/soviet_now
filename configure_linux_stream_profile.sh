#!/bin/bash
# Apply the Oracle A1 streaming profile without printing or replacing secrets.
# Run after updating the working tree on the Linux broadcaster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PREPARE_DIRECT_ONLY=0
case "${1:-}" in
"") ;;
--prepare-direct-stream-only) PREPARE_DIRECT_ONLY=1 ;;
--help|-h)
	echo "Usage: $0 [--prepare-direct-stream-only]"
	exit 0
	;;
*)
	echo "Usage: $0 [--prepare-direct-stream-only]" >&2
	exit 2
	;;
esac

if [ "$(uname -s)" != "Linux" ]; then
	echo "This profile is Linux-only." >&2
	exit 2
fi

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ ! -f "$ENV_FILE" ]; then
	echo "Missing environment file: $ENV_FILE" >&2
	exit 2
fi

# Validate the optional benchmark override before any backup or .env mutation.
profile_internal_size="${SOREN_PROFILE_GAME_INTERNAL_SIZE:-}"
case "$profile_internal_size" in
"") ;;
320,180|384,216|480,270|576,324|640,360) ;;
*)
	echo "SOREN_PROFILE_GAME_INTERNAL_SIZE must be a supported 16:9 size" >&2
	exit 2
	;;
esac

required_code=(
	external_game_audio.mjs
	browser_frame_limiter.mjs
	lib/unity_canvas_size.mjs
	soviet_local.mjs
	start_all.sh
	soviet_watchdog.sh
	generate_status_overlay.sh
	generate_show_status_overlay.sh
	direct_stream.sh
	direct_stream_status.sh
	direct_stream_soak.sh
	direct_stream_recovery_test.sh
	direct_av_sync_test.sh
	cutover_direct_stream.sh
	lib/direct_stream.py
	lib/direct_soak.py
	lib/direct_av_sync.py
	lib/direct_overlay.mjs
	lib/overlay_text.py
	install_direct_stream_relay.sh
	install_soren_runtime_service.sh
	stream_backend_condition.sh
	wait_soren_runtime_prereqs.sh
	hide_obs_ui.sh
)
required_assets=(
	"sorengame/assets/BGM/インターナショナル.ogg"
	"sorengame/assets/BGM/ソ連国歌.ogg"
	"sorengame/assets/SE/落下開始SE.wav"
	"sorengame/assets/SE/合体SE.wav"
	"sorengame/assets/SE/ロシア合体時SE.wav"
	"sorengame/assets/SE/鎌と槌合体時SE.wav"
)
for file in "${required_code[@]}" "${required_assets[@]}"; do
	if [ ! -f "$file" ]; then
		echo "Required file is missing: $file" >&2
		exit 2
	fi
done

# All commands used by the direct path and its Linux status/overlay surfaces
# must exist before the first backup or .env mutation.
required_commands=(python3 node ffmpeg ffprobe pactl paplay xdpyinfo systemctl ss zsh nproc)
for command_name in "${required_commands[@]}"; do
	if ! command -v "$command_name" >/dev/null 2>&1; then
		echo "Required command is missing: $command_name" >&2
		exit 2
	fi
done

mkdir -p tmp/state logs
backup="${ENV_FILE}.stream-profile-backup.$(date '+%Y%m%d-%H%M%S')"
cp -p "$ENV_FILE" "$backup"
chmod 600 "$backup" 2>/dev/null || true

set_env_value() {
	local key="$1" value="$2" quoted tmp
	case "$key" in
	''|*[!A-Z0-9_]*)
		echo "Invalid environment key: $key" >&2
		exit 2
		;;
	esac
	quoted=${value//\'/\'\\\'\'}
	quoted="'$quoted'"
	tmp=$(mktemp "$SCRIPT_DIR/tmp/.env.profile.XXXXXX")
	awk -v key="$key" -v replacement="$key=$quoted" '
		BEGIN { replaced = 0 }
		$0 ~ "^[[:space:]]*" key "=" {
			if (!replaced) print replacement
			replaced = 1
			next
		}
		{ print }
		END { if (!replaced) print replacement }
	' "$ENV_FILE" >"$tmp"
	chmod 600 "$tmp"
	mv "$tmp" "$ENV_FILE"
}

# Prepare the FFmpeg direct-stream path without switching the live broadcaster.
# The backend remains OBS until a loopback RTMP relay has been installed and
# SOREN_STREAM_BACKEND=ffmpeg is explicitly selected by the operator.
set_env_value SOREN_STREAM_BACKEND obs
set_env_value SOREN_SOVIET_WATCHDOG_ENABLED 1
set_env_value SOREN_STATUS_OVERLAY_WATCHERS_ENABLED 1
set_env_value SOREN_DIRECT_STREAM_DISPLAY :99.0
set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720
set_env_value SOREN_DIRECT_STREAM_FPS 30
set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS 4500
set_env_value SOREN_DIRECT_STREAM_AUDIO_KBPS 160
set_env_value SOREN_DIRECT_STREAM_PULSE_SOURCE soren_null.monitor
set_env_value SOREN_DIRECT_STREAM_AUDIO_HZ 48000
set_env_value SOREN_DIRECT_STREAM_AUDIO_CHANNELS 2
set_env_value SOREN_DIRECT_STREAM_LOCAL_URL rtmp://127.0.0.1:1935/soren/live
set_env_value SOREN_DIRECT_CUTOVER_WARMUP_SEC 15
set_env_value SOREN_DIRECT_SOAK_DURATION_SEC 86400
set_env_value SOREN_DIRECT_SOAK_INTERVAL_SEC 60
set_env_value SOREN_DIRECT_SOAK_AUDIO_PROBE_SEC 1
set_env_value SOREN_DIRECT_SOAK_AUDIO_THRESHOLD_DB -60
set_env_value SOREN_DIRECT_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_OVERLAY_HTML_FILE tmp/state/event_overlay.html
set_env_value SOREN_DIRECT_EVENT_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_STATS_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_OPS_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_AV_SYNC_OVERLAY_ENABLED 1
set_env_value SOREN_DIRECT_AV_SYNC_OVERLAY_HTML_FILE tmp/state/direct_av_sync_probe.html

if [ "$PREPARE_DIRECT_ONLY" -eq 1 ]; then
	echo "Prepared FFmpeg direct-stream settings with backend=obs. Backup: $backup"
	exit 0
fi

set_env_value SOREN_CHROME_HEADLESS 0
set_env_value SOREN_CHROME_KIOSK 1
set_env_value SOREN_CHROME_WINDOW_SIZE 1280,720
if [ -n "$profile_internal_size" ]; then
	game_internal_size="$profile_internal_size"
elif [ "$(nproc)" -le 2 ]; then
	# SwiftShader, Xvfb, capture, audio and the encoder share two physical CPU
	# cores on the free-safe shape. Keep the 720p output surface, but reduce the
	# Unity render buffer one step so frame production is not starved.
	game_internal_size=480,270
else
	game_internal_size=576,324
fi
set_env_value SOREN_GAME_INTERNAL_SIZE "$game_internal_size"
set_env_value SOREN_GAME_RENDER_FPS 30
set_env_value SOREN_GAME_BGM_INITIAL_FILE "$SCRIPT_DIR/sorengame/assets/BGM/インターナショナル.ogg"
set_env_value SOREN_GAME_BGM_FILE "$SCRIPT_DIR/sorengame/assets/BGM/インターナショナル.ogg"
set_env_value SOREN_GAME_BGM_SOVIET_FILE "$SCRIPT_DIR/sorengame/assets/BGM/ソ連国歌.ogg"
set_env_value SOREN_GAME_SE_DROP_FILE "$SCRIPT_DIR/sorengame/assets/SE/落下開始SE.wav"
set_env_value SOREN_GAME_SE_MERGE_FILE "$SCRIPT_DIR/sorengame/assets/SE/合体SE.wav"
set_env_value SOREN_GAME_SE_RUSSIA_FILE "$SCRIPT_DIR/sorengame/assets/SE/ロシア合体時SE.wav"
set_env_value SOREN_GAME_SE_HAMMER_SICKLE_FILE "$SCRIPT_DIR/sorengame/assets/SE/鎌と槌合体時SE.wav"
set_env_value SOREN_GAME_BGM_VOLUME_PCT 60
set_env_value SOREN_GAME_SE_VOLUME_PCT 70
set_env_value SOREN_GAME_AUDIO_PULSE_LATENCY_MS 100
set_env_value WILDCARD_PARALLEL_JOBS 1

if systemctl cat obs.service >/dev/null 2>&1; then
	priority_tmp=$(mktemp "$SCRIPT_DIR/tmp/obs-priority.XXXXXX")
	printf '%s\n' '[Service]' 'Nice=-5' 'CPUWeight=10000' >"$priority_tmp"
	hide_ui_tmp=$(mktemp "$SCRIPT_DIR/tmp/obs-hide-ui.XXXXXX")
	printf '%s\n' '[Service]' "ExecStartPost=$SCRIPT_DIR/hide_obs_ui.sh" >"$hide_ui_tmp"
	sudo install -d -m 0755 /etc/systemd/system/obs.service.d
	sudo install -m 0644 "$priority_tmp" /etc/systemd/system/obs.service.d/priority.conf
	sudo install -m 0644 "$hide_ui_tmp" /etc/systemd/system/obs.service.d/hide-ui.conf
	rm -f "$priority_tmp" "$hide_ui_tmp"
	chmod +x "$SCRIPT_DIR/hide_obs_ui.sh"
	sudo systemctl daemon-reload
	sudo systemctl restart obs.service
else
	echo "WARNING: obs.service was not found; OBS priority was not changed." >&2
fi

restarted_supervisor=0
if systemctl is-active --quiet soren-runtime.service 2>/dev/null; then
	sudo systemctl restart soren-runtime.service
	echo "Restarted system service: soren-runtime.service"
	restarted_supervisor=1
else
	for unit in start-all.service soren.service soren-supervisor.service; do
		if systemctl --user cat "$unit" >/dev/null 2>&1; then
			systemctl --user restart "$unit"
			echo "Restarted user service: $unit"
			restarted_supervisor=1
			break
		fi
	done
fi

if [ "$restarted_supervisor" -eq 0 ]; then
	./stop_soren.sh || true
	sleep 4
	./start_all.sh --daemon
fi

echo "Applied Linux stream profile. Backup: $backup"
echo "OCPUs: $(nproc)"
awk '/MemTotal/ { printf "Memory: %.1f GB\n", $2 / 1024 / 1024 }' /proc/meminfo
echo "Expected BGM: インターナショナル.ogg (until the first Soviet formation)"
echo "Game internal size: $game_internal_size"
echo "Render health: $SCRIPT_DIR/tmp/state/game_render_health.json"
