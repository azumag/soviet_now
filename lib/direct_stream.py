#!/usr/bin/env python3
"""Linux FFmpeg direct-stream runner.

The live FFmpeg process is deliberately restricted to a loopback RTMP URL.  A
separate, privileged relay owns destination credentials so they never appear in
this process' argv, logs, or status JSON.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Mapping, Sequence
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]

# Public Twitch web client ID used by the anonymous liveness probe.  It is not
# a credential: no token, OAuth, or channel secret is sent or stored.
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
RECONNECT_STATE_FILE = "reconnect.json"


class ConfigError(ValueError):
    """Raised before any runtime mutation when direct-stream config is invalid."""


class RuntimeCheckError(RuntimeError):
    """Raised before FFmpeg starts when a host prerequisite is missing."""


class AlreadyRunningError(RuntimeError):
    """Raised when the single-instance lock is already held."""


@dataclass(frozen=True)
class ReconnectConfig:
    """Viewer-side reconnect policy for the live FFmpeg publish path.

    The local relay/FFmpeg pipeline can stay green even after Twitch ends the
    ingest session (observed after ~48h of continuous publishing).  OBS
    reconnects on that cut; this policy restores the same behaviour by probing
    the public Twitch channel state and restarting the publish on a confirmed
    offline streak.
    """

    enabled: bool
    twitch_channel: str
    probe_interval_sec: int
    offline_threshold: int
    backoff_sec: int
    max_consecutive_restarts: int
    reload_relay: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "twitch_channel": self.twitch_channel,
            "probe_interval_sec": self.probe_interval_sec,
            "offline_threshold": self.offline_threshold,
            "backoff_sec": self.backoff_sec,
            "max_consecutive_restarts": self.max_consecutive_restarts,
            "reload_relay": self.reload_relay,
        }


@dataclass(frozen=True)
class RunOutcome:
    """Result of one FFmpeg publish run."""

    exit_code: int
    state: str
    started_at: int
    reconnect_reason: str | None = None
    ffmpeg_exit_code: int | None = None


@dataclass(frozen=True)
class DirectStreamConfig:
    backend: str
    display: str
    width: int
    height: int
    fps: int
    video_kbps: int
    audio_kbps: int
    audio_delay_ms: int
    pulse_source: str
    pulse_sample_rate: int
    pulse_channels: int
    local_rtmp_url: str
    ffmpeg_bin: str
    closed_captions_enabled: bool
    closed_captions_socket: Path
    state_dir: Path
    log_file: Path

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"

    def public_dict(self) -> dict[str, object]:
        # Do not include any output URL, even though validation restricts it to
        # loopback.  Status files remain safe if that rule changes later.
        data = asdict(self)
        data.pop("local_rtmp_url", None)
        data["closed_captions_socket"] = str(self.closed_captions_socket)
        data["state_dir"] = str(self.state_dir)
        data["log_file"] = str(self.log_file)
        return data


def _strict_int(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(env.get(key, default)).strip()
    if not re.fullmatch(r"[0-9]+", raw):
        raise ConfigError(f"{key} must be an integer")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    return value


def _strict_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, "1" if default else "0")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean")


def load_reconnect_config(env: Mapping[str, str] | None = None) -> ReconnectConfig:
    source = os.environ if env is None else env
    channel = str(
        source.get("SOREN_DIRECT_STREAM_TWITCH_CHANNEL")
        or source.get("TWITCH_CHANNEL")
        or ""
    ).strip()
    if channel and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", channel):
        raise ConfigError("SOREN_DIRECT_STREAM_TWITCH_CHANNEL is invalid")
    return ReconnectConfig(
        enabled=_strict_bool(source, "SOREN_DIRECT_STREAM_RECONNECT_ENABLED", True),
        twitch_channel=channel,
        probe_interval_sec=_strict_int(
            source, "SOREN_DIRECT_STREAM_RECONNECT_PROBE_SEC", 60, 10, 600
        ),
        offline_threshold=_strict_int(
            source, "SOREN_DIRECT_STREAM_RECONNECT_OFFLINE_THRESHOLD", 2, 1, 10
        ),
        backoff_sec=_strict_int(
            source, "SOREN_DIRECT_STREAM_RECONNECT_BACKOFF_SEC", 10, 1, 300
        ),
        max_consecutive_restarts=_strict_int(
            source, "SOREN_DIRECT_STREAM_RECONNECT_MAX_CONSECUTIVE", 10, 1, 100
        ),
        reload_relay=_strict_bool(source, "SOREN_DIRECT_STREAM_RECONNECT_RELOAD_RELAY", True),
    )


def twitch_channel_is_live(channel: str, timeout: int = 10) -> bool | None:
    """Probe the public Twitch channel state without any credentials.

    Returns True when the channel is publishing, False when it is offline, and
    None when the probe could not be completed (network/parse error).  None is
    deliberately treated as "unknown" so a transient probe failure never turns
    into an offline streak.
    """
    if not channel:
        return None
    # Channel names are validated to [A-Za-z0-9_] by load_reconnect_config, so
    # direct interpolation is safe here; json.dumps would escape the quotes.
    payload = {
        "query": f"query {{ user(login: \"{channel}\") {{ stream {{ id }} }} }}"
    }
    request = urllib.request.Request(
        TWITCH_GQL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Client-ID": TWITCH_WEB_CLIENT_ID,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError):
        return None
    try:
        stream = body["data"]["user"]["stream"]
    except (KeyError, TypeError):
        return None
    return stream is not None


def _resolve_repo_path(raw: str, default: str) -> Path:
    path = Path(raw or default).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def _validate_loopback_rtmp_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ConfigError("SOREN_DIRECT_STREAM_LOCAL_URL must be a valid loopback RTMP URL") from exc
    if parsed.scheme != "rtmp":
        raise ConfigError("SOREN_DIRECT_STREAM_LOCAL_URL must use rtmp://")
    if parsed.username or parsed.password:
        raise ConfigError("SOREN_DIRECT_STREAM_LOCAL_URL must not contain credentials")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigError("SOREN_DIRECT_STREAM_LOCAL_URL must target loopback")
    if not parsed.path or parsed.path == "/":
        raise ConfigError("SOREN_DIRECT_STREAM_LOCAL_URL must include an application/stream path")
    if parsed.query or parsed.fragment:
        raise ConfigError("SOREN_DIRECT_STREAM_LOCAL_URL must not include query or fragment data")
    return raw


def _validate_caption_socket_path(raw: str) -> Path:
    if not raw or any(character in raw for character in "\r\n\x00"):
        raise ConfigError("DOCICH_CC_SOCKET is invalid")
    if not re.fullmatch(r"/[A-Za-z0-9_./@+-]+", raw):
        raise ConfigError("DOCICH_CC_SOCKET contains unsupported characters")
    path = Path(raw)
    if (
        not path.is_absolute()
        or path.name in {"", ".", ".."}
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ConfigError("DOCICH_CC_SOCKET must be an absolute Unix socket path")
    if len(os.fsencode(path)) >= 104:
        raise ConfigError("DOCICH_CC_SOCKET must be shorter than 104 bytes")
    return path


def load_config(env: Mapping[str, str] | None = None) -> DirectStreamConfig:
    source = os.environ if env is None else env
    backend = str(source.get("SOREN_STREAM_BACKEND", "obs")).strip().lower()
    if backend not in {"obs", "ffmpeg"}:
        raise ConfigError("SOREN_STREAM_BACKEND must be obs or ffmpeg")

    size_raw = str(source.get("SOREN_DIRECT_STREAM_SIZE", "1280x720")).strip().lower()
    match = re.fullmatch(r"([0-9]{2,5})x([0-9]{2,5})", size_raw)
    if not match:
        raise ConfigError("SOREN_DIRECT_STREAM_SIZE must use WIDTHxHEIGHT")
    width, height = (int(match.group(1)), int(match.group(2)))
    if width < 320 or width > 3840 or height < 180 or height > 2160:
        raise ConfigError("SOREN_DIRECT_STREAM_SIZE is outside the supported range")
    if width % 2 or height % 2:
        raise ConfigError("SOREN_DIRECT_STREAM_SIZE dimensions must be even")

    display = str(source.get("SOREN_DIRECT_STREAM_DISPLAY", source.get("DISPLAY", ":99.0"))).strip()
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display):
        raise ConfigError("SOREN_DIRECT_STREAM_DISPLAY must look like :99.0")

    pulse_source = str(
        source.get("SOREN_DIRECT_STREAM_PULSE_SOURCE", "soren_null.monitor")
    ).strip()
    if not pulse_source or not re.fullmatch(r"[A-Za-z0-9_.:@+-]+", pulse_source):
        raise ConfigError("SOREN_DIRECT_STREAM_PULSE_SOURCE contains unsupported characters")

    local_url = _validate_loopback_rtmp_url(
        str(source.get("SOREN_DIRECT_STREAM_LOCAL_URL", "rtmp://127.0.0.1:1935/soren/live")).strip()
    )

    ffmpeg_bin = str(source.get("SOREN_DIRECT_STREAM_FFMPEG_BIN", "ffmpeg")).strip()
    if not ffmpeg_bin or any(ch in ffmpeg_bin for ch in "\r\n\x00"):
        raise ConfigError("SOREN_DIRECT_STREAM_FFMPEG_BIN is invalid")

    runtime_dir = str(
        source.get("XDG_RUNTIME_DIR") or f"/run/user/{os.geteuid()}"
    ).rstrip("/")

    return DirectStreamConfig(
        backend=backend,
        display=display,
        width=width,
        height=height,
        fps=_strict_int(source, "SOREN_DIRECT_STREAM_FPS", 30, 1, 60),
        video_kbps=_strict_int(source, "SOREN_DIRECT_STREAM_VIDEO_KBPS", 4500, 500, 6000),
        audio_kbps=_strict_int(source, "SOREN_DIRECT_STREAM_AUDIO_KBPS", 160, 64, 320),
        audio_delay_ms=_strict_int(source, "SOREN_DIRECT_STREAM_AUDIO_DELAY_MS", 0, 0, 2000),
        pulse_source=pulse_source,
        pulse_sample_rate=_strict_int(source, "SOREN_DIRECT_STREAM_AUDIO_HZ", 48000, 8000, 192000),
        pulse_channels=_strict_int(source, "SOREN_DIRECT_STREAM_AUDIO_CHANNELS", 2, 1, 2),
        local_rtmp_url=local_url,
        ffmpeg_bin=ffmpeg_bin,
        closed_captions_enabled=_strict_bool(source, "DOCICH_CC_ENABLED", False),
        closed_captions_socket=_validate_caption_socket_path(
            str(
                source.get(
                    "DOCICH_CC_SOCKET",
                    f"{runtime_dir}/docich/ffmpeg-cc.sock",
                )
            ).strip()
        ),
        state_dir=_resolve_repo_path(str(source.get("SOREN_DIRECT_STREAM_STATE_DIR", "")), "tmp/state/direct_stream"),
        log_file=_resolve_repo_path(str(source.get("SOREN_DIRECT_STREAM_LOG_FILE", "")), "logs/direct_stream.log"),
    )


def build_ffmpeg_command(
    config: DirectStreamConfig,
    *,
    mode: str,
    output_path: Path | None = None,
    duration_sec: int | None = None,
    captions_active: bool | None = None,
) -> list[str]:
    if mode not in {"live", "record"}:
        raise ConfigError("mode must be live or record")
    if mode == "record" and output_path is None:
        raise ConfigError("record mode requires an output path")
    if duration_sec is not None and not 1 <= duration_sec <= 86400:
        raise ConfigError("record duration must be between 1 and 86400 seconds")

    fps = str(config.fps)
    gop = str(config.fps * 2)
    if captions_active is None:
        captions_active = config.closed_captions_enabled
    command = [
        config.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-stats_period",
        "1",
        "-thread_queue_size",
        "1024",
        "-f",
        "x11grab",
        "-draw_mouse",
        "0",
        "-framerate",
        fps,
        "-video_size",
        config.size,
        "-i",
        f"{config.display}+0,0",
        "-thread_queue_size",
        "2048",
        "-f",
        "pulse",
        "-sample_rate",
        str(config.pulse_sample_rate),
        "-channels",
        str(config.pulse_channels),
        "-i",
        config.pulse_source,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-r",
        fps,
        "-fps_mode",
        "cfr",
        "-g",
        gop,
        "-keyint_min",
        gop,
        "-sc_threshold",
        "0",
        "-b:v",
        f"{config.video_kbps}k",
        "-minrate",
        f"{config.video_kbps}k",
        "-maxrate",
        f"{config.video_kbps}k",
        "-bufsize",
        f"{config.video_kbps * 2}k",
        "-af",
        f"adelay={config.audio_delay_ms}:all=1,aresample=async=1:first_pts=0",
        "-c:a",
        "aac",
        "-ar",
        str(config.pulse_sample_rate),
        "-ac",
        str(config.pulse_channels),
        "-b:a",
        f"{config.audio_kbps}k",
        "-progress",
        "pipe:1",
    ]
    if captions_active:
        # The socket path is restricted to a filter-safe character subset in
        # load_config, so it cannot inject another filter or option here.
        video_output_index = command.index("-c:v")
        command[video_output_index:video_output_index] = [
            "-vf",
            f"docichcc=socket={config.closed_captions_socket}",
        ]
        command[command.index("-preset"):command.index("-preset")] = ["-a53cc", "1"]
    if duration_sec is not None:
        command.extend(["-t", str(duration_sec)])
    if mode == "live":
        command.extend(["-flvflags", "no_duration_filesize", "-f", "flv", config.local_rtmp_url])
    else:
        command.extend(["-y", "-f", "matroska", str(output_path)])
    return command


def _checked(command: Sequence[str], *, env: Mapping[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            list(command),
            env=None if env is None else dict(env),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeCheckError(f"failed to run prerequisite check: {command[0]}") from exc
    if result.returncode != 0:
        raise RuntimeCheckError(f"prerequisite check failed: {command[0]}")
    return result.stdout + result.stderr


def validate_local_relay(config: DirectStreamConfig) -> None:
    parsed = urlsplit(config.local_rtmp_url)
    host = parsed.hostname
    port = parsed.port or 1935
    if host is None:
        raise RuntimeCheckError("loopback RTMP relay host is missing")
    try:
        connection = socket.create_connection((host, port), timeout=2)
    except OSError as exc:
        raise RuntimeCheckError("loopback RTMP relay is not reachable") from exc
    connection.close()


def caption_capabilities_available(config: DirectStreamConfig) -> bool:
    """Return whether this FFmpeg can inject and encode A/53 captions.

    Caption support is additive and deliberately fail-open.  A stock FFmpeg
    must never prevent the existing audio/video stream from starting.
    """
    if not config.closed_captions_enabled:
        return False
    try:
        filters = _checked([config.ffmpeg_bin, "-hide_banner", "-filters"])
        encoder_help = _checked(
            [config.ffmpeg_bin, "-hide_banner", "-h", "encoder=libx264"]
        )
    except RuntimeCheckError as exc:
        print(f"direct_stream: closed captions disabled ({exc})", file=sys.stderr)
        return False
    if not re.search(r"^\s*\.\S*\s+docichcc\s", filters, re.MULTILINE):
        print(
            "direct_stream: closed captions disabled (docichcc filter is unavailable)",
            file=sys.stderr,
        )
        return False
    if not re.search(r"(?:^|\s)-?a53cc(?:\s|$)", encoder_help):
        print(
            "direct_stream: closed captions disabled (libx264 a53cc is unavailable)",
            file=sys.stderr,
        )
        return False
    return True


def prepare_caption_runtime(config: DirectStreamConfig) -> bool:
    """Create a private socket directory, or disable captions without failing."""
    if not config.closed_captions_enabled:
        return False
    parent = config.closed_captions_socket.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError("socket parent is not a real directory")
        if metadata.st_uid != os.geteuid():
            raise OSError("socket parent is not owned by this user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            parent.chmod(0o700)
    except OSError as exc:
        print(
            f"direct_stream: closed captions disabled (socket directory: {exc})",
            file=sys.stderr,
        )
        return False
    return True


def validate_runtime(config: DirectStreamConfig, *, mode: str) -> bool:
    if sys.platform != "linux":
        raise RuntimeCheckError("FFmpeg direct streaming is Linux-only")
    if mode == "live" and config.backend != "ffmpeg":
        raise RuntimeCheckError("live direct streaming requires SOREN_STREAM_BACKEND=ffmpeg")
    if mode == "live":
        validate_local_relay(config)

    ffmpeg_path = shutil.which(config.ffmpeg_bin)
    if ffmpeg_path is None and not Path(config.ffmpeg_bin).is_file():
        raise RuntimeCheckError("FFmpeg executable was not found")
    devices = _checked([config.ffmpeg_bin, "-hide_banner", "-devices"])
    if not re.search(r"^\s*D\S*\s+x11grab\s", devices, re.MULTILINE):
        raise RuntimeCheckError("FFmpeg does not provide x11grab input")
    if not re.search(r"^\s*D\S*\s+pulse\s", devices, re.MULTILINE):
        raise RuntimeCheckError("FFmpeg does not provide PulseAudio input")
    encoders = _checked([config.ffmpeg_bin, "-hide_banner", "-encoders"])
    if not re.search(r"^\s*V\S*\s+libx264\s", encoders, re.MULTILINE):
        raise RuntimeCheckError("FFmpeg does not provide libx264")

    if shutil.which("xdpyinfo") is None:
        raise RuntimeCheckError("xdpyinfo was not found")
    display_info = _checked(["xdpyinfo", "-display", config.display])
    match = re.search(r"dimensions:\s*([0-9]+)x([0-9]+)", display_info)
    if not match:
        raise RuntimeCheckError("could not determine X11 display dimensions")
    display_width, display_height = int(match.group(1)), int(match.group(2))
    if display_width < config.width or display_height < config.height:
        raise RuntimeCheckError(
            f"X11 display is {display_width}x{display_height}, smaller than requested capture"
        )

    if shutil.which("pactl") is None:
        raise RuntimeCheckError("pactl was not found")
    sources = _checked(["pactl", "list", "short", "sources"])
    source_names = {
        fields[1]
        for line in sources.splitlines()
        if len(fields := line.split()) >= 2
    }
    if config.pulse_source not in source_names:
        raise RuntimeCheckError(f"PulseAudio source was not found: {config.pulse_source}")
    return caption_capabilities_available(config)


def parse_progress_lines(lines: Sequence[str]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    integer_keys = {"frame", "total_size", "out_time_us", "out_time_ms", "dup_frames", "drop_frames"}
    for raw in lines:
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in integer_keys:
            try:
                parsed[key] = int(float(value))
            except ValueError:
                parsed[key] = value
        elif key in {"fps", "speed"}:
            try:
                parsed[key] = float(value.removesuffix("x"))
            except ValueError:
                parsed[key] = value
        else:
            parsed[key] = value
    return parsed


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


def _reload_relay(config: DirectStreamConfig) -> bool:
    """Best-effort relay reload so nginx re-establishes its Twitch pushes."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "reload", "soren-rtmp-relay.service"],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"direct_stream: relay reload skipped ({exc})", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            f"direct_stream: relay reload failed rc={result.returncode} "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return True


def _record_reconnect(
    config: DirectStreamConfig,
    *,
    reason: str,
    consecutive: int,
    offline_streak: int,
    backoff_sec: int,
) -> None:
    payload = {
        "reason": reason,
        "consecutive_restarts": consecutive,
        "offline_streak": offline_streak,
        "backoff_sec": backoff_sec,
        "reconnected_at": int(time.time()),
    }
    _atomic_json(config.state_dir / RECONNECT_STATE_FILE, payload)
    print(
        f"direct_stream: reconnect reason={reason} consecutive={consecutive} "
        f"backoff={backoff_sec}s",
        file=sys.stderr,
    )


def _pid_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_status(config: DirectStreamConfig) -> dict[str, object]:
    state_file = config.state_dir / "status.json"
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"backend": "ffmpeg", "running": False, "state": "not_started"}
    if not isinstance(data, dict):
        return {"backend": "ffmpeg", "running": False, "state": "invalid_status"}
    data.pop("output_url", None)
    data["running"] = bool(data.get("running")) and (
        _pid_alive(data.get("pid")) or _pid_alive(data.get("ffmpeg_pid"))
    )
    return data


def classify_ffmpeg_exit(return_code: int, *, stopping: bool) -> tuple[int, str]:
    """Map an operator-requested stop to a successful runner outcome.

    FFmpeg normally reports 255 after receiving SIGINT. That raw value remains
    useful for diagnostics, but a requested stop is not a stream failure and
    must not trigger supervisor failure handling.
    """
    if stopping:
        return 0, "stopped"
    return return_code, "completed" if return_code == 0 else "failed"


def _start_reconnect_monitor(
    config: DirectStreamConfig,
    reconnect: ReconnectConfig,
    child: subprocess.Popen[str],
    monitor_state: dict[str, object],
) -> threading.Thread:
    """Probe the Twitch channel while FFmpeg runs and request a reconnect."""

    def monitor() -> None:
        while True:
            time.sleep(reconnect.probe_interval_sec)
            if monitor_state.get("stopping"):
                return
            if not _pid_alive(child.pid):
                return
            live = twitch_channel_is_live(reconnect.twitch_channel)
            if live is None:
                # Transient probe failure: never count toward an offline streak.
                continue
            monitor_state["last_live"] = live
            if live is True:
                monitor_state["offline_streak"] = 0
                continue
            streak = int(monitor_state.get("offline_streak", 0)) + 1
            monitor_state["offline_streak"] = streak
            print(
                f"direct_stream: Twitch offline streak={streak}/"
                f"{reconnect.offline_threshold}",
                file=sys.stderr,
            )
            if streak >= reconnect.offline_threshold:
                monitor_state["reconnect"] = True
                monitor_state["reconnect_reason"] = "twitch_offline"
                try:
                    child.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                return

    thread = threading.Thread(
        target=monitor,
        name="direct-stream-reconnect-monitor",
        daemon=True,
    )
    thread.start()
    return thread


def _run_ffmpeg_once(
    config: DirectStreamConfig,
    command: Sequence[str],
    *,
    mode: str,
    reconnect: ReconnectConfig | None,
    captions_active: bool = False,
) -> RunOutcome:
    """Run FFmpeg once and return the outcome of this single publish session."""
    started_at = int(time.time())
    with config.log_file.open("a", encoding="utf-8") as log_stream:
        child = subprocess.Popen(
            list(command),
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=log_stream,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        base_state: dict[str, object] = {
            "backend": "ffmpeg",
            "mode": mode,
            "running": True,
            "state": "running",
            "pid": os.getpid(),
            "ffmpeg_pid": child.pid,
            "started_at": started_at,
            "updated_at": started_at,
            "config": config.public_dict(),
            "closed_captions": {
                "requested": config.closed_captions_enabled,
                "active": captions_active,
            },
        }
        if reconnect is not None:
            base_state["reconnect"] = reconnect.public_dict()
        _atomic_json(config.state_dir / "status.json", base_state)

        stopping = False
        monitor_state: dict[str, object] = {
            "stopping": False,
            "reconnect": False,
            "reconnect_reason": None,
            "offline_streak": 0,
            "last_live": None,
        }

        def stop_child(signum: int, _frame: object) -> None:
            nonlocal stopping
            if stopping:
                return
            stopping = True
            monitor_state["stopping"] = True
            try:
                child.send_signal(signal.SIGINT if signum == signal.SIGTERM else signum)
            except ProcessLookupError:
                pass

        previous_term = signal.signal(signal.SIGTERM, stop_child)
        previous_int = signal.signal(signal.SIGINT, stop_child)
        monitor_thread: threading.Thread | None = None
        if (
            mode == "live"
            and reconnect is not None
            and reconnect.enabled
            and reconnect.twitch_channel
        ):
            monitor_thread = _start_reconnect_monitor(
                config, reconnect, child, monitor_state
            )
        batch: list[str] = []
        latest: dict[str, object] = {}
        try:
            assert child.stdout is not None
            for line in child.stdout:
                batch.append(line)
                if line.startswith("progress="):
                    latest.update(parse_progress_lines(batch))
                    batch.clear()
                    payload = {
                        **base_state,
                        **latest,
                        "updated_at": int(time.time()),
                    }
                    _atomic_json(config.state_dir / "status.json", payload)
            return_code = child.wait()
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
            if monitor_thread is not None:
                monitor_thread.join(timeout=5)

        if monitor_state.get("reconnect"):
            final_state = "reconnect"
            runner_exit_code = 0
        elif stopping:
            runner_exit_code, final_state = classify_ffmpeg_exit(
                return_code, stopping=True
            )
        else:
            runner_exit_code, final_state = classify_ffmpeg_exit(
                return_code, stopping=False
            )
        final_payload = {
            **base_state,
            **latest,
            "running": False,
            "state": final_state,
            "exit_code": runner_exit_code,
            "ended_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        if stopping or monitor_state.get("reconnect"):
            final_payload["ffmpeg_exit_code"] = return_code
        if monitor_state.get("reconnect_reason"):
            final_payload["reconnect_reason"] = monitor_state["reconnect_reason"]
        _atomic_json(config.state_dir / "status.json", final_payload)
        return RunOutcome(
            exit_code=runner_exit_code,
            state=final_state,
            started_at=started_at,
            reconnect_reason=(
                str(monitor_state["reconnect_reason"])
                if monitor_state.get("reconnect_reason")
                else None
            ),
            ffmpeg_exit_code=return_code,
        )


def _run_ffmpeg(
    config: DirectStreamConfig,
    *,
    mode: str,
    output_path: Path | None,
    duration_sec: int | None,
    reconnect: ReconnectConfig | None = None,
) -> int:
    captions_active = validate_runtime(config, mode=mode)
    captions_active = captions_active and prepare_caption_runtime(config)
    command = build_ffmpeg_command(
        config,
        mode=mode,
        output_path=output_path,
        duration_sec=duration_sec,
        captions_active=captions_active,
    )

    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = config.state_dir / "direct_stream.lock"
    lock_stream = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunningError("direct stream is already running") from exc

        if mode != "live" or reconnect is None or not reconnect.enabled:
            return _run_ffmpeg_once(
                config,
                command,
                mode=mode,
                reconnect=None,
                captions_active=captions_active,
            ).exit_code

        # Live supervise loop: reconnect on Twitch-side cuts and unexpected
        # FFmpeg exits, mirroring OBS's automatic reconnect behaviour.
        consecutive = 0
        stop_requested = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stop_requested
            stop_requested = True

        previous_term = signal.signal(signal.SIGTERM, request_stop)
        previous_int = signal.signal(signal.SIGINT, request_stop)
        try:
            while not stop_requested:
                outcome = _run_ffmpeg_once(
                    config,
                    command,
                    mode=mode,
                    reconnect=reconnect,
                    captions_active=captions_active,
                )
                if stop_requested or outcome.state == "stopped":
                    return 0
                if outcome.state == "completed":
                    return outcome.exit_code

                reason = outcome.reconnect_reason or "ffmpeg_exit"
                consecutive += 1
                if consecutive > reconnect.max_consecutive_restarts:
                    _record_reconnect(
                        config,
                        reason=reason,
                        consecutive=consecutive,
                        offline_streak=0,
                        backoff_sec=0,
                    )
                    raise RuntimeCheckError(
                        f"direct stream reconnect limit reached "
                        f"({reconnect.max_consecutive_restarts})"
                    )
                backoff = reconnect.backoff_sec * min(2 ** (consecutive - 1), 16)
                _record_reconnect(
                    config,
                    reason=reason,
                    consecutive=consecutive,
                    offline_streak=0,
                    backoff_sec=backoff,
                )
                if reconnect.reload_relay and reason == "twitch_offline":
                    _reload_relay(config)
                deadline = time.monotonic() + backoff
                while not stop_requested and time.monotonic() < deadline:
                    time.sleep(0.5)
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
    finally:
        lock_stream.close()


def _stop(config: DirectStreamConfig) -> int:
    status = read_status(config)
    pid = status.get("pid")
    if not _pid_alive(pid):
        return 0
    pid_int = int(pid)
    proc_cmdline = Path(f"/proc/{pid_int}/cmdline")
    try:
        command = proc_cmdline.read_bytes().replace(b"\x00", b" ")
    except OSError:
        return 0
    if b"direct_stream.py" not in command:
        raise RuntimeCheckError("refusing to stop a PID not owned by direct_stream.py")
    os.kill(pid_int, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not _pid_alive(pid_int):
            return 0
        time.sleep(0.2)
    raise RuntimeCheckError("direct stream did not stop within 15 seconds")


def _positive_duration(raw: str) -> int:
    if not re.fullmatch(r"[0-9]+", raw):
        raise argparse.ArgumentTypeError("duration must be an integer")
    value = int(raw)
    if not 1 <= value <= 86400:
        raise argparse.ArgumentTypeError("duration must be between 1 and 86400")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Soren Linux FFmpeg direct stream")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("config", help="print redacted normalized config")
    command_parser = subparsers.add_parser("command", help="print FFmpeg argv as JSON")
    command_parser.add_argument("--mode", choices=("live", "record"), default="live")
    command_parser.add_argument("--output", type=Path)
    command_parser.add_argument("--duration", type=_positive_duration)
    validate_parser = subparsers.add_parser("validate", help="validate config and host prerequisites")
    validate_parser.add_argument("--mode", choices=("live", "record"), default="live")
    subparsers.add_parser("run", help="run the loopback RTMP publisher in the foreground")
    record_parser = subparsers.add_parser("record", help="make a bounded local MKV PoC")
    record_parser.add_argument("--output", type=Path, required=True)
    record_parser.add_argument("--duration", type=_positive_duration, default=60)
    subparsers.add_parser("status", help="print redacted status JSON")
    subparsers.add_parser("stop", help="stop a runner started by this script")

    args = parser.parse_args(argv)
    config = load_config()

    if args.action == "config":
        print(json.dumps(config.public_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.action == "command":
        output = args.output
        if args.mode == "record" and output is None:
            raise ConfigError("record command requires --output")
        command = build_ffmpeg_command(
            config,
            mode=args.mode,
            output_path=output,
            duration_sec=args.duration,
        )
        print(json.dumps(command, ensure_ascii=False))
        return 0
    if args.action == "validate":
        captions_active = validate_runtime(config, mode=args.mode)
        if captions_active:
            captions_active = prepare_caption_runtime(config)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": args.mode,
                    "config": config.public_dict(),
                    "closed_captions_active": captions_active,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.action == "run":
        reconnect_config = load_reconnect_config()
        return _run_ffmpeg(
            config,
            mode="live",
            output_path=None,
            duration_sec=None,
            reconnect=reconnect_config,
        )
    if args.action == "record":
        return _run_ffmpeg(
            config,
            mode="record",
            output_path=args.output.resolve(),
            duration_sec=args.duration,
        )
    if args.action == "status":
        print(json.dumps(read_status(config), ensure_ascii=False, sort_keys=True))
        return 0
    if args.action == "stop":
        return _stop(config)
    raise AssertionError(f"unhandled action: {args.action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AlreadyRunningError, ConfigError, RuntimeCheckError) as exc:
        print(f"direct_stream: {exc}", file=sys.stderr)
        raise SystemExit(2)
