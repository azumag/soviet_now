"""Runner-owned evidence for a narrowly scoped, live-runner STOP handover.

This never changes the shared terminal predicate or sends game input. A legacy
marker or old board cannot attest to celebration completion or a live observation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
import uuid
from pathlib import Path

QUIET_SECONDS = 300
FRESH_SECONDS = 3
OBSERVATION = Path("tmp/state/game_observation.json")
WITNESS = Path("tmp/state/founding_boundary.json")
RUNNER = Path("tmp/state/main_strategy_runner_active.json")
MARKER = Path("tmp/markers/.soviet_created")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _projection(state):
    return {key: state.get(key) for key in ("state", "score", "makeSorenCount", "pieces")}


def _board_key(board):
    if not isinstance(board, dict):
        return None
    try:
        return json.dumps(board, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None


def _process_birth(pid):
    try:
        if Path("/proc").is_dir():
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return [Path("/proc/sys/kernel/random/boot_id").read_text().strip(), stat[19]]
        # Offline macOS regression support; Linux production uses start ticks.
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                capture_output=True, text=True, timeout=1, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def _candidate(root, state, *, epoch):
    count = state.get("makeSorenCount")
    if state.get("state") != "STOP" or not _number(count) or count <= 0 or int(count) != count:
        return None
    try:
        marker = (root / MARKER).stat()
        state_stat = (root / "game_state.json").stat()
        game_count = int((root / "game_count.txt").read_text().strip())
        if marker.st_mtime > epoch or state_stat.st_mtime > epoch or game_count < 0:
            return None
    except (OSError, ValueError):
        return None
    runner = _read(root / RUNNER) or {}
    if (type(runner.get("pid")) is not int or runner["pid"] <= 0
            or type(runner.get("game")) is not int or runner["game"] != game_count + 1
            or not _number(runner.get("started_at")) or runner["started_at"] > marker.st_mtime):
        return None
    birth = _process_birth(runner["pid"])
    if birth is None:
        return None
    observation = _read(root / OBSERVATION) or {}
    observed = observation.get("observed_epoch")
    board_key = _board_key(_projection(state))
    if (type(observation.get("schema")) is not int or observation.get("schema") != 1 or not _uuid(observation.get("game_id"))
            or not _uuid(observation.get("stop_id")) or not _number(observed)
            or not 0 <= epoch - observed <= FRESH_SECONDS
            or board_key is None
            or _board_key(observation.get("board")) != board_key
            or _board_key(_projection(_read(root / "game_state.json") or {})) != board_key):
        return None
    return {
        "runner": {key: runner[key] for key in ("pid", "game", "started_at")},
        "game_count": game_count, "process_birth": birth,
        "game_id": observation["game_id"], "stop_id": observation["stop_id"],
        "marker": [marker.st_ino, marker.st_mtime_ns, marker.st_size],
        "board": board_key,
    }


class FoundingBoundaryWitness:
    """Reset on a new runner, new marker/game/board, MOVE or an observation gap."""
    def __init__(self, root=Path(".")):
        self.root = Path(root)
        self.record = None
        self.written_mono = None
        self._clear()

    def _clear(self):
        self.record = None
        self.written_mono = None
        try:
            (self.root / WITNESS).unlink(missing_ok=True)
        except OSError:
            pass

    def observe(self, state, founding_seen):
        epoch, mono = time.time(), time.monotonic()
        identity = _candidate(self.root, state, epoch=epoch) if founding_seen else None
        if identity is None or identity["runner"]["pid"] != os.getpid():
            self._clear()
            return
        previous = self.record
        if (previous is None or previous["identity"] != identity
                or not 0 <= mono - previous["observed_mono"] <= FRESH_SECONDS
                or abs((epoch - previous["observed_epoch"]) - (mono - previous["observed_mono"])) > 1):
            previous = {"schema": 1, "identity": identity, "started_mono": mono, "started_epoch": epoch}
            self.written_mono = None
        record = {**previous, "observed_mono": mono, "observed_epoch": epoch}
        self.record = record
        if self.written_mono is not None and 0 <= mono - self.written_mono < 1:
            return
        target = self.root / WITNESS
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(f".{os.getpid()}.tmp")
        try:
            with temp.open("w") as stream:
                json.dump(record, stream, allow_nan=False)
            os.replace(temp, target)
            self.written_mono = mono
        except (OSError, ValueError):
            self._clear()
        finally:
            temp.unlink(missing_ok=True)


def _valid_founding_record(root, state):
    """Require fresh identity-matching observations and a full monotonic 300s."""
    root = Path(root)
    epoch, mono = time.time(), time.monotonic()
    identity = _candidate(root, state, epoch=epoch)
    record = _read(root / WITNESS) or {}
    if (identity is None or type(record.get("schema")) is not int
            or record.get("schema") != 1 or record.get("identity") != identity):
        return None
    times = [record.get(key) for key in ("started_mono", "started_epoch", "observed_mono", "observed_epoch")]
    if not all(_number(value) for value in times):
        return None
    start_mono, start_epoch, seen_mono, seen_epoch = times
    elapsed = seen_mono - start_mono
    valid = (0 <= mono - seen_mono <= FRESH_SECONDS
            and 0 <= epoch - seen_epoch <= FRESH_SECONDS
            and elapsed >= QUIET_SECONDS
            and abs((seen_epoch - start_epoch) - elapsed) <= 1
            and abs((epoch - seen_epoch) - (mono - seen_mono)) <= 1)
    return record if valid else None


def has_founding_boundary(root, state):
    return _valid_founding_record(root, state) is not None


def founding_boundary_token(root, state):
    record = _valid_founding_record(root, state)
    if record is None:
        return None
    return hashlib.sha256(json.dumps(record["identity"], sort_keys=True, allow_nan=False).encode()).hexdigest()
