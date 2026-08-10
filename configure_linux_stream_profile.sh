#!/bin/bash
# Apply the Oracle A1 streaming profile without printing or replacing secrets.
# Run after updating the working tree on the Linux broadcaster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$(uname -s)" != "Linux" ]; then
	echo "This profile is Linux-only." >&2
	exit 2
fi

ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ ! -f "$ENV_FILE" ]; then
	echo "Missing environment file: $ENV_FILE" >&2
	exit 2
fi

required_code=(external_game_audio.mjs browser_frame_limiter.mjs soviet_local.mjs start_all.sh)
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

set_env_value SOREN_CHROME_HEADLESS 0
set_env_value SOREN_CHROME_KIOSK 1
set_env_value SOREN_CHROME_WINDOW_SIZE 1280,720
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
set_env_value SOREN_GAME_AUDIO_PULSE_LATENCY_MS 350
set_env_value WILDCARD_PARALLEL_JOBS 1

if systemctl cat obs.service >/dev/null 2>&1; then
	priority_tmp=$(mktemp "$SCRIPT_DIR/tmp/obs-priority.XXXXXX")
	printf '%s\n' '[Service]' 'Nice=-5' 'CPUWeight=10000' >"$priority_tmp"
	sudo install -d -m 0755 /etc/systemd/system/obs.service.d
	sudo install -m 0644 "$priority_tmp" /etc/systemd/system/obs.service.d/priority.conf
	rm -f "$priority_tmp"
	sudo systemctl daemon-reload
	sudo systemctl restart obs.service
else
	echo "WARNING: obs.service was not found; OBS priority was not changed." >&2
fi

restarted_user_unit=0
for unit in start-all.service soren.service soren-supervisor.service; do
	if systemctl --user cat "$unit" >/dev/null 2>&1; then
		systemctl --user restart "$unit"
		echo "Restarted user service: $unit"
		restarted_user_unit=1
		break
	fi
done

if [ "$restarted_user_unit" -eq 0 ]; then
	./stop_soren.sh || true
	sleep 4
	./start_all.sh --daemon
fi

echo "Applied Linux stream profile. Backup: $backup"
echo "OCPUs: $(nproc)"
awk '/MemTotal/ { printf "Memory: %.1f GB\n", $2 / 1024 / 1024 }' /proc/meminfo
echo "Expected BGM: インターナショナル.ogg (until the first Soviet formation)"
echo "Render health: $SCRIPT_DIR/tmp/state/game_render_health.json"
