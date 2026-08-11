#!/usr/bin/env python3
"""A/V sync acceptance probe for the live loopback FFmpeg stream."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Mapping, Sequence
from urllib.parse import urlsplit
import wave


REPO_ROOT = Path(__file__).resolve().parents[1]


class AVSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class AVSyncConfig:
    backend: str
    ffmpeg_bin: str
    pulse_sink: str
    local_rtmp_url: str
    html_file: Path
    state_file: Path
    output_root: Path
    tone_hz: int
    event_count: int
    event_interval_sec: int
    event_duration_ms: int
    lead_sec: int
    pulse_latency_ms: int
    max_abs_offset_ms: float
    max_drift_ms: float

    @property
    def capture_duration_sec(self) -> int:
        return self.lead_sec + (self.event_count - 1) * self.event_interval_sec + 5

    def public_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "ffmpeg_bin": self.ffmpeg_bin,
            "pulse_sink": self.pulse_sink,
            "html_file": str(self.html_file),
            "state_file": str(self.state_file),
            "output_root": str(self.output_root),
            "tone_hz": self.tone_hz,
            "event_count": self.event_count,
            "event_interval_sec": self.event_interval_sec,
            "event_duration_ms": self.event_duration_ms,
            "lead_sec": self.lead_sec,
            "pulse_latency_ms": self.pulse_latency_ms,
            "capture_duration_sec": self.capture_duration_sec,
            "max_abs_offset_ms": self.max_abs_offset_ms,
            "max_drift_ms": self.max_drift_ms,
            "capture_source": "loopback_rtmp",
        }


def _path(raw: object, fallback: str) -> Path:
    text = str(raw or fallback).strip()
    path = Path(text)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def _integer(source: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(source.get(key, default)).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise AVSyncError(f"{key} must be an integer")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise AVSyncError(f"{key} must be between {minimum} and {maximum}")
    return value


def _number(source: Mapping[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    raw = str(source.get(key, default)).strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
        raise AVSyncError(f"{key} must be a number")
    value = float(raw)
    if not minimum <= value <= maximum:
        raise AVSyncError(f"{key} must be between {minimum:g} and {maximum:g}")
    return value


def _loopback_rtmp(raw: object) -> str:
    value = str(raw or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "rtmp"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/soren/")
    ):
        raise AVSyncError("A/V probe requires the credential-free loopback RTMP URL")
    return value


def load_config(env: Mapping[str, str] | None = None) -> AVSyncConfig:
    source = os.environ if env is None else env
    backend = str(source.get("SOREN_STREAM_BACKEND", "obs")).strip().lower()
    if backend not in {"obs", "ffmpeg"}:
        raise AVSyncError("SOREN_STREAM_BACKEND must be obs or ffmpeg")
    ffmpeg_bin = str(source.get("SOREN_DIRECT_STREAM_FFMPEG_BIN", "ffmpeg")).strip()
    if not ffmpeg_bin or any(character in ffmpeg_bin for character in "\r\n\x00"):
        raise AVSyncError("SOREN_DIRECT_STREAM_FFMPEG_BIN is invalid")
    pulse_source = str(
        source.get("SOREN_DIRECT_STREAM_PULSE_SOURCE", "soren_null.monitor")
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:@+-]+[.]monitor", pulse_source):
        raise AVSyncError("A/V probe Pulse source must be a monitor source")
    return AVSyncConfig(
        backend=backend,
        ffmpeg_bin=ffmpeg_bin,
        pulse_sink=pulse_source.removesuffix(".monitor"),
        local_rtmp_url=_loopback_rtmp(
            source.get("SOREN_DIRECT_STREAM_LOCAL_URL", "rtmp://127.0.0.1:1935/soren/live")
        ),
        html_file=_path(
            source.get("SOREN_DIRECT_AV_SYNC_OVERLAY_HTML_FILE"),
            "tmp/state/direct_av_sync_probe.html",
        ),
        state_file=_path(
            source.get("SOREN_DIRECT_AV_SYNC_STATE_FILE"),
            "tmp/state/direct_av_sync_probe.json",
        ),
        output_root=_path(
            source.get("SOREN_DIRECT_AV_SYNC_OUTPUT_DIR"),
            "tmp/direct_av_sync",
        ),
        tone_hz=_integer(source, "SOREN_DIRECT_AV_SYNC_TONE_HZ", 17000, 12000, 19000),
        event_count=_integer(source, "SOREN_DIRECT_AV_SYNC_EVENT_COUNT", 6, 4, 12),
        event_interval_sec=_integer(source, "SOREN_DIRECT_AV_SYNC_INTERVAL_SEC", 2, 1, 5),
        event_duration_ms=_integer(source, "SOREN_DIRECT_AV_SYNC_EVENT_MS", 180, 80, 400),
        lead_sec=_integer(source, "SOREN_DIRECT_AV_SYNC_LEAD_SEC", 5, 4, 10),
        pulse_latency_ms=_integer(source, "SOREN_GAME_AUDIO_PULSE_LATENCY_MS", 100, 50, 2000),
        max_abs_offset_ms=_number(source, "SOREN_DIRECT_AV_SYNC_MAX_OFFSET_MS", 100, 20, 500),
        max_drift_ms=_number(source, "SOREN_DIRECT_AV_SYNC_MAX_DRIFT_MS", 50, 10, 300),
    )


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def render_probe_html(events_epoch_ms: Sequence[int], duration_ms: int) -> str:
    events = json.dumps([int(value) for value in events_epoch_ms], separators=(",", ":"))
    return f"""<!doctype html>
<meta charset=\"utf-8\">
<style>
html,body,#flash{{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}}
#flash{{background:transparent}}
</style>
<div id=\"flash\"></div>
<script>
const events={events};
const duration={int(duration_ms)};
const flash=document.getElementById('flash');
const lastEventEnd=events.length?Math.max(...events)+duration:Date.now();
function draw(){{
  const now=Date.now();
  const active=events.some((at)=>now>=at&&now<at+duration);
  flash.style.background=active?'#fff':'transparent';
  requestAnimationFrame(draw);
}}
draw();
// Keep the absolute schedule stable while the probe is active, then return to
// the server route so a later run cannot remain stuck on this expired page.
setTimeout(()=>location.reload(),Math.max(250,lastEventEnd-Date.now()+5000));
</script>
"""


def generate_tone_wav(
    path: Path,
    *,
    frequency_hz: int,
    duration_ms: int,
    sample_rate: int = 48000,
    amplitude: float = 0.45,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_count = round(sample_rate * duration_ms / 1000)
    fade_samples = max(1, round(sample_rate * 0.005))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(sample_count):
            envelope = min(1.0, index / fade_samples, (sample_count - 1 - index) / fade_samples)
            value = amplitude * max(0.0, envelope) * math.sin(
                2 * math.pi * frequency_hz * index / sample_rate
            )
            frames.extend(struct.pack("<h", round(value * 32767)))
        output.writeframes(frames)


def parse_video_metadata(text: str, *, threshold: float = 220.0) -> list[float]:
    points: list[tuple[float, float]] = []
    current_time: float | None = None
    for line in text.splitlines():
        time_match = re.search(r"\bpts_time:([-+0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        y_match = re.search(r"lavfi[.]signalstats[.]YAVG=([-+0-9.]+)", line)
        if y_match and current_time is not None:
            points.append((current_time, float(y_match.group(1))))
    return _cluster_starts(points, threshold=threshold, minimum_duration=0.05, maximum_duration=0.5)


def _cluster_starts(
    points: Sequence[tuple[float, float]],
    *,
    threshold: float,
    minimum_duration: float,
    maximum_duration: float,
) -> list[float]:
    clusters: list[tuple[float, float]] = []
    start: float | None = None
    previous: float | None = None
    for at, value in points:
        active = value >= threshold
        if active and start is None:
            start = at
        if not active and start is not None:
            clusters.append((start, previous if previous is not None else at))
            start = None
        previous = at
    if start is not None and previous is not None:
        clusters.append((start, previous))
    return [start for start, end in clusters if minimum_duration <= end - start <= maximum_duration]


def detect_tone_events(
    pcm: bytes,
    *,
    sample_rate: int,
    frequency_hz: int,
    amplitude_threshold: float = 0.035,
) -> list[float]:
    samples = [value[0] / 32768.0 for value in struct.iter_unpack("<h", pcm)]
    window_size = max(64, round(sample_rate * 0.01))
    cosines = [math.cos(2 * math.pi * frequency_hz * index / sample_rate) for index in range(window_size)]
    sines = [math.sin(2 * math.pi * frequency_hz * index / sample_rate) for index in range(window_size)]
    window = [0.5 - 0.5 * math.cos(2 * math.pi * index / (window_size - 1)) for index in range(window_size)]
    normalization = sum(window) / 2
    points: list[tuple[float, float]] = []
    for start in range(0, len(samples) - window_size + 1, window_size):
        real = 0.0
        imaginary = 0.0
        for index, sample in enumerate(samples[start : start + window_size]):
            weighted = sample * window[index]
            real += weighted * cosines[index]
            imaginary -= weighted * sines[index]
        amplitude = math.hypot(real, imaginary) / normalization if normalization else 0.0
        points.append((start / sample_rate, amplitude))
    return _cluster_starts(
        points,
        threshold=amplitude_threshold,
        minimum_duration=0.05,
        maximum_duration=0.5,
    )


def match_events(
    video_events: Sequence[float],
    audio_events: Sequence[float],
    *,
    expected_count: int,
    max_abs_offset_ms: float,
    max_drift_ms: float,
) -> dict[str, object]:
    minimum_pairs = max(4, expected_count - 1)
    best: tuple[tuple[int, float], list[float]] | None = None
    for video_start in range(max(1, len(video_events) - minimum_pairs + 1)):
        for audio_start in range(max(1, len(audio_events) - minimum_pairs + 1)):
            count = min(
                expected_count,
                len(video_events) - video_start,
                len(audio_events) - audio_start,
            )
            if count < minimum_pairs:
                continue
            offsets = [
                (audio_events[audio_start + index] - video_events[video_start + index]) * 1000
                for index in range(count)
            ]
            spread = max(offsets) - min(offsets)
            score = (-count, max(abs(value) for value in offsets) + spread)
            if best is None or score < best[0]:
                best = (score, offsets)
    if best is None:
        return {
            "ok": False,
            "pair_count": 0,
            "offsets_ms": [],
            "max_abs_offset_ms": None,
            "drift_ms": None,
            "jitter_spread_ms": None,
            "reason": "insufficient_events",
        }
    offsets = best[1]
    maximum = max(abs(value) for value in offsets)
    jitter_spread = max(offsets) - min(offsets)
    drift = abs(offsets[-1] - offsets[0])
    return {
        "ok": maximum <= max_abs_offset_ms and drift <= max_drift_ms,
        "pair_count": len(offsets),
        "offsets_ms": [round(value, 3) for value in offsets],
        "mean_offset_ms": round(sum(offsets) / len(offsets), 3),
        "max_abs_offset_ms": round(maximum, 3),
        "drift_ms": round(drift, 3),
        "jitter_spread_ms": round(jitter_spread, 3),
        "limits": {
            "max_abs_offset_ms": max_abs_offset_ms,
            "max_drift_ms": max_drift_ms,
        },
    }


def _run_command(command: Sequence[str], *, timeout: int, stdout=None, stderr=None, env=None) -> None:
    try:
        result = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AVSyncError(f"command failed: {command[0]}") from exc
    if result.returncode != 0:
        raise AVSyncError(f"command failed: {command[0]}")


def analyze_capture(config: AVSyncConfig, capture: Path, run_dir: Path) -> dict[str, object]:
    metadata_file = run_dir / "video-metadata.txt"
    pcm_file = run_dir / "audio-s16le.pcm"
    _run_command(
        [
            config.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(capture),
            "-an",
            "-vf",
            f"crop=128:128:0:0,signalstats,metadata=print:file={metadata_file}",
            "-f",
            "null",
            "-",
        ],
        timeout=60,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pcm_file.open("wb") as output:
        _run_command(
            [
                config.ffmpeg_bin,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(capture),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-f",
                "s16le",
                "-",
            ],
            timeout=60,
            stdout=output,
            stderr=subprocess.DEVNULL,
        )
    video_events = parse_video_metadata(metadata_file.read_text(encoding="utf-8", errors="replace"))
    audio_events = detect_tone_events(
        pcm_file.read_bytes(),
        sample_rate=48000,
        frequency_hz=config.tone_hz,
    )
    matched = match_events(
        video_events,
        audio_events,
        expected_count=config.event_count,
        max_abs_offset_ms=config.max_abs_offset_ms,
        max_drift_ms=config.max_drift_ms,
    )
    return {
        **matched,
        "video_event_count": len(video_events),
        "audio_event_count": len(audio_events),
        "video_events_sec": [round(value, 4) for value in video_events],
        "audio_events_sec": [round(value, 4) for value in audio_events],
    }


def _validate_live(config: AVSyncConfig) -> None:
    if sys.platform != "linux":
        raise AVSyncError("A/V sync probe is Linux-only")
    if config.backend != "ffmpeg":
        raise AVSyncError("A/V sync probe requires SOREN_STREAM_BACKEND=ffmpeg")
    for command in (config.ffmpeg_bin, "paplay", "pactl", "ss"):
        executable = shutil.which(command)
        if executable is None and not (Path(command).is_file() and os.access(command, os.X_OK)):
            raise AVSyncError(f"required command not found: {command}")
    try:
        direct = json.loads(
            subprocess.check_output(
                [str(REPO_ROOT / "direct_stream.sh"), "status"],
                cwd=REPO_ROOT,
                text=True,
                timeout=5,
            )
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise AVSyncError("cannot read direct stream status") from exc
    if direct.get("running") is not True or direct.get("mode") != "live":
        raise AVSyncError("live direct stream is not running")
    from direct_soak import _publisher_count, _relay_publisher_connection_count

    if _publisher_count() != 1 or _relay_publisher_connection_count() != 1:
        raise AVSyncError("A/V probe requires exactly one publisher and one relay input")


def run_probe(config: AVSyncConfig) -> dict[str, object]:
    _validate_live(config)
    run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    run_dir = config.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    capture = run_dir / "capture.flv"
    tone_file = run_dir / "probe-tone.wav"
    capture_log = run_dir / "capture.log"
    generate_tone_wav(
        tone_file,
        frequency_hz=config.tone_hz,
        duration_ms=config.event_duration_ms,
    )
    capture_command = [
        config.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        config.local_rtmp_url,
        "-t",
        str(config.capture_duration_sec),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        str(capture),
    ]
    with capture_log.open("wb") as log:
        capture_process = subprocess.Popen(
            capture_command,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
        )
    try:
        time.sleep(1)
        if capture_process.poll() is not None:
            raise AVSyncError("loopback stream capture exited before probe start")
        first_event = time.time() + config.lead_sec
        events = [
            first_event + index * config.event_interval_sec for index in range(config.event_count)
        ]
        _atomic_text(
            config.html_file,
            render_probe_html([round(value * 1000) for value in events], config.event_duration_ms),
        )
        audio_env = os.environ.copy()
        audio_env["PULSE_LATENCY_MSEC"] = str(config.pulse_latency_ms)
        for event_at in events:
            time.sleep(max(0.0, event_at - time.time()))
            _run_command(
                ["paplay", f"--device={config.pulse_sink}", str(tone_file)],
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=audio_env,
            )
        try:
            capture_process.wait(timeout=config.capture_duration_sec + 10)
        except subprocess.TimeoutExpired as exc:
            capture_process.send_signal(signal.SIGINT)
            raise AVSyncError("loopback stream capture timed out") from exc
        if capture_process.returncode != 0:
            raise AVSyncError("loopback stream capture failed")
    finally:
        try:
            config.html_file.unlink()
        except FileNotFoundError:
            pass
        if capture_process.poll() is None:
            capture_process.send_signal(signal.SIGINT)
            try:
                capture_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                capture_process.kill()

    result = analyze_capture(config, capture, run_dir)
    payload = {
        "state": "passed" if result["ok"] else "failed",
        "tested_at": int(time.time()),
        "run_id": run_id,
        "capture_file": str(capture),
        "result": result,
        "config": config.public_dict(),
    }
    _atomic_json(run_dir / "summary.json", payload)
    _atomic_json(config.state_file, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Soren direct-stream A/V sync probe")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("config")
    subparsers.add_parser("run")
    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("capture", type=Path)
    analyze.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    config = load_config()
    if args.action == "config":
        print(json.dumps(config.public_dict(), sort_keys=True))
        return 0
    if args.action == "run":
        result = run_probe(config)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["state"] == "passed" else 1
    if args.action == "analyze":
        result = analyze_capture(config, args.capture.resolve(), args.output_dir.resolve())
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    raise AssertionError("unhandled action")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AVSyncError as exc:
        print(f"direct_av_sync: {exc}", file=sys.stderr)
        raise SystemExit(2)
