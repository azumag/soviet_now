#!/usr/bin/env python3
"""Persist Twitch Creator Goal progress and emit first completion crossings."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def process(payload: dict, state: dict) -> tuple[dict, list[dict]]:
    known = state.get("goals") if isinstance(state.get("goals"), dict) else {}
    updated = dict(known)
    events: list[dict] = []

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Twitch goals response has no data array")

    for row in rows:
        if not isinstance(row, dict):
            continue
        goal_id = str(row.get("id") or "").strip()
        goal_type = str(row.get("type") or "").strip()
        try:
            current = int(row.get("current_amount"))
            target = int(row.get("target_amount"))
        except (TypeError, ValueError):
            continue
        if not goal_id or target <= 0:
            continue

        complete = current >= target
        previous = known.get(goal_id) if isinstance(known.get(goal_id), dict) else None
        # First observation is a baseline. This prevents a process restart from
        # celebrating an already-completed goal again when no state exists.
        celebrated = bool(previous.get("celebrated")) if previous is not None else complete
        if previous is not None and not celebrated and not bool(previous.get("complete")) and complete:
            events.append(
                {
                    "id": goal_id,
                    "type": goal_type,
                    "description": str(row.get("description") or ""),
                    "current_amount": current,
                    "target_amount": target,
                }
            )
            celebrated = True
        updated[goal_id] = {
            "complete": complete,
            "celebrated": celebrated,
            "current_amount": current,
            "target_amount": target,
            "type": goal_type,
            "created_at": str(row.get("created_at") or ""),
        }

    return {"version": 1, "goals": updated}, events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("response")
    parser.add_argument("state")
    args = parser.parse_args()

    response_path = Path(args.response)
    state_path = Path(args.state)
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    new_state, events = process(payload, _load(state_path))
    _write_atomic(state_path, new_state)
    for event in events:
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
