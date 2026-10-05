#!/usr/bin/env python3
"""Emit bounded shell assignments for show_status's common game snapshot."""

from __future__ import annotations

import json
import shlex
import sys
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


def build_snapshot(game_state_path: Path, strategy_path: Path, accumulated_path: Path):
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
    )
    return "\n".join(f"{key}={shlex.quote(str(snapshot[key]))}" for key in order)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 3:
        return 64
    snapshot = build_snapshot(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    print(render_shell(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
