#!/usr/bin/env python3
"""Monitor actual deadline-crossing placements with independent geometry.

This monitor intentionally does not call analyze_board. It watches consecutive
history snapshots, detects the piece that was actually added by a drop, and only
then checks whether that new piece's measured top is over the deadline. When it
is, the monitor independently estimates whether a lower landing or approximate
same-type merge opportunity existed on the previous board.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORY = ROOT / "game_history" / "latest.jsonl"
DEFAULT_LOG = ROOT / "logs" / "deadline_misplacement_monitor.jsonl"
DEFAULT_HEARTBEAT = ROOT / "tmp" / "state" / "deadline_monitor_heartbeat.json"
DEFAULT_PID = ROOT / "tmp" / "state" / "deadline_monitor.pid"

DEADLINE_Y = 3.38
FLOOR_Y = -5.0
DROP_X_MIN = -3.0
DROP_X_MAX = 3.0
LOWER_EPS = 0.05
MERGE_GAP_EPS = 0.35

TYPE_RADII = {
    1: 0.207,
    2: 0.259,
    3: 0.316,
    4: 0.380,
    5: 0.414,
    6: 0.470,
    7: 0.559,
    8: 0.660,
    9: 0.746,
    10: 0.846,
    11: 0.982,
    12: 1.068,
    13: 1.207,
    14: 1.385,
    15: 1.600,
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _radius(piece: dict[str, Any], fallback_type: int = 0) -> float:
    r = _num(piece.get("r"), 0.0)
    if r > 0:
        return r
    ptype = int(piece.get("type", fallback_type) or fallback_type or 0)
    return TYPE_RADII.get(ptype, 0.5)


def _piece_top(piece: dict[str, Any]) -> float:
    return _num(piece.get("y")) + _radius(piece)


def _pieces(record: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = record.get("state_snapshot", {})
    pieces = snapshot.get("pieces", []) if isinstance(snapshot, dict) else []
    return pieces if isinstance(pieces, list) else []


def _new_piece(before: list[dict[str, Any]], after: list[dict[str, Any]], decision_x: float, next_type: int) -> dict[str, Any] | None:
    before_ids = {p.get("id") for p in before}
    added = [p for p in after if p.get("id") not in before_ids]
    if not added:
        return None
    typed = [p for p in added if int(p.get("type", 0) or 0) == next_type]
    pool = typed or added
    return min(
        pool,
        key=lambda p: (
            abs(_num(p.get("x")) - decision_x),
            -_piece_top(p),
        ),
    )


def _landing_at_x(pieces: list[dict[str, Any]], x: float, r: float) -> dict[str, Any]:
    landing_y = FLOOR_Y + r
    support_id = None
    for p in pieces:
        pr = _radius(p)
        dx = abs(x - _num(p.get("x")))
        contact = r + pr
        if dx >= contact:
            continue
        y = _num(p.get("y")) + math.sqrt(max(contact * contact - dx * dx, 0.0))
        if y > landing_y:
            landing_y = y
            support_id = p.get("id")
    return {
        "x": round(x, 3),
        "landing_y": round(landing_y, 3),
        "top_y": round(landing_y + r, 3),
        "support_id": support_id,
    }


def _candidate_xs(decision_x: float, pieces: list[dict[str, Any]]) -> list[float]:
    xs = {round(DROP_X_MIN + i * 0.05, 3) for i in range(int((DROP_X_MAX - DROP_X_MIN) / 0.05) + 1)}
    xs.add(round(max(DROP_X_MIN, min(DROP_X_MAX, decision_x)), 3))
    for p in pieces:
        px = max(DROP_X_MIN, min(DROP_X_MAX, _num(p.get("x"))))
        xs.add(round(px, 3))
        xs.add(round(max(DROP_X_MIN, min(DROP_X_MAX, px - 0.2)), 3))
        xs.add(round(max(DROP_X_MIN, min(DROP_X_MAX, px + 0.2)), 3))
    return sorted(xs)


def _merge_alternative(pieces: list[dict[str, Any]], next_type: int, r: float, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    same = [p for p in pieces if int(p.get("type", 0) or 0) == next_type]
    if not same:
        return None
    best = None
    best_key = None
    for c in candidates:
        cx = _num(c.get("x"))
        cy = _num(c.get("landing_y"))
        for p in same:
            pr = _radius(p)
            dist = math.hypot(cx - _num(p.get("x")), cy - _num(p.get("y")))
            gap = dist - (r + pr)
            if gap > MERGE_GAP_EPS:
                continue
            key = (abs(gap), _num(c.get("top_y")), abs(cx))
            if best_key is None or key < best_key:
                best_key = key
                best = {
                    **c,
                    "target_id": p.get("id"),
                    "target_x": round(_num(p.get("x")), 3),
                    "target_y": round(_num(p.get("y")), 3),
                    "contact_gap": round(gap, 3),
                }
    return best


def _summary_piece(piece: dict[str, Any] | None) -> dict[str, Any] | None:
    if not piece:
        return None
    return {
        "id": piece.get("id"),
        "type": piece.get("type"),
        "x": round(_num(piece.get("x")), 3),
        "y": round(_num(piece.get("y")), 3),
        "r": round(_radius(piece), 3),
        "top_y": round(_piece_top(piece), 3),
    }


def evaluate_transition(prev: dict[str, Any], curr: dict[str, Any]) -> dict[str, Any] | None:
    before = _pieces(prev)
    after = _pieces(curr)
    if not before or not after:
        return None

    decision_x = _num(prev.get("decision_x"))
    next_type = int(prev.get("next_type", 0) or 0)
    placed = _new_piece(before, after, decision_x, next_type)
    if not placed:
        return None
    actual_top = _piece_top(placed)
    if actual_top <= DEADLINE_Y:
        return None

    r = _radius(placed, next_type)
    candidates = [_landing_at_x(before, x, r) for x in _candidate_xs(decision_x, before)]
    chosen_estimate = _landing_at_x(before, decision_x, r)
    lower = [c for c in candidates if _num(c.get("top_y")) + LOWER_EPS < actual_top]
    best_lower = min(lower, key=lambda c: (_num(c.get("top_y")), abs(_num(c.get("x")))), default=None)
    best_merge = _merge_alternative(before, next_type, r, candidates)
    merge_improves_top = bool(
        best_merge and _num(best_merge.get("top_y")) + LOWER_EPS < actual_top
    )
    inappropriate = bool(best_lower or merge_improves_top)
    return {
        "kind": "deadline_misplacement_check",
        "status": "inappropriate" if inappropriate else "appropriate",
        "trigger": "actual_new_piece_top_over_deadline",
        "turn": prev.get("turn"),
        "next_turn": curr.get("turn"),
        "score": prev.get("score"),
        "piece_count": prev.get("piece_count"),
        "decision_x": round(decision_x, 3),
        "decision_reason": prev.get("decision_reason", ""),
        "history_decision_crosses_deadline": bool(prev.get("decision_crosses_deadline", False)),
        "actual_new_piece": _summary_piece(placed),
        "independent_chosen_estimate": chosen_estimate,
        "has_merge_alternative": bool(best_merge),
        "merge_alternative_improves_top": merge_improves_top,
        "has_lower_alternative": bool(best_lower),
        "best_merge_alternative": best_merge,
        "best_lower_alternative": best_lower,
        "candidate_count": len(candidates),
        "lower_candidate_count": len(lower),
        "deadline_y": DEADLINE_Y,
    }


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": int(time.time()), **event}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_heartbeat(path: Path, *, status: str, history: Path, log_path: Path, extra: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": int(time.time()),
        "pid": os.getpid(),
        "status": status,
        "history": str(history),
        "log": str(log_path),
        "detector": "actual_snapshot_geometry",
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process_records(records: list[dict[str, Any]], log_path: Path, prev: dict[str, Any] | None = None) -> tuple[int, int, dict[str, Any] | None]:
    checked = 0
    written = 0
    last = prev
    for record in records:
        if last is not None:
            event = evaluate_transition(last, record)
            if event:
                checked += 1
                append_event(log_path, event)
                written += 1
        last = record
    return checked, written, last


def _parse_lines(lines: list[str]) -> list[dict[str, Any]]:
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def run_once(history: Path, log_path: Path, tail_lines: int = 300) -> tuple[int, int]:
    if not history.exists():
        return 0, 0
    lines = history.read_text(encoding="utf-8", errors="ignore").splitlines()
    if tail_lines > 0:
        lines = lines[-tail_lines:]
    checked, written, _last = process_records(_parse_lines(lines), log_path)
    return checked, written


def run_daemon(history: Path, log_path: Path, heartbeat: Path, poll_sec: float, bootstrap_lines: int) -> None:
    DEFAULT_PID.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_PID.write_text(f"{os.getpid()}\n", encoding="utf-8")
    offset = 0
    inode = None
    last_record = None
    try:
        if history.exists():
            lines = history.read_text(encoding="utf-8", errors="ignore").splitlines()
            if bootstrap_lines > 0:
                lines = lines[-bootstrap_lines:]
            checked, written, last_record = process_records(_parse_lines(lines), log_path)
            offset = history.stat().st_size
            inode = history.stat().st_ino
        else:
            checked = written = 0
        write_heartbeat(heartbeat, status="running", history=history, log_path=log_path, extra={"bootstrap_checked": checked, "bootstrap_written": written})
        while True:
            if (ROOT / "tmp" / "stop").exists():
                break
            if not history.exists():
                write_heartbeat(heartbeat, status="waiting_history", history=history, log_path=log_path)
                time.sleep(poll_sec)
                continue
            stat = history.stat()
            if inode != stat.st_ino or stat.st_size < offset:
                inode = stat.st_ino
                offset = 0
                last_record = None
            with history.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
            if chunk:
                checked, written, last_record = process_records(_parse_lines(chunk.splitlines()), log_path, last_record)
                write_heartbeat(heartbeat, status="running", history=history, log_path=log_path, extra={"last_checked": checked, "last_written": written})
            else:
                write_heartbeat(heartbeat, status="running", history=history, log_path=log_path)
            time.sleep(poll_sec)
    finally:
        try:
            if DEFAULT_PID.read_text(encoding="utf-8").strip() == str(os.getpid()):
                DEFAULT_PID.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--heartbeat", type=Path, default=DEFAULT_HEARTBEAT)
    parser.add_argument("--poll-sec", type=float, default=float(os.environ.get("DEADLINE_MONITOR_POLL_SEC", "2")))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--tail-lines", type=int, default=int(os.environ.get("DEADLINE_MONITOR_BOOTSTRAP_LINES", "300")))
    args = parser.parse_args()
    if args.once:
        checked, written = run_once(args.history, args.log, args.tail_lines)
        write_heartbeat(args.heartbeat, status="once", history=args.history, log_path=args.log, extra={"checked": checked, "written": written})
        print(json.dumps({"checked": checked, "written": written, "log": str(args.log)}, ensure_ascii=False))
        return 0
    run_daemon(args.history, args.log, args.heartbeat, args.poll_sec, args.tail_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
