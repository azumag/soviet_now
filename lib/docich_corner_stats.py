"""Read-only bridge from docich corner state to the Soren status dashboard.

The regular Soren dashboard reads ``score_history.txt``.  That file belongs to
the Soren game and must not be reused while docich has switched the stream to
another corner: doing so would make a previous game's score look current.

This module only reads the small, allowlisted public state files written by
docich.  It deliberately returns a typed-ish snapshot for the renderer rather
than exposing arbitrary JSON fields or file contents.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Mapping


DEFAULT_DOCICH_STATE_DIR = Path("/home/ubuntu/docich/run-soren-live")
ACTIVE_STATUSES = frozenset(
    {"preparing", "starting", "active", "restoring", "recovery_required"}
)

_CORNER_SPECS = (
    ("retro", "RETRO", ("retro_corner.json", "retro_corner_manual.json")),
    ("paper", "PAPER", ("paper_corner.json", "paper_corner_manual.json")),
    ("nethack", "NETHACK", ("nethack_corner.json", "nethack_corner_manual.json")),
    ("soren91", "SOREN91", ("soren91_corner.json", "soren91_corner_manual.json")),
    ("jev", "JEV", ("jev_corner.json",)),
)
_GAME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def resolve_state_dir(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the docich state directory, with an environment override for tests."""
    if value is not None:
        return Path(value).expanduser()
    configured = os.getenv("DOCICH_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    # The production checkouts conventionally sit beside each other.  Prefer
    # that relationship when it exists, while retaining the VM default for
    # the normal /home/ubuntu/soren checkout.
    sibling = Path.cwd().parent / "docich" / "run-soren-live"
    if sibling.is_dir():
        return sibling
    return DEFAULT_DOCICH_STATE_DIR


def _read_json(path: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "unreadable"
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None, "invalid-json"
    if not isinstance(value, dict):
        return None, "invalid-shape"
    return value, None


def _parse_timestamp(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None and math.isfinite(number):
        return number
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _safe_game(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    game = value.strip()
    return game if _GAME_NAME_RE.fullmatch(game) else None


def _active_states(root: Path) -> list[dict[str, object]]:
    active: list[dict[str, object]] = []
    for kind, label, names in _CORNER_SPECS:
        for name in names:
            path = root / name
            if not path.is_file():
                continue
            state, _error = _read_json(path)
            if state is None:
                # A malformed terminal/idle file cannot prove that a corner
                # is running.  Do not let it replace the normal dashboard.
                continue
            status = state.get("status")
            if status not in ACTIVE_STATUSES:
                continue
            if state.get("schema_version") != 1:
                active.append(
                    {
                        "kind": "invalid",
                        "label": "CORNER",
                        "state_path": str(path),
                        "error": "schema",
                    }
                )
                continue
            active.append(
                {
                    "kind": kind,
                    "label": label,
                    "state": state,
                    "state_path": str(path),
                    "status": str(status),
                    "source": name,
                }
            )
    return active


def _score_history(root: Path, state: Mapping[str, object]) -> list[dict[str, object]]:
    game = _safe_game(state.get("game"))
    if game is None:
        return []
    started_at = _parse_timestamp(state.get("started_at"))
    path = root / "scores" / f"{game}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    history: list[dict[str, object]] = []
    for order, line in enumerate(lines):
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict) or item.get("game") != game:
            continue
        score = _int_value(item.get("score"))
        if score is None:
            continue
        timestamp = _parse_timestamp(item.get("ts"))
        # When a corner start boundary is available, an unparseable timestamp
        # is not safe to treat as a current score.  This is the important
        # guard against leaking a previous game's history into the corner.
        if started_at is not None and (timestamp is None or timestamp < started_at):
            continue
        history.append({"score": score, "ts": timestamp, "order": order})
    history.sort(key=lambda item: (item.get("ts") is not None, item.get("ts") or 0, item["order"]))
    return history[-100:]


def _strategy_ranking(root: Path, game: str) -> list[dict[str, object]]:
    """Read the bounded per-game strategy ranking from docich's improve log."""
    path = root / "resolver" / "improve_log.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    aggregate: dict[str, dict[str, object]] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("game") != game:
            continue
        trials = entry.get("trials")
        if not isinstance(trials, list):
            continue
        for trial in trials:
            if not isinstance(trial, Mapping):
                continue
            strategy = trial.get("strategy")
            mean = trial.get("mean_score")
            if not isinstance(strategy, Mapping) or isinstance(mean, bool) or not isinstance(mean, (int, float)):
                continue
            if not math.isfinite(float(mean)):
                continue
            try:
                payload = json.dumps(
                    dict(strategy),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError):
                continue
            key = hashlib.sha256(payload).hexdigest()
            row = aggregate.setdefault(key, {"key": key, "best": float(mean), "runs": 0, "wins": 0})
            row["runs"] = int(row["runs"]) + 1
            row["best"] = max(float(row["best"]), float(mean))
        best_key = entry.get("best_strategy_key")
        if isinstance(best_key, str) and best_key in aggregate and entry.get("promoted"):
            aggregate[best_key]["wins"] = int(aggregate[best_key]["wins"]) + 1
    return sorted(
        aggregate.values(),
        key=lambda row: (float(row["best"]), int(row["wins"]), int(row["runs"])),
        reverse=True,
    )[:5]


def _paper_snapshot(root: Path) -> dict[str, object]:
    value, _error = _read_json(root / "trading" / "status.json")
    if value is None:
        return {"status": "unavailable", "fills": [], "positions": {}}
    raw_fills = value.get("recent_fills")
    fills = [dict(fill) for fill in raw_fills if isinstance(fill, Mapping)] if isinstance(raw_fills, list) else []
    fills.sort(key=lambda item: _parse_timestamp(item.get("filled_at")) or 0)
    positions = value.get("open_positions")
    if not isinstance(positions, Mapping):
        positions = {}
    return {
        "status": str(value.get("worker_state") or "unknown"),
        "capital": value.get("capital_reference"),
        "deployed": value.get("deployed_reference"),
        "fills": fills[-20:],
        "positions": dict(positions),
        "snapshot_at": _parse_timestamp(value.get("snapshot_generated_at")),
        "market_count": _int_value(value.get("market_count")),
    }


def _nethack_snapshot(root: Path, state: Mapping[str, object]) -> dict[str, object]:
    current, _error = _read_json(root / "nethack" / "current.json")
    run = dict(current) if current is not None else {}
    for source, target in (
        ("run_id", "run_id"),
        ("expedition", "expedition"),
        ("run_status", "status"),
        ("run_score", "score"),
        ("run_turns", "turns"),
        ("run_max_depth", "max_depth"),
    ):
        if target not in run and source in state:
            run[target] = state[source]

    history: list[dict[str, object]] = []
    runs_dir = root / "nethack" / "runs"
    try:
        paths = list(runs_dir.glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        item, _error = _read_json(path)
        if item is None or _int_value(item.get("score")) is None:
            continue
        try:
            item["_mtime"] = path.stat().st_mtime
        except OSError:
            item["_mtime"] = 0
        history.append(item)
    history.sort(key=lambda item: _parse_timestamp(item.get("finished_at")) or float(item.get("_mtime") or 0))
    scores = [_int_value(item.get("score")) for item in history]
    current_score = _int_value(run.get("score"))
    if current_score is not None:
        scores.append(current_score)
    return {"run": run, "history": history[-20:], "scores": [s for s in scores if s is not None]}


def load_active_corner(state_dir: str | os.PathLike[str] | None = None) -> dict[str, object] | None:
    """Return one active corner snapshot, or ``None`` for ordinary Soren mode.

    Multiple active corner states are represented as a conflict snapshot so
    the renderer can fail closed instead of choosing one arbitrarily.
    """
    root = resolve_state_dir(state_dir)
    entries = _active_states(root)
    if not entries:
        return None
    if len(entries) != 1:
        return {
            "kind": "conflict",
            "label": "CORNER",
            "entries": entries,
        }
    entry = entries[0]
    if entry.get("kind") == "invalid":
        return entry
    state = entry["state"]
    snapshot = dict(entry)
    kind = str(entry["kind"])
    if kind == "retro":
        snapshot["game"] = _safe_game(state.get("game")) or "unknown"
        snapshot["scores"] = _score_history(root, state)
        snapshot["target_matches"] = _int_value(state.get("target_matches"))
        snapshot["strategy_ranking"] = _strategy_ranking(root, snapshot["game"])
    elif kind == "paper":
        snapshot["paper"] = _paper_snapshot(root)
    elif kind == "nethack":
        snapshot.update(_nethack_snapshot(root, state))
    elif kind == "soren91":
        snapshot["game"] = _safe_game(state.get("game")) or "soren91"
    elif kind == "jev":
        snapshot["game"] = _safe_game(state.get("game")) or "sorengame"
        snapshot["policy"] = state.get("policy")
        snapshot["player_generation"] = _int_value(state.get("player_generation"))
    return snapshot
