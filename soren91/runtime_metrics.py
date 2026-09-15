#!/usr/bin/env python3
"""Bounded, sanitized Soren91 runtime performance metrics.

Reads the existing soren91.log as a stream and emits aggregate numeric metrics
only. Raw log text, player names, comments, prompts and environment values are
never copied to the output.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import statistics
import tempfile
import time
from collections import Counter, deque
from pathlib import Path

TURN_RE = re.compile(r"\[game\] Turn \d+: .*?reason=([A-Za-z0-9_-]+)")
DECISION_RE = re.compile(r"\[game\] Decision: .*?reason=(.*)$")
SUMMARY_RE = re.compile(r"\[game\] Summary: turns=(\d+), rank=([^,\s]+)")
MAX_LOG_LINE_BYTES = 64 * 1024


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = round((len(ordered) - 1) * q)
    return int(ordered[max(0, min(idx, len(ordered) - 1))])


class MetricsState:
    """In-memory bounded aggregate. Timestamps are receipt times, not log text."""

    def __init__(self, started_at: float | None = None) -> None:
        self.started_at = float(started_at if started_at is not None else time.time())
        self.decisions = 0
        self.hold_decisions = 0
        self.temporal_next_observations = 0
        self.hold_empty_observations = 0
        self.observations = 0
        self.reasons: Counter[str] = Counter()
        self.games_completed = 0
        self.early_games = 0
        self.rank_samples: deque[int] = deque(maxlen=128)
        self.turn_samples: deque[int] = deque(maxlen=128)
        self.intervals_ms: deque[int] = deque(maxlen=256)
        self.last_decision_at: float | None = None
        self.last_rank: int | None = None
        self.last_turns: int | None = None

    def record(self, line: str, now: float | None = None) -> bool:
        """Consume one raw line; returns True only when aggregate state changed."""
        now = float(now if now is not None else time.time())

        turn = TURN_RE.search(line)
        if turn:
            raw_reason = turn.group(1)
            self.observations += 1
            if "temporal-next" in raw_reason:
                self.temporal_next_observations += 1
            if "hold-empty" in raw_reason:
                self.hold_empty_observations += 1
            if raw_reason.startswith("stable-slow-advance"):
                reason = "stable-slow-advance"
            elif raw_reason.startswith("stable"):
                reason = "stable"
            elif raw_reason in {"confirm-frame", "preview-changed", "board-moving", "unknown-current", "uncalibrated", "invalid-board"}:
                reason = raw_reason
            else:
                reason = "other"
            self.reasons[reason] += 1
            return True

        decision = DECISION_RE.search(line)
        if decision:
            reason_text = decision.group(1)
            self.decisions += 1
            if self.last_decision_at is not None:
                delta = max(0, min(int(round((now - self.last_decision_at) * 1000)), 120_000))
                self.intervals_ms.append(delta)
            self.last_decision_at = now
            if "[HOLD]" in line or reason_text.startswith("HOLD:"):
                self.hold_decisions += 1
            return True

        summary = SUMMARY_RE.search(line)
        if summary:
            turns = max(0, min(int(summary.group(1)), 100_000))
            rank_raw = summary.group(2)
            rank = int(rank_raw) if rank_raw.isdigit() else None
            if rank is not None and not 1 <= rank <= 91:
                rank = None
            self.games_completed += 1
            self.last_turns = turns
            self.turn_samples.append(turns)
            if turns < 20:
                self.early_games += 1
            if rank is not None:
                self.last_rank = rank
                self.rank_samples.append(rank)
            self.last_decision_at = None
            return True

        return False

    def payload(self, generated_at: float | None = None) -> dict:
        generated_at = float(generated_at if generated_at is not None else time.time())
        intervals = list(self.intervals_ms)
        ranks = list(self.rank_samples)
        turns = list(self.turn_samples)
        return {
            "schema": 1,
            "generated_at": int(generated_at),
            "started_at": int(self.started_at),
            "decisions": self.decisions,
            "hold_decisions": self.hold_decisions,
            "temporal_next_observations": self.temporal_next_observations,
            "hold_empty_observations": self.hold_empty_observations,
            "observations": self.observations,
            "observation_reasons": {
                key: int(self.reasons.get(key, 0))
                for key in (
                    "stable",
                    "stable-slow-advance",
                    "confirm-frame",
                    "preview-changed",
                    "board-moving",
                    "unknown-current",
                    "uncalibrated",
                    "invalid-board",
                    "other",
                )
            },
            "decision_interval_ms": {
                "samples": len(intervals),
                "mean": int(round(statistics.fmean(intervals))) if intervals else None,
                "p50": _percentile(intervals, 0.50),
                "p90": _percentile(intervals, 0.90),
                "max": max(intervals) if intervals else None,
                "last": intervals[-1] if intervals else None,
            },
            "games_completed": self.games_completed,
            "early_games": self.early_games,
            "rank_samples": len(ranks),
            "rank_median": int(round(statistics.median(ranks))) if ranks else None,
            "last_rank": self.last_rank,
            "turn_samples": len(turns),
            "turns_mean": int(round(statistics.fmean(turns))) if turns else None,
            "last_turns": self.last_turns,
        }


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def acquire_single_instance(output_path: Path):
    """Hold a non-blocking advisory lock next to the aggregate output."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_path.with_name(output_path.name + ".lock")
    handle = lock_path.open("a+", encoding="ascii")
    os.chmod(lock_path, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def follow(log_path: Path, output_path: Path, poll_sec: float = 0.10) -> bool:
    instance_lock = acquire_single_instance(output_path)
    if instance_lock is None:
        return False

    state = MetricsState()
    atomic_write(output_path, state.payload())
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    handle = None
    inode = None
    try:
        while not stopping:
            if handle is None:
                try:
                    handle = log_path.open("r", encoding="utf-8", errors="replace")
                    stat = os.fstat(handle.fileno())
                    inode = (stat.st_dev, stat.st_ino)
                    handle.seek(0, os.SEEK_END)
                except FileNotFoundError:
                    time.sleep(poll_sec)
                    continue

            line = handle.readline(MAX_LOG_LINE_BYTES)
            if line:
                if state.record(line):
                    atomic_write(output_path, state.payload())
                continue

            try:
                stat = log_path.stat()
                current_inode = (stat.st_dev, stat.st_ino)
                if current_inode != inode or stat.st_size < handle.tell():
                    handle.close()
                    handle = None
                    inode = None
                    continue
            except FileNotFoundError:
                handle.close()
                handle = None
                inode = None
            time.sleep(poll_sec)
    finally:
        if handle is not None:
            handle.close()
        atomic_write(output_path, state.payload())
        instance_lock.close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--follow", action="store_true", help="follow the log from its current end")
    parser.add_argument("--log", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.follow:
        parser.error("--follow is required")
    # A duplicate collector is benign: it exits without touching the output.
    follow(Path(args.log), Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
