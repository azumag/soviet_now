#!/bin/bash
# FFmpeg direct-capture benchmark on the Linux broadcaster. On a 4+ CPU host
# it compares native capacity with a 2-CPU affinity approximation. On the
# final 2-CPU shape it records one actual2 profile. This intentionally
# interrupts OBS streaming, records locally, and restores OBS in an EXIT trap.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
ENV_FILE="${SOREN_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
	set -a
	# shellcheck disable=SC1090
	. "$ENV_FILE"
	set +a
fi

DURATION=30
OUTPUT_ROOT=""
CONFIRM=0
PRINT_CONFIG=0

usage() {
	cat <<'EOF' >&2
Usage:
  ./benchmark_direct_stream.sh --confirm-live-interruption [--duration SEC] [--output-dir DIR]
  ./benchmark_direct_stream.sh --print-config

Samples the live OBS baseline, then runs bounded local recording(s) while OBS is stopped:
  obs_baseline  live OBS process/system CPU and game FPS without mutation
  actual2       actual 2-OCPU host, without a synthetic affinity limit
  native4       current 4+-OCPU host without a Soren CPU affinity limit
  affinity2     known Soren/Chromium/Pulse/Xvfb processes limited to CPUs 0-1

The affinity2 result is preliminary. The actual2 result is the final CPU-shape
measurement. Memory is observed but never constrained by this script.
EOF
	exit 2
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	--confirm-live-interruption)
		CONFIRM=1
		shift
		;;
	--duration)
		[ "$#" -ge 2 ] || usage
		DURATION="$2"
		shift 2
		;;
	--output-dir)
		[ "$#" -ge 2 ] || usage
		OUTPUT_ROOT="$2"
		shift 2
		;;
	--print-config)
		PRINT_CONFIG=1
		shift
		;;
	*) usage ;;
	esac
done

case "$DURATION" in
''|*[!0-9]*) echo "duration must be an integer" >&2; exit 2 ;;
esac
if [ "$DURATION" -lt 10 ] || [ "$DURATION" -gt 3600 ]; then
	echo "duration must be between 10 and 3600 seconds" >&2
	exit 2
fi

XVFB_UNIT="${SOREN_XVFB_SYSTEMD_UNIT:-xvfb.service}"
OBS_UNIT="${OBS_SYSTEMD_UNIT:-obs.service}"
if [ -z "$OUTPUT_ROOT" ]; then
	OUTPUT_ROOT="$SCRIPT_DIR/tmp/direct_stream_benchmark/$(date '+%Y%m%d-%H%M%S')"
elif [[ "$OUTPUT_ROOT" != /* ]]; then
	OUTPUT_ROOT="$SCRIPT_DIR/$OUTPUT_ROOT"
fi

if [ "$PRINT_CONFIG" -eq 1 ]; then
	python3 - "$DURATION" "$OUTPUT_ROOT" "$XVFB_UNIT" "$OBS_UNIT" <<'PY'
import json
import sys
print(json.dumps({
    "duration_sec": int(sys.argv[1]),
    "output_dir": sys.argv[2],
    "profiles": ["obs_baseline", "actual2", "native4", "affinity2"],
    "selection": "actual2 on exactly 2 CPUs; native4+affinity2 on 4 or more CPUs",
    "xvfb_unit": sys.argv[3],
    "obs_unit": sys.argv[4],
    "affinity2_allowed_cpus": "0,1",
    "memory_limit_applied": False,
    "memory_observation_only": True,
    "actual_shape_resize": False,
}, ensure_ascii=False, sort_keys=True))
PY
	exit 0
fi

if [ "$CONFIRM" -ne 1 ]; then
	echo "refusing to interrupt OBS without --confirm-live-interruption" >&2
	exit 2
fi
if [ "$(uname -s)" != "Linux" ]; then
	echo "benchmark is Linux-only" >&2
	exit 2
fi
HOST_CPUS=$(nproc)
if [ "$HOST_CPUS" -eq 2 ]; then
	BENCHMARK_MODE=actual2
elif [ "$HOST_CPUS" -ge 4 ]; then
	BENCHMARK_MODE=preliminary4
else
	echo "benchmark requires exactly 2 CPUs or at least 4 CPUs" >&2
	exit 2
fi
for command in sudo systemctl taskset pgrep ps ffprobe ffmpeg mpstat pidstat jq /usr/bin/time; do
	if ! command -v "$command" >/dev/null 2>&1; then
		echo "required command not found: $command" >&2
		exit 2
	fi
done
sudo -n true

# All prerequisite checks happen before OBS or process affinity is touched.
./direct_stream.sh validate --mode record >/dev/null
systemctl is-active --quiet "$XVFB_UNIT"
systemctl is-active --quiet "$OBS_UNIT"

mkdir -p "$OUTPUT_ROOT"
chmod 700 "$OUTPUT_ROOT" 2>/dev/null || true

OBS_WAS_ACTIVE=1
OBS_WAS_STREAMING=0
if [ "$(./obs_control.sh stream-status 2>/dev/null || true)" = "streaming=on" ]; then
	OBS_WAS_STREAMING=1
fi
if [ "$OBS_WAS_STREAMING" -ne 1 ]; then
	echo "OBS must be streaming to collect a comparable live baseline" >&2
	exit 2
fi
RESTORE_DONE=0
AFFINITY_RESTORE_FILE="$OUTPUT_ROOT/affinity_restore.tsv"
: >"$AFFINITY_RESTORE_FILE"

descendants_of() {
	local parent="$1" child
	for child in $(pgrep -P "$parent" 2>/dev/null || true); do
		printf '%s\n' "$child"
		descendants_of "$child"
	done
}

collect_affinity_targets() {
	local root
	printf '%s\n' "$$"
	for root in \
		$(pgrep -f '^node soviet_local[.]mjs$' 2>/dev/null || true) \
		$(pgrep -f '[/]bin/bash ./start_all[.]sh --supervisor' 2>/dev/null || true) \
		$(pgrep -f 'tmux new-session.*soren_bridge' 2>/dev/null || true) \
		$(pgrep -u "$(id -u)" -x pulseaudio 2>/dev/null || true) \
		$(pgrep -f '^/usr/bin/Xvfb :99([[:space:]]|$)' 2>/dev/null || true); do
		printf '%s\n' "$root"
		descendants_of "$root"
	done | awk '/^[0-9]+$/ && !seen[$0]++'
}

apply_affinity2() {
	local pid affinity
	: >"$AFFINITY_RESTORE_FILE"
	while IFS= read -r pid; do
		[ -r "/proc/$pid/status" ] || continue
		affinity=$(taskset -pc "$pid" 2>/dev/null | sed -n 's/^.*: //p' | tail -n 1)
		[ -n "$affinity" ] || continue
		printf '%s\t%s\n' "$pid" "$affinity" >>"$AFFINITY_RESTORE_FILE"
		sudo taskset -pc 0,1 "$pid" >/dev/null
	done < <(collect_affinity_targets)
}

restore_affinities() {
	local pid affinity
	[ -f "$AFFINITY_RESTORE_FILE" ] || return 0
	while IFS=$'\t' read -r pid affinity; do
		[ -n "$pid" ] && [ -n "$affinity" ] || continue
		[ -r "/proc/$pid/status" ] || continue
		sudo taskset -pc "$affinity" "$pid" >/dev/null 2>&1 || true
	done <"$AFFINITY_RESTORE_FILE"
}

wait_for_obs_stream_restore() {
	local attempt
	: >"$OUTPUT_ROOT/obs-restore.log"
	for attempt in $(seq 1 "${OBS_RESTORE_ATTEMPTS:-30}"); do
		if [ "$(./obs_control.sh stream-status 2>/dev/null || true)" = "streaming=on" ]; then
			return 0
		fi
		# obs.service already starts with --startstreaming. Give that first RTMP
		# attempt ten seconds to finish so a websocket request does not overlap
		# it. If the ingest endpoint rejects that early connection, retry every
		# ten seconds and verify the resulting state instead of treating the
		# asynchronous StartStream acknowledgement as recovery.
		if [ "$attempt" -ge 6 ] && [ "$(( (attempt - 6) % 5 ))" -eq 0 ]; then
			printf 'attempt=%s\n' "$attempt" >>"$OUTPUT_ROOT/obs-restore.log"
			./obs_control.sh stream-start >>"$OUTPUT_ROOT/obs-restore.log" 2>&1 || true
		fi
		sleep "${OBS_RESTORE_POLL_SEC:-2}"
	done
	echo "OBS stream did not return to streaming=on" >&2
	return 1
}

restore_runtime() {
	[ "$RESTORE_DONE" -eq 0 ] || return 0
	RESTORE_DONE=1
	restore_affinities
	if [ "$OBS_WAS_ACTIVE" -eq 1 ]; then
		sudo systemctl start "$OBS_UNIT" >/dev/null 2>&1 || true
		if [ "$OBS_WAS_STREAMING" -eq 1 ]; then
			wait_for_obs_stream_restore
		fi
	fi
}
trap restore_runtime EXIT INT TERM

sample_game_fps() {
	local output="$1" seconds="$2" deadline
	deadline=$(( $(date +%s) + seconds ))
	while [ "$(date +%s)" -lt "$deadline" ]; do
		if [ -s tmp/state/game_render_health.json ]; then
			jq -c --argjson sampled_at "$(date +%s)" '. + {sampledAt: $sampled_at}' \
				tmp/state/game_render_health.json >>"$output" 2>/dev/null || true
		fi
		sleep 2
	done
}

benchmark_obs_baseline() {
	local profile_dir="$OUTPUT_ROOT/obs_baseline" obs_pid sampler_pid mpstat_pid pidstat_pid
	mkdir -p "$profile_dir"
	obs_pid=$(systemctl show "$OBS_UNIT" -p MainPID --value)
	case "$obs_pid" in ''|0|*[!0-9]*) echo "could not resolve active OBS MainPID" >&2; return 1 ;; esac
	printf '%s\n' "$obs_pid" >"$profile_dir/obs_pid"
	ps -eo pid=,ppid=,rss=,comm=,args= >"$profile_dir/processes_before.txt"
	sample_game_fps "$profile_dir/game_fps.jsonl" "$DURATION" &
	sampler_pid=$!
	mpstat -P ALL 1 "$DURATION" >"$profile_dir/mpstat.txt" 2>&1 &
	mpstat_pid=$!
	pidstat -u -p "$obs_pid" 1 "$DURATION" >"$profile_dir/pidstat_obs.txt" 2>&1 &
	pidstat_pid=$!
	wait "$sampler_pid"
	wait "$mpstat_pid"
	wait "$pidstat_pid"
}

benchmark_profile() {
	local profile="$1" profile_dir="$OUTPUT_ROOT/$1" capture sampler_pid mpstat_pid pidstat_all_pid
	mkdir -p "$profile_dir"
	capture="$profile_dir/capture.mkv"
	ps -eo pid=,ppid=,rss=,comm=,args= >"$profile_dir/processes_before.txt"
	sample_game_fps "$profile_dir/game_fps.jsonl" "$DURATION" &
	sampler_pid=$!
	mpstat -P ALL 1 "$DURATION" >"$profile_dir/mpstat.txt" 2>&1 &
	mpstat_pid=$!
	pidstat -u 1 "$DURATION" >"$profile_dir/pidstat_all.txt" 2>&1 &
	pidstat_all_pid=$!
	set +e
	/usr/bin/time -v -o "$profile_dir/time.txt" \
		./direct_stream.sh record --output "$capture" --duration "$DURATION"
	local rc=$?
	set -e
	wait "$sampler_pid" 2>/dev/null || true
	wait "$mpstat_pid" 2>/dev/null || true
	wait "$pidstat_all_pid" 2>/dev/null || true
	./direct_stream_status.sh >"$profile_dir/direct_status.json"
	if [ "$rc" -eq 0 ] && [ -s "$capture" ]; then
		ffprobe -v error \
			-show_entries stream=index,codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate,sample_rate,channels,duration \
			-of json "$capture" >"$profile_dir/ffprobe.json"
		ffmpeg -hide_banner -nostdin -v info -i "$capture" -map 0:a:0 \
			-af volumedetect -f null - >/dev/null 2>"$profile_dir/audio_volumedetect.txt" || true
	fi
	printf '%s\n' "$rc" >"$profile_dir/exit_code"
	return "$rc"
}

benchmark_obs_baseline

./obs_control.sh stream-stop >"$OUTPUT_ROOT/obs-stop.log" 2>&1
sleep 2
sudo systemctl stop "$OBS_UNIT"

sleep 4
if [ "$BENCHMARK_MODE" = actual2 ]; then
	benchmark_profile actual2
else
	benchmark_profile native4
	apply_affinity2
	sleep 4
	benchmark_profile affinity2
fi

restore_runtime
trap - EXIT INT TERM

python3 - "$OUTPUT_ROOT" "$SCRIPT_DIR" <<'PY'
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, sys.argv[2])
from lib.direct_benchmark import build_comparison

root = Path(sys.argv[1])
summary = {"output_dir": str(root), "profiles": {}}

def game_fps(directory):
    samples = []
    try:
        for line in (directory / "game_fps.jsonl").read_text().splitlines():
            value = float(json.loads(line).get("measuredFps", 0))
            if value > 0:
                samples.append(value)
    except Exception:
        pass
    return samples

def system_busy(directory):
    try:
        for line in (directory / "mpstat.txt").read_text(errors="replace").splitlines():
            parts = line.split()
            if line.startswith("Average:") and len(parts) > 2 and parts[1] == "all":
                return round(100.0 - float(parts[-1]), 3)
    except Exception:
        pass
    return None

obs_dir = root / "obs_baseline"
obs_samples = game_fps(obs_dir)
obs_cpu = None
try:
    for line in (obs_dir / "pidstat_obs.txt").read_text(errors="replace").splitlines():
        if line.strip().startswith("Average:") and "obs" in line:
            obs_cpu = float(line.split()[-3])
except Exception:
    pass
summary["profiles"]["obs_baseline"] = {
    "obs_cpu_pct": obs_cpu,
    "system_busy_pct": system_busy(obs_dir),
    "game_fps_mean": round(statistics.fmean(obs_samples), 3) if obs_samples else None,
    "game_fps_min": round(min(obs_samples), 3) if obs_samples else None,
    "game_fps_max": round(max(obs_samples), 3) if obs_samples else None,
    "game_fps_samples": len(obs_samples),
}

direct_names = [
    name for name in ("actual2", "native4", "affinity2")
    if (root / name / "exit_code").is_file()
]
for name in direct_names:
    directory = root / name
    try:
        status = json.loads((directory / "direct_status.json").read_text())
    except Exception:
        status = {}
    samples = game_fps(directory)
    encoder_cpu = None
    try:
        for line in (directory / "time.txt").read_text(errors="replace").splitlines():
            if "Percent of CPU this job got" in line:
                encoder_cpu = float(line.split(":", 1)[1].strip().rstrip("%"))
    except Exception:
        pass
    summary["profiles"][name] = {
        "ffmpeg_fps": status.get("fps"),
        "ffmpeg_speed": status.get("speed"),
        "drop_frames": status.get("drop_frames"),
        "dup_frames": status.get("dup_frames"),
        "encoded_frames": status.get("frame"),
        "game_fps_mean": round(statistics.fmean(samples), 3) if samples else None,
        "game_fps_min": round(min(samples), 3) if samples else None,
        "game_fps_max": round(max(samples), 3) if samples else None,
        "game_fps_samples": len(samples),
        "encoder_cpu_pct": encoder_cpu,
        "system_busy_pct": system_busy(directory),
        "exit_code": int((directory / "exit_code").read_text().strip()),
    }

obs_profile = summary["profiles"]["obs_baseline"]
primary_direct_name = "actual2" if "actual2" in summary["profiles"] else "native4"
direct_profile = summary["profiles"][primary_direct_name]
summary["comparison"] = build_comparison(
    obs_profile, direct_profile, primary_direct_name
)
(root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
