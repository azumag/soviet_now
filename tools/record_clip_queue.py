"""Consume record events through twitch_clip.sh, retaining receipts forever.

No independent worker, OAuth reader, API client, or stream control. The common
chat worker calls this bounded single-flight local adapter once per tick.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from clip_receipt import read, write


def _event(path):
    row = json.loads(path.read_text())
    if (not isinstance(row, dict) or row.get("event_kind") != "record"
            or row.get("schema") != 1
            or not re.fullmatch("[0-9a-f]{64}", row.get("event_id", ""))
            or path.name != "record_" + row["event_id"] + ".json"
            or not isinstance(row.get("event_msg"), str) or not 1 <= len(row["event_msg"]) <= 300
            or type(row.get("created_at")) not in (int, float)
            or not math.isfinite(row["created_at"]) or row["created_at"] < 0
            or row.get("delay") != 0 or row.get("game_id") != ""):
        raise ValueError("invalid record event")
    return row


def _eligible(queue, path, now):
    row = _event(path)
    receipt = queue / "receipts" / (row["event_id"] + ".json")
    saved = read(receipt)
    phase = saved.get("phase")
    destination = None
    if phase in {"ready", "expired", "disabled"}:
        destination = "done"
    elif phase in {"creating", "unknown", "rejected", "unconfirmed"}:
        # Crash/response-loss has no safe way to repeat Create.
        destination = "failed"
    elif phase == "accepted":
        # Once accepted, retry only Get Clips, for up to 10 minutes.
        if now - saved["created_at"] > 600:
            write(receipt, "unconfirmed")
            destination = "failed"
    elif now < row["created_at"] or now - row["created_at"] > 20:
        # Create Clip publishes the tail of the current live window.
        # Stale events would capture another game/moment.
        write(receipt, "expired")
        destination = "done"
    if destination:
        path.replace(queue / destination / path.name)
        return None
    if os.environ.get("TWITCH_CLIP_ENABLED", "0") != "1" or os.environ.get("EXPLORE_MODE") == "1":
        write(receipt, "disabled")
        path.replace(queue / "done" / path.name)
        return None
    if phase == "retryable" and saved.get("post_attempts", 0) >= 3:
        write(receipt, "rejected")
        path.replace(queue / "failed" / path.name)
        return None
    if phase == "retryable" and now - saved["updated_at"] < 5:
        return None
    return row, receipt, saved


def _defer(queue, path):
    print("record clip processing deferred", file=sys.stderr)
    try:
        if read(queue / "receipts" / (path.stem.removeprefix("record_") + ".json")).get("phase") == "accepted":
            return
    except Exception:
        pass
    # Malformed input/receipt cannot occupy a bounded delivery slot forever.
    if path.exists():
        path.replace(queue / "failed" / path.name)


def process(queue, *, now=None):
    fixed_now = now
    queue = Path(queue)
    queue.mkdir(parents=True, exist_ok=True)
    for name in ("done", "failed", "receipts"):
        (queue / name).mkdir(exist_ok=True)
    with (queue / ".record-lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        # Reserve one slot for accepted GETs when both classes are pending.
        # Fresh POSTs run first in deadline order; GETs rotate by last attempt.
        fresh, accepted = [], []
        for path in queue.glob("record_*.json"):
            if Path("tmp/stop").exists():
                return
            try:
                candidate = _eligible(queue, path, time.time() if fixed_now is None else fixed_now)
                if candidate is None:
                    continue
                row, _, saved = candidate
                if saved.get("phase") == "accepted":
                    accepted.append((saved["updated_at"], path))
                else:
                    fresh.append((row["created_at"], path))
            except Exception:
                _defer(queue, path)
        fresh.sort()
        accepted.sort()
        fresh_limit = 2 if accepted else 3
        # At most three sequential script attempts per tick. Cleanup and a
        # cooldown do not consume HTTP slots. Expiry is checked again at use.
        processed = 0
        fresh_processed = 0
        for is_fresh, (_, path) in [(True, item) for item in fresh] + [(False, item) for item in accepted]:
            if processed >= 3:
                break
            if is_fresh and fresh_processed >= fresh_limit:
                continue
            if (Path("tmp/stop")).exists():
                return
            try:
                candidate = _eligible(queue, path, time.time() if fixed_now is None else fixed_now)
                if candidate is None:
                    continue
                row, receipt, saved = candidate
                if saved.get("phase") == "accepted":
                    # Persist before GET, including failures/auth loss, so a
                    # restart cannot repeatedly select the same first IDs.
                    write(receipt, "accepted")
                processed += 1
                fresh_processed += int(is_fresh)
                # Per-event acceptance ID is saved by the script BEFORE polls.
                # The global POST ambiguity guard is likewise saved BEFORE HTTP.
                completed = subprocess.run(
                    ["bash", "./twitch_clip.sh", row["event_msg"], "record", str(receipt)],
                    timeout=100, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env={**os.environ, "TWITCH_CLIP_POLL_MAX": "1", "TWITCH_CLIP_POLL_INTERVAL_SEC": "0"},
                )
                phase = read(receipt).get("phase")
                if phase == "ready":
                    path.replace(queue / "done" / path.name)
                elif completed.returncode not in {75, 76}:
                    path.replace(queue / "failed" / path.name)
            except Exception:
                # Leave an accepted receipt for a GET-only next tick; a stuck
                # creating receipt fails closed next tick instead of re-POST.
                _defer(queue, path)


if __name__ == "__main__":
    process(sys.argv[1] if len(sys.argv) > 1 else "tmp/clip_queue")
