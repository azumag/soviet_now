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
PLAYER_STATE_FILE = "player_state.json"
PLAYER_CAPABILITIES_FILE = "player_capabilities.json"
JEV_ONE_GAME_FILE = "jev_one_game.json"
LOCK_FILE = "broker.lock"
HISTORY_DIR = "history"

TERMINAL_STATUSES = frozenset({
    "stopped",
    "cancelled",
    "failed",
    "timeout",
    "unsupported",
    "resumed",
    "committed",
})
STOPPING_STATUS = "stopping"
BOUNDARY_STATUSES = frozenset({"boundary", "stop_requested", "resume_requested"})
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLAYER_POLICIES = frozenset({"existing", "jev"})
PLAYER_CAPABILITY = "player_policy_v1"
PLAYER_REQUEST_FIELDS = (
    "operation",
    "target_policy",
    "run_id",
    "expected_player_generation",
    "config_hash",
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
        *(request.get(field) for field in PLAYER_REQUEST_FIELDS),
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
        self.player_state_path = self.directory / PLAYER_STATE_FILE
        self.player_capabilities_path = self.directory / PLAYER_CAPABILITIES_FILE
        self.jev_one_game_path = self.directory / JEV_ONE_GAME_FILE
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

    def player_state(self) -> dict[str, Any] | None:
        return _json_object(self.player_state_path)

    def player_capabilities(self) -> dict[str, Any] | None:
        return _json_object(self.player_capabilities_path)

    def jev_one_game(self) -> dict[str, Any] | None:
        return _json_object(self.jev_one_game_path)

    def save_request(self, value: dict[str, Any]) -> None:
        _atomic_json(self.request_path, value)

    def save_ack(self, value: dict[str, Any]) -> None:
        _atomic_json(self.ack_path, value)

    def save_control(self, value: dict[str, Any]) -> None:
        _atomic_json(self.control_path, value)

    def save_resource(self, value: dict[str, Any]) -> None:
        _atomic_json(self.resource_path, value)

    def save_player_state(self, value: dict[str, Any]) -> None:
        _atomic_json(self.player_state_path, value)

    def save_jev_one_game(self, value: dict[str, Any]) -> None:
        _atomic_json(self.jev_one_game_path, value)

    def clear_jev_one_game(self) -> None:
        self.jev_one_game_path.unlink(missing_ok=True)

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
    value = {
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
    for field in PLAYER_REQUEST_FIELDS:
        if field in request:
            value[field] = request[field]
    return value


def _record_matches_request(record: dict[str, Any] | None, request: dict[str, Any]) -> bool:
    """Require every durable record to carry the same request identity."""

    if not isinstance(record, dict) or record.get("schema") != SCHEMA_VERSION:
        return False
    for field in ("request_id", "game", "generation", "deadline_epoch", "deadline_at"):
        if field not in record or field not in request or record.get(field) != request.get(field):
            return False
    if request.get("operation") == "player_change":
        for field in PLAYER_REQUEST_FIELDS:
            if field not in record or record.get(field) != request.get(field):
                return False
    return True


def _resource_is_irreversible(resource: dict[str, Any] | None) -> bool:
    """Whether the game bridge has already crossed its no-restore fence."""

    return isinstance(resource, dict) and (
        bool(resource.get("irreversible")) or bool(resource.get("quit_called"))
    )


def _control_for(request: dict[str, Any], action: str) -> dict[str, Any]:
    value = {
        "schema": SCHEMA_VERSION,
        "action": action,
        "request_id": request["request_id"],
        "game": request.get("game"),
        "generation": request.get("generation"),
        "deadline_epoch": request.get("deadline_epoch"),
        "deadline_at": request.get("deadline_at"),
        "created_at": _utc_now(),
    }
    for field in PLAYER_REQUEST_FIELDS:
        if field in request:
            value[field] = request[field]
    return value


def _emit(value: dict[str, Any], rc: int) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return rc


def _check_request_id(args: argparse.Namespace) -> str:
    return _valid_request_id(args.request_id)


def _validate_player_change(store: LifecycleStore, args: argparse.Namespace) -> dict[str, Any] | None:
    """Validate the opt-in player transaction without changing runtime state."""

    if getattr(args, "operation", None) != "player_change":
        return None
    if args.game != "sorengame":
        raise LifecycleError("player_change is supported only for sorengame")
    if type(args.generation) is not int or args.generation < 1 or args.generation > 2**31 - 1:
        raise LifecycleError("game_generation is required for player_change")
    target_policy = str(getattr(args, "target_policy", "") or "").strip().lower()
    if target_policy not in PLAYER_POLICIES:
        raise LifecycleError("target_policy must be existing or jev")
    run_id = _valid_request_id(getattr(args, "run_id", ""))
    expected = getattr(args, "expected_player_generation", None)
    if type(expected) is not int or expected < 0 or expected > 2**31 - 1:
        raise LifecycleError("expected_player_generation is invalid")
    config_hash = str(getattr(args, "config_hash", "") or "").strip().lower()
    if not SHA256_RE.fullmatch(config_hash):
        raise LifecycleError("config_hash must be a lowercase sha256")
    capabilities = store.player_capabilities() or {}
    advertised = capabilities.get("capabilities")
    if not isinstance(advertised, list) or PLAYER_CAPABILITY not in advertised or not _live_player_capability(capabilities):
        raise LifecycleError("player_policy_v1 capability is not advertised by a live bridge")
    current = store.player_state() or {
        "schema": SCHEMA_VERSION,
        "game": "sorengame",
        "game_generation": args.generation,
        "policy": "existing",
        "player_generation": 0,
    }
    if current.get("game") not in (None, "sorengame"):
        raise LifecycleError("active player state belongs to another game")
    if current.get("game_generation") not in (None, args.generation):
        raise LifecycleError("game_generation does not match player state")
    if current.get("player_generation") != expected:
        raise LifecycleError("expected_player_generation does not match active player")
    if current.get("policy") == target_policy:
        raise LifecycleError("target player policy is already active")
    return {
        "operation": "player_change",
        "target_policy": target_policy,
        "run_id": run_id,
        "expected_player_generation": expected,
        "config_hash": config_hash,
        "capability": PLAYER_CAPABILITY,
    }


def _live_player_capability(capabilities: dict[str, Any]) -> bool:
    """Reject a capability file left by a dead bridge process."""

    pid = capabilities.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            if "soviet_local.mjs" not in proc_cmdline.read_bytes().decode(errors="replace"):
                return False
        except OSError:
            return False
    return True


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
        try:
            player_fields = _validate_player_change(store, args)
        except (LifecycleError, ValueError) as exc:
            message = str(exc)
            if "capability" in message:
                return _emit({"status": "unsupported", "error": message}, RC_INVALID)
            if any(marker in message for marker in ("expected_player_generation", "game_generation does not match", "already active")):
                return _emit({"status": "conflict", "error": message}, RC_CONFLICT)
            return _emit({"status": "invalid", "error": message}, RC_INVALID)
        if player_fields:
            requested.update(player_fields)
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


def command_commit_player(store: LifecycleStore, args: argparse.Namespace) -> int:
    """Commit a prepared player change with a CAS on player_generation."""

    request_id = _check_request_id(args)
    with store.lock():
        try:
            request, ack = _load_matching_request(store, request_id)
        except LifecycleError as exc:
            return _emit({"status": "conflict", "error": str(exc)}, RC_CONFLICT)
        if request is None or ack is None:
            return _emit({"status": "missing", "request_id": request_id}, RC_INVALID)
        if request.get("operation") != "player_change":
            return _emit({"status": "conflict", "error": "request is not a player_change"}, RC_CONFLICT)
        if ack.get("status") != "prepared":
            return _emit({"status": "waiting", "error": "player boundary is not prepared", "ack": ack}, RC_WAITING)
        if _deadline_expired(request):
            expired = _base_ack(request, "timeout", reason="player commit deadline expired")
            store.save_ack(expired)
            return _emit({"request": request, "ack": expired}, RC_EXPIRED)
        capabilities = store.player_capabilities() or {}
        advertised = capabilities.get("capabilities")
        if not isinstance(advertised, list) or PLAYER_CAPABILITY not in advertised or not _live_player_capability(capabilities):
            unsupported = _base_ack(request, "unsupported", reason="player_policy_v1 capability disappeared")
            store.save_ack(unsupported)
            return _emit({"request": request, "ack": unsupported}, RC_CONFLICT)
        current = store.player_state() or {
            "schema": SCHEMA_VERSION,
            "game": request.get("game"),
            "game_generation": request.get("generation"),
            "policy": "existing",
            "player_generation": 0,
        }
        if (
            current.get("game") != request.get("game")
            or current.get("game_generation") != request.get("generation")
            or current.get("player_generation") != request.get("expected_player_generation")
        ):
            return _emit({"status": "conflict", "error": "player CAS no longer matches active state"}, RC_CONFLICT)
        next_generation = int(current["player_generation"]) + 1
        player_state = {
            "schema": SCHEMA_VERSION,
            "game": request["game"],
            "game_generation": request["generation"],
            "policy": request["target_policy"],
            "player_generation": next_generation,
            "run_id": request["run_id"],
            "config_hash": request["config_hash"],
            "source_request_id": request["request_id"],
            "updated_at": _utc_now(),
        }
        store.save_player_state(player_state)
        # A completed JEV one-game park belongs to the previous committed
        # policy. Clear it only after the new player snapshot is durable. If
        # this process dies before the clear, the supervisor still verifies
        # the marker against player_state before suppressing a respawn.
        store.clear_jev_one_game()
        committed = _base_ack(
            request,
            "committed",
            player_generation=next_generation,
            player_state=player_state,
        )
        store.save_ack(committed)
        store.archive_current()
        store.request_path.unlink(missing_ok=True)
        store.ack_path.unlink(missing_ok=True)
        store.control_path.unlink(missing_ok=True)
        return _emit({"status": "committed", "player_state": player_state, "ack": committed}, RC_OK)


def command_mark_jev_one_game(store: LifecycleStore, _args: argparse.Namespace) -> int:
    """Durably park the supervisor after the explicitly requested JEV game."""

    with store.lock():
        # A pending player transaction owns the boundary. The loop must let
        # game_lifecycle_after_game() prepare that request instead of marking
        # a completed park that could race the operator's finish operation.
        if store.request() is not None or store.ack() is not None:
            return _emit({"status": "conflict", "error": "player transaction is still active"}, RC_CONFLICT)
        player_state = store.player_state() or {}
        run_id = player_state.get("run_id")
        if (
            player_state.get("schema") != SCHEMA_VERSION
            or player_state.get("game") != "sorengame"
            or player_state.get("policy") != "jev"
            or not isinstance(run_id, str)
            or not UUID_RE.fullmatch(run_id)
            or type(player_state.get("game_generation")) is not int
            or player_state.get("game_generation") < 1
            or type(player_state.get("player_generation")) is not int
            or player_state.get("player_generation") < 0
        ):
            return _emit({"status": "conflict", "error": "committed JEV player state is invalid"}, RC_CONFLICT)
        snapshot = _read_game_snapshot(store.root)
        if snapshot.get("state") != "GAMEOVER" or snapshot.get("runner_alive"):
            return _emit({"status": "waiting", "error": "JEV game has not reached a stable boundary"}, RC_WAITING)
        marker = {
            "schema": SCHEMA_VERSION,
            "game": "sorengame",
            "policy": "jev",
            "run_id": run_id,
            "game_generation": player_state["game_generation"],
            "player_generation": player_state["player_generation"],
            "completed_at": _utc_now(),
        }
        store.save_jev_one_game(marker)
        return _emit({"status": "parked", "marker": marker}, RC_OK)


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
        if request.get("operation") == "player_change" and ack.get("status") == "prepared":
            return _emit({"request": request, "ack": ack}, RC_OK)
        if _deadline_expired(request):
            next_ack = _base_ack(request, "timeout", reason="boundary deadline expired", snapshot=_read_game_snapshot(store.root))
            store.save_ack(next_ack)
            control = store.control()
            if control and _record_matches_request(control, request):
                store.control_path.unlink(missing_ok=True)
            return _emit({"request": request, "ack": next_ack}, RC_EXPIRED)

        snapshot = _read_game_snapshot(store.root)
        # STOP is a temporary state during founding animations, not a safe
        # handover boundary, even if the previous runner has already exited.
        if snapshot.get("state") != "GAMEOVER" or snapshot.get("runner_alive"):
            next_ack = _base_ack(
                request,
                "waiting",
                reason="game is still running",
                snapshot=snapshot,
            )
            store.save_ack(next_ack)
            return _emit({"request": request, "ack": next_ack}, RC_WAITING)

        if request.get("operation") == "player_change":
            next_ack = _base_ack(
                request,
                "prepared",
                boundary_snapshot=snapshot,
                player_change_ready=True,
            )
            store.save_ack(next_ack)
            return _emit({"request": request, "ack": next_ack}, RC_OK)

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
        # Once the writer-side stop request and matching control are already
        # durable, allow the bridge to cross the irreversible claim fence even
        # if the wall-clock deadline elapsed between subprocess calls.  A
        # later stop request still expires normally; this only prevents a
        # half-committed stop from being downgraded into a false restore path.
        stop_control_claimed = (
            status == "stop_requested"
            and control is not None
            and _record_matches_request(control, request)
            and control.get("action") == "stop"
        )
        if _deadline_expired(request) and not stop_control_claimed:
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
        ack_status = ack.get("status")
        if ack_status in TERMINAL_STATUSES - {"timeout"} or ack_status == STOPPING_STATUS:
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
                "player_state": store.player_state(),
                "player_capabilities": store.player_capabilities(),
                "jev_one_game": store.jev_one_game(),
            },
            RC_OK,
        )


def command_fresh_start(store: LifecycleStore, args: argparse.Namespace) -> int:
    """Clear a completed stop and only the pause markers owned by its request."""
    request_id = _check_request_id(args)
    with store.lock():
        receipt_path = store.root / "tmp/state/game_lifecycle_fresh_start.json"
        prior = _json_object(receipt_path)
        request = store.request()
        ack = store.ack()
        resource = store.resource()
        resuming = bool(
            prior
            and prior.get("request_id") == request_id
            and prior.get("status") in {"starting", "started"}
        )
        if resuming:
            # The durable receipt is the authority for a retried cleanup.  A
            # request_id can be reused by a later generation, so using the
            # current request as the identity would let a stale retry erase
            # that newer generation.
            identity = prior
            for record in (request, ack, store.control(), resource):
                if record is not None and not _record_matches_request(record, identity):
                    return _emit({"status": "conflict", "error": "fresh-start residue identity mismatch"}, RC_CONFLICT)
            request = request or identity
        if request is None or request.get("request_id") != request_id:
            return _emit({"status": "conflict", "error": "request is not stopped"}, RC_CONFLICT)
        ack_status = ack.get("status") if ack is not None else None
        if not resuming and ack is not None and (
            not _record_matches_request(ack, request)
            or ack_status not in {"stopped", "cancelled"}
        ):
            return _emit({"status": "conflict", "error": "request acknowledgement is not stopped or cancelled"}, RC_CONFLICT)
        if not resuming and ack_status == "cancelled":
            if resource is not None and (
                not _record_matches_request(resource, request)
                or resource.get("status") != "cancelled"
                or _resource_is_irreversible(resource)
            ):
                return _emit({"status": "conflict", "error": "matching reversible cancelled resource is required"}, RC_CONFLICT)
        elif not resuming and (not _record_matches_request(resource, request) or resource.get("status") != "stopped"):
            return _emit({"status": "conflict", "error": "matching stopped resource is missing"}, RC_CONFLICT)
        pause_specs = (
            (store.directory / "improvement_pause.json", store.root / "tmp/state/improve_daemon.paused"),
            (store.directory / "prediction_pause.json", store.root / "tmp/state/prediction_worker.paused"),
            (store.directory / "loop_pause.json", store.root / "tmp/state/soren_loop.paused"),
            (store.directory / "watchdog_pause.json", store.root / "tmp/state/soviet_watchdog.paused"),
        )
        for record_path, marker_path in pause_specs:
            record = _json_object(record_path)
            if record and record.get("request_id") == request_id:
                owned = bool(record.get("improvement_marker_created") or record.get("loop_marker_created"))
                marker_value = ""
                try:
                    marker_value = marker_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                if owned and marker_value == f"lifecycle:{request_id}":
                    marker_path.unlink(missing_ok=True)
        if not resuming:
            prior = {
                "schema": 1,
                "request_id": request_id,
                "game": request.get("game"),
                "generation": request.get("generation"),
                "deadline_epoch": request.get("deadline_epoch"),
                "deadline_at": request.get("deadline_at"),
                "status": "starting",
                "started_at": time.time(),
            }
            _atomic_json(receipt_path, prior)
        store.archive_current()
        for path in (
            store.ack_path, store.control_path, store.resource_path,
            *(record for record, _marker in pause_specs),
            # Delete request last.  The receipt remains a durable journal if
            # any earlier unlink fails, and a retry validates and resumes all
            # remaining cleanup before reporting success.
            store.request_path,
        ):
            path.unlink(missing_ok=True)
        return _emit(prior, RC_OK)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="soren root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    request = sub.add_parser("request")
    request.add_argument("--request-id", required=True)
    request.add_argument("--game", required=True)
    request.add_argument("--generation", type=int)
    request.add_argument("--deadline-sec", type=float, default=900.0)
    request.add_argument("--operation", choices=("player_change",))
    request.add_argument("--target-policy")
    request.add_argument("--run-id")
    request.add_argument("--expected-player-generation", type=int)
    request.add_argument("--config-hash")

    for name in ("boundary", "stop", "claim-stop", "cancel", "finish", "restore", "resume-complete", "fresh-start"):
        item = sub.add_parser(name)
        item.add_argument("--request-id", required=True)
    commit = sub.add_parser("commit-player")
    commit.add_argument("--request-id", required=True)
    sub.add_parser("mark-jev-one-game")
    sub.add_parser("capabilities")
    sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = LifecycleStore(Path(args.root))
    try:
        if args.command == "request":
            return command_request(store, args)
        if args.command == "commit-player":
            return command_commit_player(store, args)
        if args.command == "mark-jev-one-game":
            return command_mark_jev_one_game(store, args)
        if args.command == "capabilities":
            with store.lock():
                return _emit(store.player_capabilities() or {"capabilities": []}, RC_OK)
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
        if args.command == "fresh-start":
            return command_fresh_start(store, args)
    except LifecycleError as exc:
        return _emit({"status": "invalid", "error": str(exc)}, RC_INVALID)
    except (OSError, ValueError) as exc:
        return _emit({"status": "error", "error": str(exc)[:240]}, RC_INVALID)
    return RC_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
