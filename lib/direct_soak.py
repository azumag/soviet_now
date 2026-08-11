#!/usr/bin/env python3
"""Detached soak monitor and acceptance summary for FFmpeg direct streaming."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]


class SoakConfigError(RuntimeError):
    pass


class SoakRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SoakConfig:
    backend: str
    duration_sec: int
    interval_sec: int
    state_dir: Path
    direct_status_file: Path
    game_health_file: Path
    relay_unit: str
    obs_unit: str
    ffmpeg_bin: str
    pulse_source: str
    audio_probe_duration_sec: int
    audio_silence_threshold_db: float

    def public_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "duration_sec": self.duration_sec,
            "interval_sec": self.interval_sec,
            "state_dir": str(self.state_dir),
            "direct_status_file": str(self.direct_status_file),
            "game_health_file": str(self.game_health_file),
            "relay_unit": self.relay_unit,
            "obs_unit": self.obs_unit,
            "ffmpeg_bin": self.ffmpeg_bin,
            "pulse_source": self.pulse_source,
            "audio_probe_duration_sec": self.audio_probe_duration_sec,
            "audio_silence_threshold_db": self.audio_silence_threshold_db,
        }


def _path(raw: str, fallback: str) -> Path:
    value = str(raw or "").strip()
    path = Path(value) if value else Path(fallback)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def _strict_int(raw: object, name: str, minimum: int, maximum: int) -> int:
    text = str(raw).strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise SoakConfigError(f"{name} must be an integer")
    value = int(text)
    if not minimum <= value <= maximum:
        raise SoakConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _strict_float(raw: object, name: str, minimum: float, maximum: float) -> float:
    text = str(raw).strip()
    if not re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", text):
        raise SoakConfigError(f"{name} must be a number")
    value = float(text)
    if not minimum <= value <= maximum:
        raise SoakConfigError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    duration_override: int | None = None,
    interval_override: int | None = None,
) -> SoakConfig:
    source = os.environ if env is None else env
    backend = str(source.get("SOREN_STREAM_BACKEND", "obs")).strip().lower()
    if backend not in {"obs", "ffmpeg"}:
        raise SoakConfigError("SOREN_STREAM_BACKEND must be obs or ffmpeg")
    duration_raw: object = (
        duration_override
        if duration_override is not None
        else source.get("SOREN_DIRECT_SOAK_DURATION_SEC", "86400")
    )
    interval_raw: object = (
        interval_override
        if interval_override is not None
        else source.get("SOREN_DIRECT_SOAK_INTERVAL_SEC", "60")
    )
    duration = _strict_int(duration_raw, "duration", 10, 172800)
    interval = _strict_int(interval_raw, "interval", 1, 300)
    if interval >= duration:
        raise SoakConfigError("interval must be shorter than duration")
    pulse_source = str(
        source.get("SOREN_DIRECT_STREAM_PULSE_SOURCE", "soren_null.monitor")
    ).strip()
    if not pulse_source or not re.fullmatch(r"[A-Za-z0-9_.:@+-]+", pulse_source):
        raise SoakConfigError("SOREN_DIRECT_STREAM_PULSE_SOURCE contains unsupported characters")
    ffmpeg_bin = str(source.get("SOREN_DIRECT_STREAM_FFMPEG_BIN", "ffmpeg")).strip()
    if not ffmpeg_bin or any(character in ffmpeg_bin for character in "\r\n\x00"):
        raise SoakConfigError("SOREN_DIRECT_STREAM_FFMPEG_BIN is invalid")
    direct_state_dir = _path(source.get("SOREN_DIRECT_STREAM_STATE_DIR", ""), "tmp/state/direct_stream")
    return SoakConfig(
        backend=backend,
        duration_sec=duration,
        interval_sec=interval,
        state_dir=_path(source.get("SOREN_DIRECT_SOAK_STATE_DIR", ""), "tmp/state/direct_soak"),
        direct_status_file=direct_state_dir / "status.json",
        game_health_file=_path(
            source.get("SOREN_GAME_RENDER_HEALTH_FILE", ""),
            "tmp/state/game_render_health.json",
        ),
        relay_unit="soren-rtmp-relay.service",
        obs_unit=str(source.get("OBS_SYSTEMD_UNIT", "obs.service") or "obs.service"),
        ffmpeg_bin=ffmpeg_bin,
        pulse_source=pulse_source,
        audio_probe_duration_sec=_strict_int(
            source.get("SOREN_DIRECT_SOAK_AUDIO_PROBE_SEC", "1"),
            "audio probe duration",
            1,
            10,
        ),
        audio_silence_threshold_db=_strict_float(
            source.get("SOREN_DIRECT_SOAK_AUDIO_THRESHOLD_DB", "-60"),
            "audio silence threshold",
            -90,
            -10,
        ),
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _pid_alive(value: object) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _service_state(unit: str) -> dict[str, object]:
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=3,
        check=False,
    ).returncode == 0
    restarts = 0
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "NRestarts", "--value"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            restarts = int(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"active": active, "restarts": restarts}


def _direct_snapshot(path: Path) -> dict[str, object]:
    state = _read_json(path)
    allowed = (
        "state",
        "mode",
        "pid",
        "ffmpeg_pid",
        "started_at",
        "updated_at",
        "frame",
        "fps",
        "speed",
        "bitrate",
        "drop_frames",
        "dup_frames",
        "out_time_us",
    )
    result = {key: state.get(key) for key in allowed if key in state}
    result["running"] = bool(state.get("running")) and (
        _pid_alive(state.get("pid")) or _pid_alive(state.get("ffmpeg_pid"))
    )
    return result


def _game_snapshot(path: Path) -> dict[str, object]:
    state = _read_json(path)
    allowed = (
        "limitedToFps",
        "measuredFps",
        "lastFrameAt",
        "visibility",
        "canvasWidth",
        "canvasHeight",
        "observedAt",
    )
    return {key: state.get(key) for key in allowed if key in state}


def _process_cpu_seconds(pid_value: object) -> float | None:
    try:
        pid = int(pid_value)
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2 :].split()
        ticks = int(fields[11]) + int(fields[12])
        return round(ticks / os.sysconf("SC_CLK_TCK"), 3)
    except (IndexError, OSError, TypeError, ValueError):
        return None


def _system_snapshot(ffmpeg_pid: object) -> dict[str, object]:
    result: dict[str, object] = {}
    try:
        load = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        result["load1"] = float(load[0])
        result["load5"] = float(load[1])
        result["load15"] = float(load[2])
    except (OSError, ValueError, IndexError):
        pass
    try:
        memory: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            number = value.strip().split()[0]
            if number.isdigit():
                memory[key] = int(number)
        result["mem_total_kib"] = memory.get("MemTotal", 0)
        result["mem_available_kib"] = memory.get("MemAvailable", 0)
    except OSError:
        pass
    cpu_seconds = _process_cpu_seconds(ffmpeg_pid)
    if cpu_seconds is not None:
        result["ffmpeg_cpu_seconds"] = cpu_seconds
    return result


def _publisher_count() -> int:
    try:
        result = subprocess.run(
            ["ps", "-eo", "args="],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1
    pattern = re.compile(r"(?:^|/)lib/direct_stream[.]py[ \t]+run(?:[ \t]|$)")
    return sum(1 for line in result.stdout.splitlines() if pattern.search(line))


def _relay_publisher_connection_count() -> int:
    try:
        result = subprocess.run(
            [
                "ss",
                "-Htn",
                "state",
                "established",
                "(",
                "sport",
                "=",
                ":1935",
                ")",
            ],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1
    if result.returncode != 0:
        return -1
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def _volume_db(stderr: str, field: str) -> float | None:
    match = re.search(
        rf"{re.escape(field)}:\s*(-?inf|[-+]?[0-9]+(?:\.[0-9]+)?)\s*dB",
        stderr,
        re.IGNORECASE,
    )
    if not match:
        return None
    if match.group(1).lower() == "-inf":
        return -120.0
    return float(match.group(1))


def _probe_audio(config: SoakConfig) -> dict[str, object]:
    command = [
        config.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "info",
        "-f",
        "pulse",
        "-i",
        config.pulse_source,
        "-t",
        str(config.audio_probe_duration_sec),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=config.audio_probe_duration_sec + 5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "non_silent": False, "error": "probe_failed"}
    mean_db = _volume_db(result.stderr, "mean_volume")
    max_db = _volume_db(result.stderr, "max_volume")
    if result.returncode != 0 or mean_db is None or max_db is None:
        return {"ok": False, "non_silent": False, "error": "probe_failed"}
    return {
        "ok": True,
        "non_silent": max_db >= config.audio_silence_threshold_db,
        "mean_db": mean_db,
        "max_db": max_db,
        "threshold_db": config.audio_silence_threshold_db,
        "duration_sec": config.audio_probe_duration_sec,
    }


def collect_sample(config: SoakConfig, sampled_at: float | None = None) -> dict[str, object]:
    direct = _direct_snapshot(config.direct_status_file)
    return {
        "sampled_at": time.time() if sampled_at is None else sampled_at,
        "direct": direct,
        "game": _game_snapshot(config.game_health_file),
        "relay": _service_state(config.relay_unit),
        "obs": _service_state(config.obs_unit),
        "publisher_process_count": _publisher_count(),
        "relay_publisher_connection_count": _relay_publisher_connection_count(),
        "audio": _probe_audio(config),
        "system": _system_snapshot(direct.get("ffmpeg_pid")),
    }


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def summarize_samples(samples: Sequence[Mapping[str, object]], expected_duration: int) -> dict[str, object]:
    rows = sorted(samples, key=lambda item: _number(item.get("sampled_at")))
    elapsed = max(0.0, _number(rows[-1].get("sampled_at")) - _number(rows[0].get("sampled_at"))) if len(rows) >= 2 else 0.0
    frame_delta = 0
    frame_seconds = 0.0
    drop_delta = 0
    dup_delta = 0
    drop_growth_intervals = 0
    max_consecutive_drop_growth = 0
    consecutive_drop_growth = 0
    restart_count = 0
    previous: Mapping[str, object] | None = None
    speed_values: list[float] = []
    status_fps_values: list[float] = []
    game_fps_values: list[float] = []
    running_count = 0
    relay_active_count = 0
    obs_inactive_count = 0
    publisher_counts: list[int] = []
    relay_publisher_counts: list[int] = []
    audio_probe_success_count = 0
    audio_present_count = 0
    audio_mean_db_values: list[float] = []
    audio_max_db_values: list[float] = []

    for row in rows:
        direct = row.get("direct") if isinstance(row.get("direct"), Mapping) else {}
        game = row.get("game") if isinstance(row.get("game"), Mapping) else {}
        relay = row.get("relay") if isinstance(row.get("relay"), Mapping) else {}
        obs = row.get("obs") if isinstance(row.get("obs"), Mapping) else {}
        audio = row.get("audio") if isinstance(row.get("audio"), Mapping) else {}
        if direct.get("running") is True:
            running_count += 1
        if relay.get("active") is True:
            relay_active_count += 1
        if obs.get("active") is False:
            obs_inactive_count += 1
        publisher = int(_number(row.get("publisher_process_count"), -1))
        if publisher >= 0:
            publisher_counts.append(publisher)
        relay_publisher = int(_number(row.get("relay_publisher_connection_count"), -1))
        if relay_publisher >= 0:
            relay_publisher_counts.append(relay_publisher)
        if _number(direct.get("speed")) > 0:
            speed_values.append(_number(direct.get("speed")))
        if _number(direct.get("fps")) > 0:
            status_fps_values.append(_number(direct.get("fps")))
        if _number(game.get("measuredFps")) > 0:
            game_fps_values.append(_number(game.get("measuredFps")))
        if audio.get("ok") is True:
            audio_probe_success_count += 1
            if audio.get("non_silent") is True:
                audio_present_count += 1
            if audio.get("mean_db") is not None:
                audio_mean_db_values.append(_number(audio.get("mean_db")))
            if audio.get("max_db") is not None:
                audio_max_db_values.append(_number(audio.get("max_db")))
        if previous is not None:
            prior_direct = previous.get("direct") if isinstance(previous.get("direct"), Mapping) else {}
            same_run = direct.get("started_at") and direct.get("started_at") == prior_direct.get("started_at")
            delta_time = _number(row.get("sampled_at")) - _number(previous.get("sampled_at"))
            if same_run and delta_time > 0:
                frames = int(_number(direct.get("frame"))) - int(_number(prior_direct.get("frame")))
                drops = int(_number(direct.get("drop_frames"))) - int(_number(prior_direct.get("drop_frames")))
                dups = int(_number(direct.get("dup_frames"))) - int(_number(prior_direct.get("dup_frames")))
                if frames >= 0:
                    frame_delta += frames
                    frame_seconds += delta_time
                if drops > 0:
                    drop_delta += drops
                    drop_growth_intervals += 1
                    consecutive_drop_growth += 1
                    max_consecutive_drop_growth = max(max_consecutive_drop_growth, consecutive_drop_growth)
                else:
                    consecutive_drop_growth = 0
                if dups > 0:
                    dup_delta += dups
            elif direct.get("started_at") != prior_direct.get("started_at"):
                restart_count += 1
        previous = row

    count = len(rows)
    mean_output_fps = frame_delta / frame_seconds if frame_seconds > 0 else 0.0
    running_ratio = running_count / count if count else 0.0
    relay_ratio = relay_active_count / count if count else 0.0
    obs_inactive_ratio = obs_inactive_count / count if count else 0.0
    publisher_max = max(publisher_counts) if publisher_counts else -1
    publisher_min = min(publisher_counts) if publisher_counts else -1
    relay_publisher_max = max(relay_publisher_counts) if relay_publisher_counts else -1
    relay_publisher_min = min(relay_publisher_counts) if relay_publisher_counts else -1
    speed_p05 = _percentile(speed_values, 0.05)
    audio_probe_success_ratio = audio_probe_success_count / count if count else 0.0
    audio_present_ratio = audio_present_count / count if count else 0.0
    requirements = {
        "duration_covered": elapsed >= expected_duration * 0.99,
        "mean_output_fps_29_5": mean_output_fps >= 29.5,
        "speed_p05_0_98": speed_p05 is not None and speed_p05 >= 0.98,
        "drop_not_continuous": max_consecutive_drop_growth < 2,
        "single_publisher": publisher_min == 1 and publisher_max == 1,
        "single_relay_publisher_connection": (
            relay_publisher_min == 1 and relay_publisher_max == 1
        ),
        "direct_running_ratio_0_99": running_ratio >= 0.99,
        "relay_active_all_samples": relay_ratio == 1.0,
        "obs_inactive_all_samples": obs_inactive_ratio == 1.0,
        "audio_probe_success_ratio_0_99": audio_probe_success_ratio >= 0.99,
        "combined_audio_present_ratio_0_99": audio_present_ratio >= 0.99,
    }
    return {
        "ok": all(requirements.values()),
        "requirements": requirements,
        "sample_count": count,
        "elapsed_sec": round(elapsed, 3),
        "expected_duration_sec": expected_duration,
        "mean_output_fps": round(mean_output_fps, 4),
        "mean_status_fps": round(statistics.fmean(status_fps_values), 4) if status_fps_values else None,
        "speed_p05": round(speed_p05, 4) if speed_p05 is not None else None,
        "speed_min": round(min(speed_values), 4) if speed_values else None,
        "drop_delta": drop_delta,
        "dup_delta": dup_delta,
        "drop_growth_intervals": drop_growth_intervals,
        "max_consecutive_drop_growth": max_consecutive_drop_growth,
        "restart_count": restart_count,
        "direct_running_ratio": round(running_ratio, 6),
        "relay_active_ratio": round(relay_ratio, 6),
        "obs_inactive_ratio": round(obs_inactive_ratio, 6),
        "publisher_count_min": publisher_min,
        "publisher_count_max": publisher_max,
        "relay_publisher_connection_count_min": relay_publisher_min,
        "relay_publisher_connection_count_max": relay_publisher_max,
        "game_fps_mean": round(statistics.fmean(game_fps_values), 4) if game_fps_values else None,
        "audio_probe_success_ratio": round(audio_probe_success_ratio, 6),
        "combined_audio_present_ratio": round(audio_present_ratio, 6),
        "audio_mean_db_mean": (
            round(statistics.fmean(audio_mean_db_values), 3) if audio_mean_db_values else None
        ),
        "audio_max_db_max": round(max(audio_max_db_values), 3) if audio_max_db_values else None,
        "not_measured_by_soak_monitor": [
            "individual_bgm_se_tts_source_identity",
            "audio_video_sync_ms",
            "overlay_visual_parity",
            "twitch_player_codec_probe",
            "cpu_reduction_vs_obs",
        ],
    }


def _validate_runtime(config: SoakConfig) -> None:
    if sys.platform != "linux":
        raise SoakRuntimeError("soak monitoring is Linux-only")
    if config.backend != "ffmpeg":
        raise SoakRuntimeError("SOREN_STREAM_BACKEND must be ffmpeg")
    direct = _direct_snapshot(config.direct_status_file)
    if direct.get("running") is not True:
        raise SoakRuntimeError("direct stream is not running")
    if not _service_state(config.relay_unit).get("active"):
        raise SoakRuntimeError("RTMP relay is not active")
    if _service_state(config.obs_unit).get("active"):
        raise SoakRuntimeError("OBS must be inactive during direct-stream soak")
    if _publisher_count() != 1:
        raise SoakRuntimeError("expected exactly one direct-stream publisher process")


def _run(config: SoakConfig) -> int:
    _validate_runtime(config)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    lock_stream = (config.state_dir / "monitor.lock").open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SoakRuntimeError("soak monitor is already running") from exc
        run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        run_dir = config.state_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        samples_file = run_dir / "samples.jsonl"
        summary_file = run_dir / "summary.json"
        status_file = config.state_dir / "status.json"
        started_at = time.time()
        next_sample_at = started_at
        stopping = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        previous_term = signal.signal(signal.SIGTERM, request_stop)
        previous_int = signal.signal(signal.SIGINT, request_stop)
        samples: list[dict[str, object]] = []
        try:
            while True:
                sampled_at = time.time()
                sample = collect_sample(config, sampled_at)
                samples.append(sample)
                with samples_file.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
                elapsed = sampled_at - started_at
                _atomic_json(
                    status_file,
                    {
                        "state": "running",
                        "running": True,
                        "pid": os.getpid(),
                        "run_id": run_id,
                        "started_at": started_at,
                        "updated_at": sampled_at,
                        "elapsed_sec": round(elapsed, 3),
                        "samples_file": str(samples_file),
                        "latest": sample,
                        "config": config.public_dict(),
                    },
                )
                if stopping or elapsed >= config.duration_sec:
                    break
                next_sample_at += config.interval_sec
                now = time.time()
                if next_sample_at <= now:
                    missed = math.floor((now - next_sample_at) / config.interval_sec) + 1
                    next_sample_at += missed * config.interval_sec
                time.sleep(
                    min(
                        max(0.0, next_sample_at - now),
                        max(0.0, started_at + config.duration_sec - now),
                    )
                )
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)

        summary = summarize_samples(samples, config.duration_sec)
        _atomic_json(summary_file, summary)
        final_state = "stopped" if stopping else ("passed" if summary["ok"] else "failed")
        _atomic_json(
            status_file,
            {
                "state": final_state,
                "running": False,
                "pid": os.getpid(),
                "run_id": run_id,
                "started_at": started_at,
                "ended_at": time.time(),
                "samples_file": str(samples_file),
                "summary_file": str(summary_file),
                "summary": summary,
                "config": config.public_dict(),
            },
        )
        return 0 if stopping or summary["ok"] else 1
    finally:
        lock_stream.close()


def _read_monitor_status(config: SoakConfig) -> dict[str, object]:
    status = _read_json(config.state_dir / "status.json")
    if not status:
        return {"state": "not_started", "running": False}
    status["running"] = bool(status.get("running")) and _pid_alive(status.get("pid"))
    return status


def _start(config: SoakConfig, argv: Sequence[str]) -> int:
    current = _read_monitor_status(config)
    if current.get("running"):
        raise SoakRuntimeError(f"soak monitor is already running (PID={current.get('pid')})")
    _validate_runtime(config)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.state_dir / "monitor.log"
    command = [sys.executable, str(Path(__file__).resolve()), "run", *argv]
    with log_path.open("a", encoding="utf-8") as log_stream:
        child = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            start_new_session=True,
        )
    print(json.dumps({"started": True, "pid": child.pid, "log_file": str(log_path)}, sort_keys=True))
    return 0


def _stop(config: SoakConfig) -> int:
    status = _read_monitor_status(config)
    if not status.get("running"):
        return 0
    pid = int(status["pid"])
    command_path = Path(f"/proc/{pid}/cmdline")
    try:
        command = command_path.read_bytes().replace(b"\x00", b" ")
    except OSError as exc:
        raise SoakRuntimeError("cannot verify soak monitor process identity") from exc
    if b"direct_soak.py run" not in command:
        raise SoakRuntimeError("refusing to stop a PID not owned by direct_soak.py run")
    os.kill(pid, signal.SIGTERM)
    return 0


def _duration_arg(raw: str) -> int:
    try:
        return _strict_int(raw, "duration", 10, 172800)
    except SoakConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _interval_arg(raw: str) -> int:
    try:
        return _strict_int(raw, "interval", 1, 300)
    except SoakConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Soren direct-stream soak monitor")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("config")
    for action in ("run", "start"):
        action_parser = subparsers.add_parser(action)
        action_parser.add_argument("--duration", type=_duration_arg)
        action_parser.add_argument("--interval", type=_interval_arg)
    subparsers.add_parser("status")
    subparsers.add_parser("stop")
    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--samples", type=Path)
    summary_parser.add_argument("--expected-duration", type=_duration_arg)
    args = parser.parse_args(argv)

    duration = getattr(args, "duration", None)
    interval = getattr(args, "interval", None)
    config = load_config(duration_override=duration, interval_override=interval)
    if args.action == "config":
        print(json.dumps(config.public_dict(), sort_keys=True))
        return 0
    if args.action == "run":
        return _run(config)
    if args.action == "start":
        forwarded: list[str] = []
        if duration is not None:
            forwarded.extend(("--duration", str(duration)))
        if interval is not None:
            forwarded.extend(("--interval", str(interval)))
        return _start(config, forwarded)
    if args.action == "status":
        print(json.dumps(_read_monitor_status(config), sort_keys=True))
        return 0
    if args.action == "stop":
        return _stop(config)
    if args.action == "summary":
        sample_path = args.samples
        if sample_path is None:
            status = _read_monitor_status(config)
            raw = status.get("samples_file")
            if not raw:
                raise SoakRuntimeError("no soak samples are available")
            sample_path = Path(str(raw))
        rows = []
        for line in sample_path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        expected = args.expected_duration or config.duration_sec
        print(json.dumps(summarize_samples(rows, expected), sort_keys=True))
        return 0
    raise AssertionError(f"unhandled action: {args.action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SoakConfigError, SoakRuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"direct_soak: {exc}", file=sys.stderr)
        raise SystemExit(2)
