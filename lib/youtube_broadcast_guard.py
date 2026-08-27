#!/usr/bin/env python3
"""Fail-closed YouTube live-broadcast recovery worker.

The encoder can keep sending to a liveStream after its liveBroadcast has ended.
This worker detects that split state, creates one replacement broadcast, binds
the sole active/configured stream, and restarts only the ffmpeg publisher so
YouTube receives a timestamp-zero session.  It never touches the game process.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


API_ROOT = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ACTIVE_LIFECYCLES = {"live", "testing"}
UPCOMING_LIFECYCLES = {"created", "ready", "testStarting", "liveStarting"}


class GuardError(RuntimeError):
    """Expected fail-closed condition with a safe, credential-free message."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "1" if default else "0").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise GuardError(f"invalid_boolean:{name}")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise GuardError(f"invalid_integer:{name}") from exc
    if value < minimum:
        raise GuardError(f"integer_below_minimum:{name}")
    return value


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Config:
    enabled: bool
    auto_create: bool
    restart_publisher: bool
    poll_sec: int
    missing_confirmations: int
    create_cooldown_sec: int
    live_timeout_sec: int
    api_timeout_sec: int
    title: str
    privacy: str
    stream_id: str
    state_file: Path
    pid_file: Path
    direct_status_file: Path
    expected_local_target: str

    @classmethod
    def from_env(cls) -> "Config":
        privacy = os.environ.get("YOUTUBE_BROADCAST_GUARD_PRIVACY", "public").strip()
        if privacy not in {"public", "unlisted", "private"}:
            raise GuardError("invalid_privacy")
        return cls(
            enabled=_env_bool("YOUTUBE_BROADCAST_GUARD_ENABLED", False),
            auto_create=_env_bool("YOUTUBE_BROADCAST_GUARD_AUTO_CREATE", False),
            restart_publisher=_env_bool("YOUTUBE_BROADCAST_GUARD_RESTART_PUBLISHER", True),
            poll_sec=_env_int("YOUTUBE_BROADCAST_GUARD_POLL_SEC", 60, 10),
            missing_confirmations=_env_int(
                "YOUTUBE_BROADCAST_GUARD_MISSING_CONFIRMATIONS", 3, 1
            ),
            create_cooldown_sec=_env_int(
                "YOUTUBE_BROADCAST_GUARD_CREATE_COOLDOWN_SEC", 900, 60
            ),
            live_timeout_sec=_env_int(
                "YOUTUBE_BROADCAST_GUARD_LIVE_TIMEOUT_SEC", 120, 10
            ),
            api_timeout_sec=_env_int("YOUTUBE_BROADCAST_GUARD_API_TIMEOUT_SEC", 15, 1),
            title=os.environ.get(
                "YOUTUBE_BROADCAST_GUARD_TITLE",
                "中華AIと メリケンAIによる自動ソ連建国チャレンジ [ソ連ゲーム]",
            ).strip(),
            privacy=privacy,
            stream_id=os.environ.get("YOUTUBE_BROADCAST_STREAM_ID", "").strip(),
            state_file=Path(
                os.environ.get(
                    "YOUTUBE_BROADCAST_GUARD_STATE_FILE",
                    "tmp/state/youtube_broadcast_guard.json",
                )
            ),
            pid_file=Path(
                os.environ.get(
                    "YOUTUBE_BROADCAST_GUARD_PID_FILE",
                    "tmp/state/youtube_broadcast_guard.pid",
                )
            ),
            direct_status_file=Path(
                os.environ.get(
                    "YOUTUBE_BROADCAST_GUARD_DIRECT_STATUS_FILE",
                    "tmp/state/direct_stream/status.json",
                )
            ),
            expected_local_target=os.environ.get(
                "YOUTUBE_BROADCAST_GUARD_EXPECTED_TARGET",
                "rtmp://127.0.0.1:1935/soren/live",
            ),
        )


class YouTubeAPI:
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        self.access_token = ""

    def refresh(self) -> None:
        required = {
            "client_id": os.environ.get("YOUTUBE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET", ""),
            "refresh_token": os.environ.get("YOUTUBE_OAUTH_REFRESH_TOKEN", ""),
        }
        if not all(required.values()):
            raise GuardError("oauth_not_configured")
        payload = urllib.parse.urlencode(
            {**required, "grant_type": "refresh_token"}
        ).encode()
        data = self._request_json(TOKEN_URL, method="POST", body=payload, auth=False)
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise GuardError("oauth_refresh_failed")
        self.access_token = token

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded"
                if url == TOKEN_URL
                else "application/json; charset=utf-8"
            )
        if auth:
            if not self.access_token:
                raise GuardError("oauth_access_token_missing")
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                error_data = json.loads(exc.read().decode("utf-8", errors="replace"))
                api_error = error_data.get("error")
                if isinstance(api_error, dict):
                    errors = api_error.get("errors") or []
                    first = errors[0] if errors and isinstance(errors[0], dict) else {}
                    reason = first.get("reason") or api_error.get("status") or "api_error"
                elif isinstance(api_error, str):
                    reason = api_error
                else:
                    reason = "http_error"
            except Exception:
                reason = "http_error"
            if url == TOKEN_URL and isinstance(reason, str):
                raise GuardError(f"oauth_refresh:{reason}") from exc
            raise GuardError(f"youtube_api_http_{exc.code}:{reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GuardError(f"network_error:{type(exc).__name__}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GuardError("invalid_json_response") from exc
        if not isinstance(parsed, dict):
            raise GuardError("invalid_api_response")
        return parsed

    def _api(
        self,
        path: str,
        *,
        params: dict[str, str],
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        return self._request_json(
            f"{API_ROOT}/{path}?{query}", method=method, body=body
        )

    def broadcasts(self, broadcast_status: str) -> list[dict[str, Any]]:
        data = self._api(
            "liveBroadcasts",
            params={
                "part": "id,snippet,status,contentDetails",
                "broadcastStatus": broadcast_status,
                "broadcastType": "all",
                "mine": "true",
                "maxResults": "50",
            },
        )
        items = data.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    def streams(self) -> list[dict[str, Any]]:
        data = self._api(
            "liveStreams",
            params={"part": "id,status,snippet", "mine": "true", "maxResults": "50"},
        )
        items = data.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    def create_broadcast(self, config: Config) -> str:
        scheduled = (
            datetime.now(timezone.utc) + timedelta(seconds=10)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        data = self._api(
            "liveBroadcasts",
            params={"part": "id,snippet,status,contentDetails"},
            method="POST",
            payload={
                "snippet": {
                    "title": config.title,
                    "scheduledStartTime": scheduled,
                },
                "status": {
                    "privacyStatus": config.privacy,
                    "selfDeclaredMadeForKids": False,
                },
                "contentDetails": {
                    "enableAutoStart": True,
                    "enableAutoStop": False,
                    "enableDvr": True,
                    "recordFromStart": True,
                    "monitorStream": {"enableMonitorStream": False},
                },
            },
        )
        broadcast_id = data.get("id")
        if not isinstance(broadcast_id, str) or not broadcast_id:
            raise GuardError("broadcast_create_missing_id")
        return broadcast_id

    def bind(self, broadcast_id: str, stream_id: str) -> None:
        self._api(
            "liveBroadcasts/bind",
            params={
                "part": "id,contentDetails",
                "id": broadcast_id,
                "streamId": stream_id,
            },
            method="POST",
        )


def _lifecycle(item: dict[str, Any]) -> str:
    return str((item.get("status") or {}).get("lifeCycleStatus") or "")


def _select_stream(items: list[dict[str, Any]], configured_id: str) -> str:
    if configured_id:
        matches = [item for item in items if item.get("id") == configured_id]
        if len(matches) != 1:
            raise GuardError("configured_stream_not_found")
        return configured_id
    active = [
        str(item.get("id"))
        for item in items
        if (item.get("status") or {}).get("streamStatus") == "active" and item.get("id")
    ]
    if len(active) != 1:
        raise GuardError(f"active_stream_count:{len(active)}")
    return active[0]


def _publisher_cmdline(pid: int) -> str:
    return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
        "utf-8", errors="replace"
    )


def restart_publisher(
    config: Config,
    *,
    kill_fn: Callable[[int, int], None] = os.kill,
    cmdline_fn: Callable[[int], str] = _publisher_cmdline,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> tuple[int, int]:
    before = _read_json(config.direct_status_file)
    try:
        old_pid = int(before["ffmpeg_pid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GuardError("direct_stream_ffmpeg_pid_missing") from exc
    if before.get("running") is not True:
        raise GuardError("direct_stream_not_running")
    try:
        cmdline = cmdline_fn(old_pid)
    except OSError as exc:
        raise GuardError("direct_stream_cmdline_unreadable") from exc
    if "ffmpeg" not in cmdline or config.expected_local_target not in cmdline:
        raise GuardError("direct_stream_identity_mismatch")
    kill_fn(old_pid, signal.SIGTERM)
    deadline = monotonic_fn() + config.live_timeout_sec
    while monotonic_fn() < deadline:
        sleep_fn(2)
        current = _read_json(config.direct_status_file)
        try:
            new_pid = int(current.get("ffmpeg_pid", 0))
        except (TypeError, ValueError):
            new_pid = 0
        if current.get("running") is True and new_pid and new_pid != old_pid:
            return old_pid, new_pid
    raise GuardError("direct_stream_restart_timeout")


def _base_state(previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "updated_at": int(time.time()),
        "missing_count": int(previous.get("missing_count") or 0),
        "last_create_at": int(previous.get("last_create_at") or 0),
        "last_restart_at": int(previous.get("last_restart_at") or 0),
        "broadcast_id": str(previous.get("broadcast_id") or ""),
        "stream_id": str(previous.get("stream_id") or ""),
    }


def _wait_for_live(
    config: Config,
    api: YouTubeAPI,
    state: dict[str, Any],
    broadcast_id: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + config.live_timeout_sec
    while time.monotonic() < deadline:
        active = api.broadcasts("active")
        if any(item.get("id") == broadcast_id for item in active):
            state.update(status="live", action="recovered", error="")
            _atomic_json(config.state_file, state)
            return state
        time.sleep(5)
    raise GuardError("broadcast_live_timeout")


def check_once(
    config: Config,
    api: YouTubeAPI,
    *,
    force: bool = False,
    restart_fn: Callable[[Config], tuple[int, int]] = restart_publisher,
) -> dict[str, Any]:
    previous = _read_json(config.state_file)
    state = _base_state(previous)
    if not config.enabled and not force:
        state.update(status="disabled", action="none")
        _atomic_json(config.state_file, state)
        return state
    try:
        api.refresh()
        active = [item for item in api.broadcasts("active") if _lifecycle(item) in ACTIVE_LIFECYCLES]
        if active:
            state.update(
                status="live",
                action="none",
                missing_count=0,
                broadcast_id=str(active[0].get("id") or ""),
                error="",
            )
            _atomic_json(config.state_file, state)
            return state
        upcoming = [
            item for item in api.broadcasts("upcoming") if _lifecycle(item) in UPCOMING_LIFECYCLES
        ]
        if upcoming:
            own_pending = next(
                (
                    item
                    for item in upcoming
                    if item.get("id") == state["broadcast_id"]
                    and state["last_create_at"] > 0
                ),
                None,
            )
            if (
                own_pending is not None
                and config.restart_publisher
                and state["last_restart_at"] < state["last_create_at"]
            ):
                old_pid, new_pid = restart_fn(config)
                state.update(
                    status="restarting",
                    action="publisher_restarted",
                    missing_count=0,
                    last_restart_at=int(time.time()),
                    old_ffmpeg_pid=old_pid,
                    new_ffmpeg_pid=new_pid,
                    error="",
                )
                _atomic_json(config.state_file, state)
                return _wait_for_live(config, api, state, state["broadcast_id"])
            state.update(
                status="upcoming",
                action="none",
                missing_count=0,
                broadcast_id=str(upcoming[0].get("id") or ""),
                error="",
            )
            _atomic_json(config.state_file, state)
            return state

        state["missing_count"] += 1
        state.update(status="missing", action="none", error="")
        if not config.auto_create and not force:
            _atomic_json(config.state_file, state)
            return state
        if not force and state["missing_count"] < config.missing_confirmations:
            _atomic_json(config.state_file, state)
            return state
        now = int(time.time())
        if not force and now - state["last_create_at"] < config.create_cooldown_sec:
            state.update(status="cooldown")
            _atomic_json(config.state_file, state)
            return state

        stream_id = _select_stream(api.streams(), config.stream_id)
        broadcast_id = api.create_broadcast(config)
        api.bind(broadcast_id, stream_id)
        state.update(
            status="created",
            action="created",
            missing_count=0,
            last_create_at=now,
            broadcast_id=broadcast_id,
            stream_id=stream_id,
        )
        _atomic_json(config.state_file, state)
        if config.restart_publisher:
            old_pid, new_pid = restart_fn(config)
            state.update(
                status="restarting",
                action="publisher_restarted",
                last_restart_at=int(time.time()),
                old_ffmpeg_pid=old_pid,
                new_ffmpeg_pid=new_pid,
            )
            _atomic_json(config.state_file, state)

        return _wait_for_live(config, api, state, broadcast_id)
    except GuardError as exc:
        state.update(status="error", action="none", error=str(exc)[:240])
        _atomic_json(config.state_file, state)
        return state


def run(config: Config) -> int:
    stop = False

    def _stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    config.pid_file.parent.mkdir(parents=True, exist_ok=True)
    previous_pid = 0
    try:
        previous_pid = int(config.pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        pass
    if previous_pid:
        try:
            os.kill(previous_pid, 0)
        except OSError:
            pass
        else:
            raise GuardError(f"already_running:{previous_pid}")
    config.pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        api = YouTubeAPI(config.api_timeout_sec)
        while not stop:
            state = check_once(config, api)
            print(json.dumps(state, ensure_ascii=False, separators=(",", ":")), flush=True)
            deadline = time.monotonic() + config.poll_sec
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        return 0
    finally:
        try:
            if int(config.pid_file.read_text(encoding="utf-8").strip()) == os.getpid():
                config.pid_file.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    try:
        config = Config.from_env()
    except GuardError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    if command == "status":
        print(json.dumps(_read_json(config.state_file), ensure_ascii=False))
        return 0
    if command == "check":
        force = "--force" in argv[2:]
        state = check_once(config, YouTubeAPI(config.api_timeout_sec), force=force)
        print(json.dumps(state, ensure_ascii=False))
        return 0 if state.get("status") != "error" else 1
    if command == "run":
        try:
            return run(config)
        except GuardError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
            return 1
    print("usage: youtube_broadcast_guard.py {status|check [--force]|run}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
