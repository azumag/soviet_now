#!/usr/bin/env python3
"""Weather-corner adapter for Soren's existing shared comment queue.

This module accepts already-written literal narration. It does not fetch
forecasts, generate speech text, or enable a producer. Receipts are stored in
the comment queue's metadata area; there is no second playback queue.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo


SOURCE = "weather_corner"
JMA_SOURCE = "https://www.jma.go.jp/bosai/forecast/"
REQUEST_KEYS = {
    "schema_version", "source", "execution_id", "item_index", "item_key",
    "text", "runtime_fence", "forecast",
}
FENCE_KEYS = {"game", "runtime_id", "generation", "lease_id", "expires_at"}
FORECAST_KEYS = {"source_url", "date", "issued_at", "report_digest"}
RECEIPT_KEYS = {
    "schema_version", "source", "item_key", "request_digest", "status",
    "runtime_fence", "forecast", "recorded_at", "reason",
}
RUNTIME_ID_RE = re.compile(r"g([1-9][0-9]*)-[a-f0-9]{6,32}\Z")
SHA256_RE = re.compile(r"[a-f0-9]{64}\Z")
ITEM_NAME_RE = re.compile(
    r"comment_announce_weather_audio_[0-9]{16,}_([0-9a-f-]{36})_([0-9]{2})_weather_audio_item\.(?:txt|playing)\Z"
)
MAX_REQUEST_BYTES = 65536
MAX_RECEIPT_BYTES = 65536
MAX_ITEM_TEXT_CHARS = 1000
MAX_ITEM_INDEX = 12
MAX_FORECAST_AGE_SEC = 18 * 60 * 60
MAX_QUEUE_TTL_SEC = 15 * 60
GAME_SWITCH_LOCK_BURST_SEC = 0.5
GAME_SWITCH_MONITOR_GRACE_SEC = 1.0
PLAYER_MONITOR_INTERVAL_SEC = 0.05
PLAYER_STOP_GRACE_SEC = 1.0
MAX_PLAYER_CHUNKS = 128


class WeatherAudioError(ValueError):
    pass


def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WeatherAudioError("duplicate JSON key")
        result[key] = value
    return result


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _read_json(path: Path, limit: int) -> object:
    if path.is_symlink() or path.stat().st_size > limit:
        raise WeatherAudioError("unsafe JSON record")
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_object_no_duplicates,
        parse_constant=lambda _value: (_ for _ in ()).throw(WeatherAudioError("non-finite JSON value")),
    )


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise WeatherAudioError(f"invalid {label}")
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise WeatherAudioError(f"invalid {label}") from exc
    if normalized != value:
        raise WeatherAudioError(f"non-canonical {label}")
    return normalized


def _finite_number(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WeatherAudioError(f"invalid {label}")
    return float(value)


def _runtime_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"game", "runtime_id", "generation", "lease_id"}:
        raise WeatherAudioError("invalid runtime identity")
    runtime_id = value.get("runtime_id")
    match = RUNTIME_ID_RE.fullmatch(runtime_id) if isinstance(runtime_id, str) else None
    generation = value.get("generation")
    if (value.get("game") != "weather-view" or match is None
            or type(generation) is not int or generation < 1
            or int(match.group(1)) != generation):
        raise WeatherAudioError("invalid runtime identity")
    lease_id = _uuid(value.get("lease_id"), "lease identity")
    return {
        "game": "weather-view", "runtime_id": runtime_id,
        "generation": generation, "lease_id": lease_id,
    }


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise WeatherAudioError("invalid forecast issue time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise WeatherAudioError("invalid forecast issue time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != value:
        raise WeatherAudioError("non-canonical forecast issue time")
    return parsed


def normalize_request(value: object) -> dict[str, object]:
    """Validate the shared docich value contract without refreshing its data."""
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
        raise WeatherAudioError("invalid request fields")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise WeatherAudioError("unsupported request version")
    if value.get("source") != SOURCE:
        raise WeatherAudioError("invalid weather source")
    execution_id = _uuid(value.get("execution_id"), "execution identity")
    item_index = value.get("item_index")
    if type(item_index) is not int or not 0 <= item_index <= MAX_ITEM_INDEX:
        raise WeatherAudioError("invalid narration item index")
    item_key = f"{SOURCE}:{execution_id}:{item_index:02d}"
    if value.get("item_key") != item_key:
        raise WeatherAudioError("item key mismatch")

    text = value.get("text")
    if (not isinstance(text, str) or not text or len(text) > MAX_ITEM_TEXT_CHARS
            or any(ord(char) < 32 for char in text) or "<" in text or ">" in text):
        raise WeatherAudioError("invalid narration text")
    normalized_text = " ".join(text.split())
    if not normalized_text:
        raise WeatherAudioError("empty narration text")

    raw_fence = value.get("runtime_fence")
    if not isinstance(raw_fence, dict) or set(raw_fence) != FENCE_KEYS:
        raise WeatherAudioError("invalid runtime fence")
    identity = _runtime_identity({key: raw_fence[key] for key in ("game", "runtime_id", "generation", "lease_id")})
    expiry = _finite_number(raw_fence.get("expires_at"), "runtime expiry")

    raw_forecast = value.get("forecast")
    if not isinstance(raw_forecast, dict) or set(raw_forecast) != FORECAST_KEYS:
        raise WeatherAudioError("invalid forecast metadata")
    if raw_forecast.get("source_url") != JMA_SOURCE:
        raise WeatherAudioError("invalid forecast source")
    target_text = raw_forecast.get("date")
    if not isinstance(target_text, str):
        raise WeatherAudioError("invalid forecast date")
    try:
        target_date = date.fromisoformat(target_text)
    except ValueError as exc:
        raise WeatherAudioError("invalid forecast date") from exc
    if target_date.isoformat() != target_text:
        raise WeatherAudioError("non-canonical forecast date")
    issued_at = _parse_stamp(raw_forecast.get("issued_at"))
    report_digest = raw_forecast.get("report_digest")
    if not isinstance(report_digest, str) or SHA256_RE.fullmatch(report_digest) is None:
        raise WeatherAudioError("invalid report digest")
    issued_epoch = issued_at.timestamp()
    if not issued_epoch < expiry <= issued_epoch + MAX_FORECAST_AGE_SEC:
        raise WeatherAudioError("forecast and expiry are inconsistent")
    if not issued_at.date() <= target_date <= issued_at.date() + timedelta(days=2):
        raise WeatherAudioError("forecast target and issue date are inconsistent")

    return {
        "schema_version": 1,
        "source": SOURCE,
        "execution_id": execution_id,
        "item_index": item_index,
        "item_key": item_key,
        "text": normalized_text,
        "runtime_fence": {**identity, "expires_at": expiry},
        "forecast": {
            "source_url": JMA_SOURCE,
            "date": target_text,
            "issued_at": raw_forecast["issued_at"],
            "report_digest": report_digest,
        },
    }


def request_digest(request: dict[str, object]) -> str:
    return hashlib.sha256(_json_bytes(request)).hexdigest()


def _request_from_json(raw: str) -> dict[str, object]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise WeatherAudioError("request is too large")
    value = json.loads(
        raw, object_pairs_hook=_object_no_duplicates,
        parse_constant=lambda _value: (_ for _ in ()).throw(WeatherAudioError("non-finite JSON value")),
    )
    return normalize_request(value)


def _queue_dir(value: str | None) -> Path:
    result = Path(value or os.environ.get("COMMENT_QUEUE_DIR", "tmp/.comment_queue"))
    result.mkdir(parents=True, exist_ok=True)
    if result.is_symlink() or not result.is_dir():
        raise WeatherAudioError("unsafe shared queue directory")
    return result


@contextmanager
def _queue_lock(queue: Path):
    path = queue / ".weather_audio.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _receipt_dir(queue: Path) -> Path:
    path = queue / ".weather_audio_receipts"
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise WeatherAudioError("unsafe receipt directory")
    return path


def _record_path(queue: Path, item_key: str) -> Path:
    match = re.fullmatch(r"weather_corner:([0-9a-f-]{36}):(0[0-9]|1[0-2])", item_key)
    if match is None:
        raise WeatherAudioError("invalid item key")
    execution_id = _uuid(match.group(1), "execution identity")
    item_index = int(match.group(2))
    return _receipt_dir(queue) / f"{execution_id}_{item_index:02d}.json"


def _target_sidecar(target: Path) -> Path:
    if target.suffix in (".txt", ".playing"):
        target = target.with_suffix("")
    return Path(f"{target}.weather_audio.json")


def _target_key(target: Path) -> str:
    match = ITEM_NAME_RE.fullmatch(target.name)
    if match is None:
        raise WeatherAudioError("invalid weather item filename")
    execution_id = _uuid(match.group(1), "execution identity")
    index = int(match.group(2))
    if index > MAX_ITEM_INDEX:
        raise WeatherAudioError("invalid weather item filename")
    return f"{SOURCE}:{execution_id}:{index:02d}"


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_record(path: Path, record: dict[str, object]) -> None:
    _atomic_write(path, _json_bytes(record) + b"\n")


def _read_record(queue: Path, item_key: str) -> tuple[Path, dict[str, object]]:
    path = _record_path(queue, item_key)
    value = _read_json(path, MAX_RECEIPT_BYTES)
    if (not isinstance(value, dict)
            or set(value) != {
                "schema_version", "request_digest", "queue_filename", "phase", "receipt",
                "expected_player_count", "player_attempts", "player_successes",
                "player_active", "player_pending",
            }
            or type(value.get("schema_version")) is not int or value["schema_version"] != 2
            or not isinstance(value.get("request_digest"), str)
            or SHA256_RE.fullmatch(value["request_digest"]) is None
            or value.get("phase") not in {"queued", "playing", "terminal"}
            or not isinstance(value.get("receipt"), dict)
            or set(value["receipt"]) != RECEIPT_KEYS
            or type(value.get("expected_player_count")) is not int
            or not 0 <= value["expected_player_count"] <= MAX_PLAYER_CHUNKS
            or type(value.get("player_attempts")) is not int
            or not 0 <= value["player_attempts"] <= MAX_PLAYER_CHUNKS
            or type(value.get("player_successes")) is not int
            or not 0 <= value["player_successes"] <= value["player_attempts"]
            or value["player_attempts"] > value["expected_player_count"]
            or type(value.get("player_active")) is not bool
            or type(value.get("player_pending")) is not bool
            or value["player_active"] and value["player_pending"]):
        raise WeatherAudioError("invalid receipt record")
    if value["phase"] == "queued" and (
        value["player_attempts"] or value["player_successes"]
        or value["player_active"] or value["player_pending"]
    ):
        raise WeatherAudioError("invalid queued player record")
    if value["phase"] == "playing" and (
        value["expected_player_count"] < 1
        or value["player_attempts"] < 1
        or value["player_attempts"] - value["player_successes"] not in (0, 1)
        or value["player_active"] and value["player_attempts"] != value["player_successes"] + 1
        or value["player_pending"] and value["player_attempts"] != value["player_successes"] + 1
    ):
        raise WeatherAudioError("invalid playing player record")
    if value["phase"] == "terminal" and (value["player_active"] or value["player_pending"]):
        raise WeatherAudioError("invalid terminal player record")
    filename = value.get("queue_filename")
    if filename and (not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith("_weather_audio_item.txt")):
        raise WeatherAudioError("invalid queue record")
    return path, value


def _make_receipt(request: dict[str, object], digest: str, status: str, now: float, reason: str | None) -> dict[str, object]:
    issued_epoch = _parse_stamp(request["forecast"]["issued_at"]).timestamp()
    if now < issued_epoch:
        raise WeatherAudioError("local clock precedes the forecast issue")
    return {
        "schema_version": 1,
        "source": SOURCE,
        "item_key": request["item_key"],
        "request_digest": digest,
        "status": status,
        "runtime_fence": request["runtime_fence"],
        "forecast": request["forecast"],
        "recorded_at": now,
        "reason": reason,
    }


def _store_initial(
    request: dict[str, object], digest: str, status: str, reason: str | None,
    *, now: float, queue_filename: str = "",
) -> dict[str, object]:
    receipt = _make_receipt(request, digest, status, now, reason)
    return {
        "schema_version": 2,
        "request_digest": digest,
        "queue_filename": queue_filename,
        "phase": "terminal" if status == "rejected" else "queued",
        "receipt": receipt,
        "expected_player_count": 0,
        "player_attempts": 0,
        "player_successes": 0,
        "player_active": False,
        "player_pending": False,
    }


def _freshness_reason(request: dict[str, object], now: float) -> str | None:
    fence = request["runtime_fence"]
    forecast = request["forecast"]
    expiry = float(fence["expires_at"])
    issued = _parse_stamp(forecast["issued_at"]).timestamp()
    target = date.fromisoformat(forecast["date"])
    today = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo("Asia/Tokyo")).date()
    if issued > now:
        raise WeatherAudioError("local clock precedes the forecast issue")
    if (now >= expiry or now - issued > MAX_FORECAST_AGE_SEC
            or target not in (today, today + timedelta(days=1))):
        return "expired"
    return None


def _switch_path(canonical: Path) -> Path:
    if canonical.is_symlink():
        raise WeatherAudioError("unsafe runtime context")
    return canonical.parent / "locks" / "game-switch.lock"


@contextmanager
def _game_switch_lock(canonical: Path, *, budget_sec: float | None = None):
    lock_path = _switch_path(canonical)
    if lock_path.is_symlink() or lock_path.parent.is_symlink():
        raise WeatherAudioError("unsafe runtime lock")
    fd = os.open(lock_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    acquired = False
    try:
        if budget_sec is None:
            fcntl.flock(fd, fcntl.LOCK_SH)
        else:
            deadline = time.monotonic() + budget_sec
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("GameSwitch lock stayed exclusive")
                    time.sleep(min(0.05, remaining))
        acquired = True
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _active_identity(canonical: Path) -> dict[str, object]:
    value = _read_json(canonical, 65536)
    if not isinstance(value, dict) or value.get("phase") != "ready":
        raise WeatherAudioError("runtime is not ready")
    active = value.get("active")
    if not isinstance(active, dict):
        raise WeatherAudioError("runtime has no active identity")
    return _runtime_identity({
        key: active.get(key) for key in ("game", "runtime_id", "generation", "lease_id")
    })


def _runtime_matches(
    canonical: Path, request: dict[str, object], *, lock_budget_sec: float | None = None,
) -> bool:
    try:
        with _game_switch_lock(canonical, budget_sec=lock_budget_sec):
            current = _active_identity(canonical)
    except TimeoutError:
        if lock_budget_sec is not None:
            raise
        return False
    except (OSError, ValueError, WeatherAudioError):
        return False
    expected = request["runtime_fence"]
    return all(current[key] == expected[key] and type(current[key]) is type(expected[key])
               for key in ("game", "runtime_id", "generation", "lease_id"))


def _monitor_runtime_matches(canonical: Path, request: dict[str, object]) -> bool:
    """Fail closed after bounded nonblocking reads; never wait behind a switch."""
    deadline = time.monotonic() + GAME_SWITCH_MONITOR_GRACE_SEC
    while True:
        try:
            return _runtime_matches(
                canonical, request, lock_budget_sec=GAME_SWITCH_LOCK_BURST_SEC,
            )
        except TimeoutError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))


def _canonical_path() -> Path:
    return Path(os.environ.get(
        "SOREN_ACTIVE_GAME_CONTEXT_FILE",
        "/home/ubuntu/docich/run-soren-live/game_switch.json",
    ))


def _request_from_sidecar(target: Path) -> dict[str, object]:
    path = _target_sidecar(target)
    value = _read_json(path, MAX_REQUEST_BYTES)
    return normalize_request(value)


def _reject_record(queue: Path, path: Path, record: dict[str, object], request: dict[str, object], reason: str) -> dict[str, object]:
    if record["phase"] == "terminal":
        return record["receipt"]
    status = "interrupted" if record["phase"] == "playing" else "rejected"
    if status == "interrupted":
        reason = "runtime_fence_lost" if reason in {"runtime_mismatch", "expired"} else "playback_interrupted"
    elif reason not in {"expired", "runtime_mismatch", "queue_rejected", "player_rejected"}:
        reason = "queue_rejected"
    receipt = _make_receipt(request, record["request_digest"], status, time.time(), reason)
    record["phase"] = "terminal"
    record["player_active"] = False
    record["player_pending"] = False
    record["receipt"] = receipt
    _write_record(path, record)
    return receipt


def _interrupt_record(path: Path, record: dict[str, object], request: dict[str, object], reason: str) -> dict[str, object]:
    if record["phase"] == "terminal":
        return record["receipt"]
    if reason not in {"runtime_fence_lost", "worker_interrupted", "playback_interrupted"}:
        reason = "worker_interrupted"
    receipt = _make_receipt(request, record["request_digest"], "interrupted", time.time(), reason)
    record["phase"] = "terminal"
    record["player_active"] = False
    record["player_pending"] = False
    record["receipt"] = receipt
    _write_record(path, record)
    return receipt


def _record_player_completion(
    queue: Path, key: str, request: dict[str, object], exit_code: int,
    canonical: Path,
) -> dict[str, object]:
    """Record one owned chunk's exit; whole-item success is finalized later."""
    with _queue_lock(queue):
        path, record = _read_record(queue, key)
        if record["phase"] == "terminal":
            return record
        if (record["phase"] != "playing" or not record["player_active"]
                or record["player_pending"]):
            _interrupt_record(path, record, request, "worker_interrupted")
            return record
        if not _monitor_runtime_matches(canonical, request):
            _interrupt_record(path, record, request, "runtime_fence_lost")
            return record
        if exit_code == 0:
            record["player_active"] = False
            record["player_pending"] = True
            _write_record(path, record)
            return record
        _interrupt_record(path, record, request, "playback_interrupted")
        return record


def _plan_players(target: Path, queue: Path, expected_count: int) -> None:
    if type(expected_count) is not int or not 1 <= expected_count <= MAX_PLAYER_CHUNKS:
        raise WeatherAudioError("invalid player chunk count")
    key = _target_key(target)
    with _queue_lock(queue):
        request = _request_from_sidecar(target)
        if request["item_key"] != key:
            raise WeatherAudioError("item metadata key mismatch")
        path, record = _read_record(queue, key)
        if request_digest(request) != record["request_digest"]:
            raise WeatherAudioError("item metadata digest mismatch")
        if record["phase"] == "terminal":
            raise WeatherAudioError("weather item is already terminal")
        if record["expected_player_count"] == expected_count:
            return
        if (record["expected_player_count"] != 0 or record["player_attempts"]
                or record["player_successes"] or record["player_active"]
                or record["player_pending"] or record["phase"] != "queued"):
            raise WeatherAudioError("player chunk plan is already fixed")
        record["expected_player_count"] = expected_count
        _write_record(path, record)


def _ack_player(target: Path, queue: Path) -> None:
    key = _target_key(target)
    with _queue_lock(queue):
        request = _request_from_sidecar(target)
        if request["item_key"] != key:
            raise WeatherAudioError("item metadata key mismatch")
        path, record = _read_record(queue, key)
        if request_digest(request) != record["request_digest"]:
            raise WeatherAudioError("item metadata digest mismatch")
        if record["phase"] == "terminal":
            raise WeatherAudioError("weather item is already terminal")
        if (record["phase"] != "playing" or record["player_active"]
                or not record["player_pending"]
                or record["player_attempts"] != record["player_successes"] + 1
                or record["player_successes"] >= record["expected_player_count"]):
            raise WeatherAudioError("no completed owned player awaits acknowledgement")
        if not _monitor_runtime_matches(_canonical_path(), request):
            _interrupt_record(path, record, request, "runtime_fence_lost")
            raise WeatherAudioError("runtime fence changed before player acknowledgement")
        record["player_successes"] += 1
        record["player_pending"] = False
        _write_record(path, record)


def _interrupt_item(target: Path, queue: Path) -> dict[str, object]:
    key = _target_key(target)
    with _queue_lock(queue):
        request = _request_from_sidecar(target)
        if request["item_key"] != key:
            raise WeatherAudioError("item metadata key mismatch")
        path, record = _read_record(queue, key)
        if request_digest(request) != record["request_digest"]:
            raise WeatherAudioError("item metadata digest mismatch")
        if record["phase"] == "terminal":
            return record["receipt"]
        if record["phase"] == "playing":
            return _interrupt_record(path, record, request, "playback_interrupted")
        return _reject_record(queue, path, record, request, "player_rejected")


def _stop_owned_player(child: subprocess.Popen) -> None:
    """Stop and reap only this helper's process group within a fixed bound."""
    deadline = time.monotonic() + PLAYER_STOP_GRACE_SEC
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    while time.monotonic() < deadline:
        if child.poll() is not None:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
        time.sleep(0.02)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if child.poll() is None:
        child.wait()


def _item_check(target: Path, queue: Path) -> bool:
    key = _target_key(target)
    with _queue_lock(queue):
        request = _request_from_sidecar(target)
        if request["item_key"] != key:
            raise WeatherAudioError("item metadata key mismatch")
        path, record = _read_record(queue, key)
        if request_digest(request) != record["request_digest"]:
            raise WeatherAudioError("item metadata digest mismatch")
        if record["phase"] == "terminal":
            return False
        now = time.time()
        reason = _freshness_reason(request, now)
        if reason is None and not _runtime_matches(_canonical_path(), request):
            reason = "runtime_mismatch"
        if reason is not None:
            _reject_record(queue, path, record, request, reason)
            return False
        return True


def _queue_request(raw: str, queue: Path) -> dict[str, object]:
    request = _request_from_json(raw)
    digest = request_digest(request)
    key = request["item_key"]
    now = time.time()
    with _queue_lock(queue):
        path = _record_path(queue, key)
        if path.exists() or path.is_symlink():
            existing_path, record = _read_record(queue, key)
            if record["request_digest"] != digest:
                raise WeatherAudioError("idempotency key conflicts with existing payload")
            if record["phase"] == "terminal":
                return record["receipt"]
            queue_filename = record["queue_filename"]
            if queue_filename:
                item_txt = queue / queue_filename
                item_playing = item_txt.with_suffix(".playing")
                sidecar = _target_sidecar(item_txt)
                if item_playing.exists():
                    # The shared consumer renames .txt to .playing before it
                    # reaches our owned-player start hook. That claim window
                    # is in flight too: a duplicate enqueue must not erase it.
                    # If the worker actually died, the existing orphan reaper
                    # will durably mark it interrupted after its stale grace.
                    return record["receipt"]
                if item_txt.exists():
                    try:
                        current = _request_from_sidecar(item_txt)
                        valid = request_digest(current) == digest and item_txt.read_text(encoding="utf-8").rstrip("\n") == request["text"]
                    except (OSError, ValueError, WeatherAudioError):
                        valid = False
                    if not valid:
                        receipt = _reject_record(queue, existing_path, record, request, "queue_rejected")
                        item_txt.unlink(missing_ok=True)
                        sidecar.unlink(missing_ok=True)
                        return receipt
                    return record["receipt"]
        else:
            reason = _freshness_reason(request, now)
            if reason is None and not _runtime_matches(_canonical_path(), request):
                reason = "runtime_mismatch"
            if reason is not None:
                record = _store_initial(request, digest, "rejected", reason, now=now)
                _write_record(path, record)
                return record["receipt"]
            if float(request["runtime_fence"]["expires_at"]) > now + MAX_QUEUE_TTL_SEC:
                raise WeatherAudioError("runtime fence exceeds the queue lifetime")
            stamp = time.time_ns()
            # A retry reuses this exact filename from the durable record.
            queue_filename = f"comment_announce_weather_audio_{stamp}_{request['execution_id']}_{request['item_index']:02d}_weather_audio_item.txt"
            record = _store_initial(request, digest, "queued", None, now=now, queue_filename=queue_filename)
            _write_record(path, record)

        # Recover an incomplete publish from a prior enqueue process. The
        # durable queued record is written before the text file becomes visible.
        queue_filename = record["queue_filename"]
        if not queue_filename:
            raise WeatherAudioError("queued item has no queue filename")
        item_txt = queue / queue_filename
        if item_txt.with_suffix(".playing").exists():
            _reject_record(queue, path, record, request, "worker_interrupted")
            return record["receipt"]
        sidecar = _target_sidecar(item_txt)
        _atomic_write(sidecar, _json_bytes(request) + b"\n")
        temporary = queue / f".{queue_filename}.{os.getpid()}.{time.time_ns()}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write((request["text"] + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, item_txt)
        except OSError:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            receipt = _reject_record(queue, path, record, request, "queue_rejected")
            sidecar.unlink(missing_ok=True)
            return receipt
        return record["receipt"]


def _run_player(target: Path, queue: Path, command: list[str]) -> int:
    if not command:
        raise WeatherAudioError("missing owned player command")
    key = _target_key(target)
    request = None
    record_path = None
    child = None

    def interrupted(_signum, _frame):
        raise InterruptedError("owned player wrapper interrupted")

    previous_term = signal.signal(signal.SIGTERM, interrupted)
    previous_int = signal.signal(signal.SIGINT, interrupted)
    try:
        # Keep the existing GameSwitch shared lock until the exact player
        # process is spawned, so a switch cannot slip between the final check
        # and owned-player start.
        with _queue_lock(queue):
            request = _request_from_sidecar(target)
            if request["item_key"] != key:
                raise WeatherAudioError("item metadata key mismatch")
            record_path, record = _read_record(queue, key)
            if request_digest(request) != record["request_digest"]:
                raise WeatherAudioError("item metadata digest mismatch")
            if (record["phase"] == "terminal"
                    or record["expected_player_count"] < 1
                    or record["player_attempts"] >= record["expected_player_count"]
                    or record["player_active"] or record["player_pending"]):
                return 75
            canonical = _canonical_path()
            try:
                with _game_switch_lock(canonical):
                    current = _active_identity(canonical)
                    expected = request["runtime_fence"]
                    identity_ok = all(
                        current[key_name] == expected[key_name]
                        and type(current[key_name]) is type(expected[key_name])
                        for key_name in ("game", "runtime_id", "generation", "lease_id")
                    )
                    now = time.time()
                    try:
                        freshness = _freshness_reason(request, now)
                    except WeatherAudioError:
                        return 75
                    if freshness is not None or not identity_ok:
                        reason = freshness or "runtime_mismatch"
                        _reject_record(queue, record_path, record, request, reason)
                        return 75
                    record["phase"] = "playing"
                    record["player_attempts"] += 1
                    record["player_active"] = True
                    _write_record(record_path, record)
                    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
                    try:
                        child = subprocess.Popen(
                            command,
                            start_new_session=True,
                            preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask),
                        )
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            except (OSError, ValueError, WeatherAudioError):
                if record.get("phase") == "playing":
                    _interrupt_record(record_path, record, request, "playback_interrupted")
                    return 74
                _reject_record(queue, record_path, record, request, "runtime_mismatch")
                return 75
        while child.poll() is None:
            with _queue_lock(queue):
                _record_path_value, current_record = _read_record(queue, key)
                if current_record["phase"] == "terminal":
                    _stop_owned_player(child)
                    return 74
                if not _monitor_runtime_matches(canonical, request):
                    _interrupt_record(
                        record_path, current_record, request, "runtime_fence_lost",
                    )
                    _stop_owned_player(child)
                    return 74
            time.sleep(PLAYER_MONITOR_INTERVAL_SEC)
        exit_code = child.wait()
        record = _record_player_completion(queue, key, request, exit_code, canonical)
        if (exit_code != 0 or record["phase"] != "playing"
                or record["player_active"] or not record["player_pending"]):
            return 74
        return 0
    except InterruptedError:
        return 74
    finally:
        if child is not None and child.poll() is None:
            _stop_owned_player(child)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def _finish(target: Path, queue: Path, outcome: str) -> dict[str, object]:
    key = _target_key(target)
    with _queue_lock(queue):
        sidecar = _target_sidecar(target)
        if sidecar.exists() and not sidecar.is_symlink():
            request = _request_from_sidecar(target)
            if request["item_key"] != key:
                raise WeatherAudioError("item metadata key mismatch")
        else:
            _path, record = _read_record(queue, key)
            receipt = record["receipt"]
            if record["phase"] != "terminal":
                uncertain = record["phase"] == "playing" or target.suffix == ".playing" or outcome != "success"
                status = "interrupted" if uncertain else "rejected"
                reason = "playback_interrupted" if status == "interrupted" else "player_rejected"
                receipt = _make_receipt(
                    {
                        "item_key": key,
                        "runtime_fence": receipt["runtime_fence"],
                        "forecast": receipt["forecast"],
                    }, record["request_digest"], status, time.time(), reason,
                )
                record["phase"] = "terminal"
                record["player_active"] = False
                record["player_pending"] = False
                record["receipt"] = receipt
                _write_record(_record_path(queue, key), record)
            return receipt

        path, record = _read_record(queue, key)
        if request_digest(request) != record["request_digest"]:
            raise WeatherAudioError("item metadata digest mismatch")
        if record["phase"] != "terminal":
            if record["phase"] == "playing":
                all_players_acknowledged = (
                    outcome == "success"
                    and record["expected_player_count"] > 0
                    and record["player_attempts"] == record["expected_player_count"]
                    and record["player_successes"] == record["expected_player_count"]
                    and not record["player_active"] and not record["player_pending"]
                )
                if all_players_acknowledged:
                    receipt = _make_receipt(
                        request, record["request_digest"], "played", time.time(), None,
                    )
                else:
                    reason = "playback_interrupted"
                    if (record["player_active"] or record["player_pending"]
                            or record["player_attempts"] > record["player_successes"]):
                        if not _monitor_runtime_matches(_canonical_path(), request):
                            reason = "runtime_fence_lost"
                    receipt = _make_receipt(
                        request, record["request_digest"], "interrupted", time.time(), reason,
                    )
            else:
                receipt = _make_receipt(request, record["request_digest"], "rejected", time.time(), "player_rejected")
            record["phase"] = "terminal"
            record["player_active"] = False
            record["player_pending"] = False
            record["receipt"] = receipt
            _write_record(path, record)
        sidecar.unlink(missing_ok=True)
        return record["receipt"]


def _recover(target: Path, queue: Path) -> dict[str, object]:
    key = _target_key(target)
    with _queue_lock(queue):
        path, record = _read_record(queue, key)
        if record["phase"] != "terminal":
            try:
                request = _request_from_sidecar(target)
            except (OSError, ValueError, WeatherAudioError):
                receipt = record["receipt"]
                request = {
                    "item_key": key,
                    "runtime_fence": receipt["runtime_fence"],
                    "forecast": receipt["forecast"],
                }
            _interrupt_record(path, record, request, "worker_interrupted")
        _target_sidecar(target).unlink(missing_ok=True)
        return record["receipt"]


def _get_receipt(queue: Path, item_key: str) -> dict[str, object] | None:
    with _queue_lock(queue):
        try:
            _path, record = _read_record(queue, item_key)
        except FileNotFoundError:
            return None
        return record["receipt"]


def _emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--queue-dir", required=True)
    enqueue.add_argument("request_json")

    get = subparsers.add_parser("get")
    get.add_argument("--queue-dir", required=True)
    get.add_argument("item_key")

    check = subparsers.add_parser("check")
    check.add_argument("--queue-dir", required=True)
    check.add_argument("target")

    play = subparsers.add_parser("play")
    play.add_argument("--queue-dir", required=True)
    play.add_argument("target")
    play.add_argument("command", nargs=argparse.REMAINDER)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--queue-dir", required=True)
    plan.add_argument("target")
    plan.add_argument("count", type=int)

    ack = subparsers.add_parser("ack")
    ack.add_argument("--queue-dir", required=True)
    ack.add_argument("target")

    interrupt = subparsers.add_parser("interrupt")
    interrupt.add_argument("--queue-dir", required=True)
    interrupt.add_argument("target")

    finish = subparsers.add_parser("finish")
    finish.add_argument("--queue-dir", required=True)
    finish.add_argument("target")
    finish.add_argument("outcome", choices=("success", "failure"))

    recover = subparsers.add_parser("recover")
    recover.add_argument("--queue-dir", required=True)
    recover.add_argument("target")

    try:
        args = parser.parse_args(argv)
        queue = _queue_dir(args.queue_dir)
        if args.operation == "enqueue":
            _emit(_queue_request(args.request_json, queue))
            return 0
        if args.operation == "get":
            _emit(_get_receipt(queue, args.item_key))
            return 0
        target = Path(args.target)
        if args.operation == "check":
            return 0 if _item_check(target, queue) else 75
        if args.operation == "plan":
            _plan_players(target, queue, args.count)
            return 0
        if args.operation == "ack":
            _ack_player(target, queue)
            return 0
        if args.operation == "interrupt":
            _emit(_interrupt_item(target, queue))
            return 0
        if args.operation == "play":
            command = args.command
            if command and command[0] == "--":
                command = command[1:]
            return _run_player(target, queue, command)
        if args.operation == "finish":
            _emit(_finish(target, queue, args.outcome))
            return 0
        if args.operation == "recover":
            _emit(_recover(target, queue))
            return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, WeatherAudioError):
        # Never echo narration, forecast text, credentials, or arbitrary input.
        print("weather audio operation rejected", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
