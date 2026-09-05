#!/usr/bin/env python3
"""Durable, request-scoped coordination for game-only lifecycle changes.

The broker deliberately knows nothing about the shared overlay, audio, or
streaming processes.  It records a request and its acknowledgement under a
private directory, and every mutating operation re-checks the same request
identity before changing state.  A late response from an expired controller
therefore cannot stop a newer game.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import re
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
LIFECYCLE_DIR = Path("tmp/state/game_lifecycle")
REQUEST_FILE = "request.json"
ACK_FILE = "ack.json"
CONTROL_FILE = "control.json"
RESOURCE_FILE = "game_resource.json"
LOCK_FILE = "broker.lock"
HISTORY_DIR = "history"

TERMINAL_STATUSES = frozenset({
    "stopped",
    "cancelled",
    "failed",
    "timeout",
    "unsupported",
    "resumed",
})
STOPPING_STATUS = "stopping"
BOUNDARY_STATUSES = frozenset({"boundary", "stop_requested", "resume_requested"})
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

RC_OK = 0
RC_WAITING = 1
RC_EXPIRED = 2
RC_CONFLICT = 3
RC_INVALID = 4


class LifecycleError(RuntimeError):
    """Expected broker validation or state error."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_request_id(value: str) -> str:
    value = str(value or "")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise LifecycleError("request_id must be a canonical UUID") from exc
    if value != canonical or not UUID_RE.fullmatch(value):
        raise LifecycleError("request_id must be a canonical UUID")
    return canonical


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _read_game_snapshot(root: Path) -> dict[str, Any]:
    state = _json_object(root / "game_state.json") or {}
    runner = _json_object(root / "tmp/state/main_strategy_runner_active.json") or {}
    pid = runner.get("pid")
    runner_alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            runner_alive = True
        except (ProcessLookupError, PermissionError, OSError):
            runner_alive = False
    game_count = None
    try:
        game_count = int((root / "game_count.txt").read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        pass
    return {
        "state": str(state.get("state", "")),
        "score": state.get("score"),
        "pieces": len(state.get("pieces", [])) if isinstance(state.get("pieces"), list) else None,
        "game_count": game_count,
        "runner_pid": pid if isinstance(pid, int) else None,
        "runner_game": runner.get("game"),
        "runner_alive": runner_alive,
        "observed_at": _utc_now(),
    }


def _deadline_expired(request: dict[str, Any]) -> bool:
    try:
        return float(request["deadline_epoch"]) <= time.time()
    except (KeyError, TypeError, ValueError):
        return True


def _identity(request: dict[str, Any]) -> tuple[Any, ...]:
    return (
        request.get("request_id"),
        request.get("game"),
        request.get("generation"),
    )


def _same_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _identity(left) == _identity(right)


class LifecycleStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.directory = self.root / LIFECYCLE_DIR
        self.request_path = self.directory / REQUEST_FILE
        self.ack_path = self.directory / ACK_FILE
        self.control_path = self.directory / CONTROL_FILE
        self.resource_path = self.directory / RESOURCE_FILE
        self.lock_path = self.directory / LOCK_FILE
        self.history_dir = self.directory / HISTORY_DIR

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.history_dir, 0o700)

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.prepare()
        with self.lock_path.open("a+", encoding="utf-8") as stream:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def request(self) -> dict[str, Any] | None:
        return _json_object(self.request_path)

    def ack(self) -> dict[str, Any] | None:
        return _json_object(self.ack_path)

    def control(self) -> dict[str, Any] | None:
        return _json_object(self.control_path)

    def resource(self) -> dict[str, Any] | None:
        return _json_object(self.resource_path)

    def save_request(self, value: dict[str, Any]) -> None:
        _atomic_json(self.request_path, value)

    def save_ack(self, value: dict[str, Any]) -> None:
        _atomic_json(self.ack_path, value)

    def save_control(self, value: dict[str, Any]) -> None:
        _atomic_json(self.control_path, value)

    def save_resource(self, value: dict[str, Any]) -> None:
        _atomic_json(self.resource_path, value)

    def archive_current(self) -> None:
        request = self.request()
        ack = self.ack()
        resource = self.resource()
        if request is None and ack is None and resource is None:
            return
        identity = request or ack or resource or {}
        request_id = str(identity.get("request_id") or "unknown")
        stamp = str(int(time.time() * 1000))
        _atomic_json(
            self.history_dir / f"{stamp}-{request_id}.json",
            {
                "schema": SCHEMA_VERSION,
                "archived_at": _utc_now(),
                "request": request,
                "ack": ack,
                "resource": resource,
            },
        )


def _base_ack(request: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "request_id": request["request_id"],
        "game": request.get("game"),
        "generation": request.get("generation"),
        "deadline_epoch": request.get("deadline_epoch"),
        "deadline_at": request.get("deadline_at"),
        "status": status,
        "updated_at": _utc_now(),
        **extra,
    }


def _record_matches_request(record: dict[str, Any] | None, request: dict[str, Any]) -> bool:
    """Require every durable record to carry the same request identity."""

    if not isinstance(record, dict) or record.get("schema") != SCHEMA_VERSION:
        return False
    for field in ("request_id", "game", "generation", "deadline_epoch", "deadline_at"):
        if field not in record or field not in request or record.get(field) != request.get(field):
            return False
    return True


def _resource_is_irreversible(resource: dict[str, Any] | None) -> bool:
    """Whether the game bridge has already crossed its no-restore fence."""

    return isinstance(resource, dict) and (
        bool(resource.get("irreversible")) or bool(resource.get("quit_called"))
    )


def _control_for(request: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "action": action,
        "request_id": request["request_id"],
        "game": request.get("game"),
        "generation": request.get("generation"),
        "deadline_epoch": request.get("deadline_epoch"),
        "deadline_at": request.get("deadline_at"),
        "created_at": _utc_now(),
    }


def _emit(value: dict[str, Any], rc: int) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return rc


def _check_request_id(args: argparse.Namespace) -> str:
    return _valid_request_id(args.request_id)


def command_request(store: LifecycleStore, args: argparse.Namespace) -> int:
    request_id = _check_request_id(args)
    game = str(args.game or "").strip()
    if not game or "/" in game or "\\" in game or "\x00" in game:
        return _emit({"status": "invalid", "error": "invalid game"}, RC_INVALID)
    generation = args.generation
    if generation is not None and (generation < 1 or generation > 2**31 - 1):
        return _emit({"status": "invalid", "error": "invalid generation"}, RC_INVALID)
    try:
        deadline_sec = float(args.deadline_sec)
    except (TypeError, ValueError):
        return _emit({"status": "invalid", "error": "deadline_sec must be numeric"}, RC_INVALID)
    if not math.isfinite(deadline_sec) or deadline_sec <= 0 or deadline_sec > 86400:
        return _emit({"status": "invalid", "error": "deadline_sec out of range"}, RC_INVALID)

    requested = {
        "schema": SCHEMA_VERSION,
        "request_id": request_id,
        "game": game,
        "generation": generation,
        "created_at": _utc_now(),
        "deadline_epoch": time.time() + deadline_sec,
        "deadline_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=deadline_sec)).isoformat().replace("+00:00", "Z"),
        "snapshot": _read_game_snapshot(store.root),
    }
    with store.lock():
        current = store.request()
        current_ack = store.ack()
        if current is not None and current.get("request_id") != request_id:
            if current_ack is None:
                return _emit({"status": "busy", "request_id": current.get("request_id")}, RC_CONFLICT)
            if not _record_matches_request(current_ack, current):
                # A stale acknowledgement from another request or generation
                # must not block a fresh request.  Archive the orphaned state
                # and allow the new request instead of trusting the ack status.
                store.archive_current()
            elif current_ack.get("status") not in TERMINAL_STATUSES:
                return _emit({"status": "busy", "request_id": current.get("request_id")}, RC_CONFLICT)
            else:
                store.archive_current()
        elif current is None and current_ack is not None:
            # An acknowledgement without its request carries no verifiable
            # identity, so it cannot park the broker.  Archive the orphan and
            # allow the new request.
            store.archive_current()

        if current is not None and current.get("request_id") == request_id:
            if not _same_identity(current, requested):
                return _emit({"status": "conflict", "request_id": request_id}, RC_CONFLICT)
            ack = store.ack() or _base_ack(current, "accepted")
            return _emit({"status": "existing", "request": current, "ack": ack}, RC_OK)

        store.save_request(requested)
        ack = _base_ack(requested, "accepted", snapshot=requested["snapshot"])
        store.save_ack(ack)
        store.control_path.unlink(missing_ok=True)
        store.resource_path.unlink(missing_ok=True)
        return _emit({"status": "accepted", "request": requested, "ack": ack}, RC_OK)


def _load_matching_request(store: LifecycleStore, request_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    request = store.request()
    if request is None:
        return None, None
    if request.get("request_id") != request_id:
        raise LifecycleError("request_id does not match the active lifecycle request")
    if request.get("schema") != SCHEMA_VERSION or not _record_matches_request(request, request):
        raise LifecycleError("active lifecycle request identity is invalid")
    ack = store.ack()
    if ack is not None and not _record_matches_request(ack, request):
        raise LifecycleError("active lifecycle acknowledgement identity is invalid")
    return request, ack


def command_boundary(store: LifecycleStore, args: argparse.Namespace) -> int:
    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None:
            return _emit({"status": "missing", "request_id": request_id}, RC_INVALID)
        ack = ack or _base_ack(request, "accepted")
        if ack.get("status") in TERMINAL_STATUSES:
            return _emit({"request": request, "ack": ack}, RC_EXPIRED if ack.get("status") == "timeout" else RC_CONFLICT)
        if ack.get("status") == STOPPING_STATUS:
            # A stop claim is an atomic no-cancel fence.  A late boundary poll
            # must never downgrade it back to boundary/stop_requested.
            return _emit({"request": request, "ack": ack, "control": store.control()}, RC_OK)
        if _deadline_expired(request):
            next_ack = _base_ack(request, "timeout", reason="boundary deadline expired", snapshot=_read_game_snapshot(store.root))
            store.save_ack(next_ack)
            control = store.control()
            if control and _record_matches_request(control, request):
                store.control_path.unlink(missing_ok=True)
            return _emit({"request": request, "ack": next_ack}, RC_EXPIRED)

        snapshot = _read_game_snapshot(store.root)
        if snapshot.get("state") not in {"GAMEOVER", "STOP"} or snapshot.get("runner_alive"):
            next_ack = _base_ack(
                request,
                "waiting",
                reason="game is still running",
                snapshot=snapshot,
            )
            store.save_ack(next_ack)
            return _emit({"request": request, "ack": next_ack}, RC_WAITING)

        if ack.get("status") not in BOUNDARY_STATUSES:
            next_ack = _base_ack(
                request,
                "boundary",
                boundary_snapshot=snapshot,
                stop_allowed=True,
            )
            store.save_ack(next_ack)
        else:
            next_ack = ack
        return _emit({"request": request, "ack": next_ack}, RC_OK)


def command_stop(store: LifecycleStore, args: argparse.Namespace) -> int:
    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing"}, RC_INVALID)
        status = ack.get("status")
        if status == "stopped":
            return _emit({"request": request, "ack": ack, "resource": store.resource()}, RC_OK)
        if status == STOPPING_STATUS:
            control = store.control()
            if control is None or not _record_matches_request(control, request) or control.get("action") != "stop":
                return _emit({"status": "conflict", "error": "stopping claim lacks its matching stop control"}, RC_CONFLICT)
            return _emit({"request": request, "ack": ack, "control": control}, RC_OK)
        if status in TERMINAL_STATUSES:
            # stopped is handled idempotently above; every other terminal
            # status (cancelled/failed/timeout/unsupported/resumed) conflicts.
            return _emit({"request": request, "ack": ack}, RC_CONFLICT)
        if _deadline_expired(request):
            next_ack = _base_ack(request, "timeout", reason="stop request deadline expired")
            store.save_ack(next_ack)
            control = store.control()
            if control and _record_matches_request(control, request):
                store.control_path.unlink(missing_ok=True)
            return _emit({"request": request, "ack": next_ack}, RC_EXPIRED)

        if status not in {"boundary", "stop_requested"}:
            return _emit(
                {"request": request, "ack": ack, "status": "waiting", "error": "boundary acknowledgement is required before stop"},
                RC_WAITING,
            )

        control = store.control()
        if control is not None:
            if not _record_matches_request(control, request):
                return _emit({"status": "conflict", "error": "control identity does not match the active request"}, RC_CONFLICT)
            if control.get("action") != "stop":
                return _emit({"status": "conflict", "error": "another lifecycle operation is active"}, RC_CONFLICT)
        if control is None:
            store.save_control(_control_for(request, "stop"))
        next_ack = _base_ack(request, "stop_requested", boundary_snapshot=ack.get("boundary_snapshot"))
        store.save_ack(next_ack)
        return _emit({"request": request, "ack": next_ack, "control": store.control()}, RC_OK)


def command_claim_stop(store: LifecycleStore, args: argparse.Namespace) -> int:
    """Atomically cross the irreversible stop fence before Unity.Quit.

    ``stop`` publishes intent and leaves cancellation possible.  The bridge
    calls this command only after the shared overlay is ready and immediately
    before invoking the game's destructive teardown.  Once ``stopping`` is
    durable, cancellation and expiry cannot make the bridge pretend that a
    Quit'ed game was restored.
    """

    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing"}, RC_INVALID)
        status = ack.get("status")
        control = store.control()
        if status == STOPPING_STATUS:
            if control is None or not _record_matches_request(control, request) or control.get("action") != "stop":
                return _emit({"status": "conflict", "error": "stopping claim lacks its matching stop control"}, RC_CONFLICT)
            return _emit({"request": request, "ack": ack, "control": control}, RC_OK)
        if status == "stopped":
            return _emit({"request": request, "ack": ack, "resource": store.resource()}, RC_OK)
        if status in {"cancelled", "failed", "timeout", "unsupported", "resumed"}:
            return _emit({"request": request, "ack": ack}, RC_CONFLICT)
        if status != "stop_requested":
            return _emit(
                {"request": request, "ack": ack, "status": "waiting", "error": "stop request acknowledgement is required before claim"},
                RC_WAITING,
            )
        if _deadline_expired(request):
            next_ack = _base_ack(request, "timeout", reason="stop claim deadline expired")
            store.save_ack(next_ack)
            if control and _record_matches_request(control, request):
                store.control_path.unlink(missing_ok=True)
            return _emit({"request": request, "ack": next_ack}, RC_EXPIRED)
        if control is None:
            return _emit({"status": "conflict", "error": "matching stop control is missing"}, RC_CONFLICT)
        if not _record_matches_request(control, request) or control.get("action") != "stop":
            return _emit({"status": "conflict", "error": "stop control identity/action does not match the active request"}, RC_CONFLICT)
        next_ack = _base_ack(
            request,
            STOPPING_STATUS,
            boundary_snapshot=ack.get("boundary_snapshot"),
            stop_claimed_at=_utc_now(),
        )
        store.save_ack(next_ack)
        return _emit({"request": request, "ack": next_ack, "control": control}, RC_OK)


def command_cancel(store: LifecycleStore, args: argparse.Namespace) -> int:
    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing"}, RC_INVALID)
        if ack.get("status") == "cancelled":
            # Idempotent retry: report the existing acknowledgement without
            # rewriting the durable cancel control.
            return _emit({"request": request, "ack": ack, "control": store.control()}, RC_OK)
        if ack.get("status") in TERMINAL_STATUSES or ack.get("status") == STOPPING_STATUS:
            return _emit({"request": request, "ack": ack}, RC_CONFLICT)
        resource = store.resource()
        if resource:
            if not _record_matches_request(resource, request):
                return _emit({"status": "conflict", "error": "resource identity does not match the active request"}, RC_CONFLICT)
            if _resource_is_irreversible(resource):
                return _emit({"status": "conflict", "error": "irreversible game stop cannot be cancelled"}, RC_CONFLICT)
            if resource.get("status") == "stopped":
                return _emit({"status": "conflict", "error": "resource already stopped"}, RC_CONFLICT)
        control = store.control()
        if control is not None and not _record_matches_request(control, request):
            return _emit({"status": "conflict", "error": "control identity does not match the active request"}, RC_CONFLICT)
        store.save_control(_control_for(request, "cancel"))
        next_ack = _base_ack(request, "cancelled", reason="explicit lifecycle cancellation")
        store.save_ack(next_ack)
        return _emit({"request": request, "ack": next_ack, "control": store.control()}, RC_OK)


def command_finish(store: LifecycleStore, args: argparse.Namespace) -> int:
    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing"}, RC_INVALID)
        resource = store.resource()
        if ack.get("status") == "stopped":
            if not _record_matches_request(resource, request) or resource.get("status") != "stopped":
                return _emit({"status": "conflict", "error": "stopped acknowledgement lacks a matching stopped resource"}, RC_CONFLICT)
            return _emit({"request": request, "ack": ack, "resource": resource}, RC_OK)
        if ack.get("status") in TERMINAL_STATUSES:
            return _emit({"request": request, "ack": ack, "resource": resource}, RC_CONFLICT)
        if ack.get("status") != STOPPING_STATUS:
            return _emit({"status": "waiting", "error": "stopping claim is required before finish"}, RC_WAITING)
        if not _record_matches_request(resource, request):
            return _emit({"status": "waiting", "error": "matching resource acknowledgement is missing"}, RC_WAITING)
        if resource.get("status") != "stopped":
            failed = _base_ack(request, "failed", reason=f"resource status={resource.get('status')}")
            store.save_ack(failed)
            return _emit({"request": request, "ack": failed, "resource": resource}, RC_CONFLICT)
        stopped = _base_ack(request, "stopped", resource=resource)
        store.save_ack(stopped)
        store.control_path.unlink(missing_ok=True)
        return _emit({"request": request, "ack": stopped, "resource": resource}, RC_OK)


def command_resume_complete(store: LifecycleStore, args: argparse.Namespace) -> int:
    """Commit an in-process resume only after the game reports it restored."""

    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing"}, RC_INVALID)
        if ack.get("status") == "resumed":
            return _emit({"request": request, "ack": ack, "resource": store.resource()}, RC_OK)
        if ack.get("status") != "resume_requested":
            return _emit({"request": request, "ack": ack, "status": "conflict"}, RC_CONFLICT)
        resource = store.resource()
        if not _record_matches_request(resource, request) or resource.get("status") != "resumed":
            return _emit({"status": "waiting", "error": "matching resumed resource acknowledgement is missing"}, RC_WAITING)
        resumed = _base_ack(request, "resumed", resource=resource)
        store.save_ack(resumed)
        store.control_path.unlink(missing_ok=True)
        return _emit({"request": request, "ack": resumed, "resource": resource}, RC_OK)


def command_restore(store: LifecycleStore, args: argparse.Namespace) -> int:
    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing"}, RC_INVALID)
        if ack.get("status") not in {"cancelled", "stopped", "failed"}:
            return _emit({"status": "conflict", "error": "request is not restorable"}, RC_CONFLICT)
        resource = store.resource()
        if ack.get("status") == "stopped":
            # A stopped acknowledgement with no matching stopped resource is
            # either a hand-written ack or a half-finished handover; restoring
            # the game on top of it could resurrect a Quit'ed bridge.
            if (
                resource is None
                or not _record_matches_request(resource, request)
                or resource.get("status") != "stopped"
                or _resource_is_irreversible(resource)
            ):
                return _emit({"status": "conflict", "error": "stopped acknowledgement lacks a matching reversible stopped resource"}, RC_CONFLICT)
        elif resource is not None:
            if not _record_matches_request(resource, request):
                return _emit({"status": "conflict", "error": "resource identity does not match the active request"}, RC_CONFLICT)
            if _resource_is_irreversible(resource):
                return _emit({"status": "conflict", "error": "irreversible game stop cannot be restored"}, RC_CONFLICT)
        existing_control = store.control()
        if existing_control is not None and not _record_matches_request(existing_control, request):
            return _emit({"status": "conflict", "error": "control identity does not match the active request"}, RC_CONFLICT)
        store.save_control(_control_for(request, "resume"))
        next_ack = _base_ack(request, "resume_requested")
        store.save_ack(next_ack)
        return _emit({"request": request, "ack": next_ack, "control": store.control()}, RC_OK)


def command_status(store: LifecycleStore, _args: argparse.Namespace) -> int:
    with store.lock():
        return _emit(
            {
                "schema": SCHEMA_VERSION,
                "request": store.request(),
                "ack": store.ack(),
                "control": store.control(),
                "resource": store.resource(),
            },
            RC_OK,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="soren root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    request = sub.add_parser("request")
    request.add_argument("--request-id", required=True)
    request.add_argument("--game", required=True)
    request.add_argument("--generation", type=int)
    request.add_argument("--deadline-sec", type=float, default=900.0)

    for name in ("boundary", "stop", "claim-stop", "cancel", "finish", "restore", "resume-complete"):
        item = sub.add_parser(name)
        item.add_argument("--request-id", required=True)
    sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = LifecycleStore(Path(args.root))
    try:
        if args.command == "request":
            return command_request(store, args)
        if args.command == "boundary":
            return command_boundary(store, args)
        if args.command == "stop":
            return command_stop(store, args)
        if args.command == "claim-stop":
            return command_claim_stop(store, args)
        if args.command == "cancel":
            return command_cancel(store, args)
        if args.command == "finish":
            return command_finish(store, args)
        if args.command == "restore":
            return command_restore(store, args)
        if args.command == "resume-complete":
            return command_resume_complete(store, args)
        if args.command == "status":
            return command_status(store, args)
    except LifecycleError as exc:
        return _emit({"status": "invalid", "error": str(exc)}, RC_INVALID)
    except (OSError, ValueError) as exc:
        return _emit({"status": "error", "error": str(exc)[:240]}, RC_INVALID)
    return RC_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
