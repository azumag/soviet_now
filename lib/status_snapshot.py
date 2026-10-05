#!/usr/bin/env python3
"""Emit bounded shell assignments for show_status's common game snapshot."""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extract_decide_hash import compute_hash  # noqa: E402


def _load_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_snapshot(
    game_state_path: Path,
    strategy_path: Path,
    accumulated_path: Path,
    rejected_path: Path | None = None,
    rejected_meta_path: Path | None = None,
    stagnation_path: Path | None = None,
    rejected_ttl_sec: int = 21600,
    improve_state_path: Path | None = None,
    improve_monitor_path: Path | None = None,
    *,
    now: int | None = None,
):
    result = {
        "game_state": "",
        "game_score": 0,
        "game_pieces": 0,
        "current_hash_for_acc": "",
        "acc_count": 0,
        "acc_scores": "",
        "acc_russia_count": 0,
        "acc_soviet": "false",
        "acc_max_type": 0,
        "rejected_count": 0,
        "stagnation_count": 0,
        "regression_streak": 0,
        "stagnation_event": "none",
        "stagnation_age": "n/a",
        "imp_status": "idle",
        "imp_pid": 0,
        "imp_hash": "",
        "imp_phase": "",
        "imp_progress": 0,
        "imp_updated_at": 0,
        "imp_monitor_status": "",
        "imp_monitor_action": "",
        "imp_monitor_stale_sec": 0,
    }

    game = _load_json(game_state_path)
    if game:
        result["game_state"] = str(game.get("state", "?"))
        try:
            result["game_score"] = int(game.get("score", 0) or 0)
        except (TypeError, ValueError):
            result["game_score"] = 0
        pieces = game.get("pieces", [])
        result["game_pieces"] = len(pieces) if isinstance(pieces, list) else 0

    try:
        current_hash = compute_hash(strategy_path)
    except (OSError, UnicodeError, ValueError, TypeError):
        current_hash = ""
    result["current_hash_for_acc"] = current_hash

    accumulated = _load_json(accumulated_path)
    if accumulated and current_hash and str(accumulated.get("hash", "") or "") == current_hash:
        try:
            result["acc_count"] = int(accumulated.get("count", 0) or 0)
        except (TypeError, ValueError):
            result["acc_count"] = 0
        result["acc_scores"] = str(accumulated.get("scores", "") or "")
        try:
            result["acc_russia_count"] = int(accumulated.get("russia_count", 0) or 0)
        except (TypeError, ValueError):
            result["acc_russia_count"] = 0
        result["acc_soviet"] = "true" if accumulated.get("soviet", False) else "false"
        try:
            result["acc_max_type"] = int(accumulated.get("best_max_type", 0) or 0)
        except (TypeError, ValueError):
            result["acc_max_type"] = 0

    now = int(time.time()) if now is None else int(now)

    if rejected_path is not None and rejected_meta_path is not None:
        try:
            hashes = [
                line.strip()
                for line in rejected_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, UnicodeError):
            hashes = []
        meta = _load_json(rejected_meta_path)
        active = 0
        for hash_ in hashes:
            entry = meta.get(hash_)
            if not isinstance(entry, dict):
                continue
            try:
                updated_at = int(entry.get("updated_at", 0) or 0)
            except (TypeError, ValueError):
                updated_at = 0
            if updated_at <= 0:
                continue
            if rejected_ttl_sec > 0 and now - updated_at >= rejected_ttl_sec:
                continue
            active += 1
        result["rejected_count"] = active

    if stagnation_path is not None:
        stagnation = _load_json(stagnation_path)
        try:
            result["stagnation_count"] = int(
                stagnation.get("consecutive_no_improve", 0) or 0
            )
        except (TypeError, ValueError):
            result["stagnation_count"] = 0
        try:
            result["regression_streak"] = int(
                stagnation.get("regression_streak", 0) or 0
            )
        except (TypeError, ValueError):
            result["regression_streak"] = 0
        result["stagnation_event"] = str(
            stagnation.get("last_event", "unknown") or "unknown"
        )
        try:
            updated = int(stagnation.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            updated = 0
        if updated > 0:
            diff = max(0, now - updated)
            if diff < 60:
                result["stagnation_age"] = f"{diff}s"
            elif diff < 3600:
                result["stagnation_age"] = f"{diff // 60}m"
            else:
                result["stagnation_age"] = f"{diff // 3600}h"

    if improve_state_path is not None:
        improve = _load_json(improve_state_path)
        result["imp_status"] = str(improve.get("status", "idle") or "idle")
        try:
            result["imp_pid"] = int(improve.get("pid", 0) or 0)
        except (TypeError, ValueError):
            result["imp_pid"] = 0
        result["imp_hash"] = str(improve.get("strategy_hash_before", "") or "")
        result["imp_phase"] = str(improve.get("phase", "") or "")
        try:
            result["imp_progress"] = int(improve.get("progress", 0) or 0)
        except (TypeError, ValueError):
            result["imp_progress"] = 0
        try:
            result["imp_updated_at"] = int(improve.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            result["imp_updated_at"] = 0

    if improve_monitor_path is not None:
        monitor = _load_json(improve_monitor_path)
        result["imp_monitor_status"] = str(monitor.get("status", "") or "")
        result["imp_monitor_action"] = str(monitor.get("action", "") or "")
        try:
            result["imp_monitor_stale_sec"] = int(
                monitor.get("stale_sec", 0) or 0
            )
        except (TypeError, ValueError):
            result["imp_monitor_stale_sec"] = 0

    return result


def render_shell(snapshot):
    order = (
        "game_state",
        "game_score",
        "game_pieces",
        "current_hash_for_acc",
        "acc_count",
        "acc_scores",
        "acc_russia_count",
        "acc_soviet",
        "acc_max_type",
        "rejected_count",
        "stagnation_count",
        "regression_streak",
        "stagnation_event",
        "stagnation_age",
        "imp_status",
        "imp_pid",
        "imp_hash",
        "imp_phase",
        "imp_progress",
        "imp_updated_at",
        "imp_monitor_status",
        "imp_monitor_action",
        "imp_monitor_stale_sec",
    )
    return "\n".join(f"{key}={shlex.quote(str(snapshot[key]))}" for key in order)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) not in (3, 7, 9):
        return 64
    if len(argv) == 3:
        snapshot = build_snapshot(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    else:
        try:
            ttl = int(argv[6])
        except (TypeError, ValueError):
            ttl = 21600
        snapshot = build_snapshot(
            Path(argv[0]),
            Path(argv[1]),
            Path(argv[2]),
            Path(argv[3]),
            Path(argv[4]),
            Path(argv[5]),
            ttl,
            Path(argv[7]) if len(argv) == 9 else None,
            Path(argv[8]) if len(argv) == 9 else None,
        )
    print(render_shell(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
